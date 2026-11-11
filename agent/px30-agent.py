#!/usr/bin/env python3
"""
px30-agent - Home Assistant integration for the PX30 wall panel.

Publishes the panel as one Home Assistant device via MQTT Discovery (no custom
HA component needed) and executes a fixed whitelist of actions on request.

Design rules (see docs/ha-integratie.md):
  * MQTT payloads are never passed to a shell or used as command arguments.
    Every action is a constant argv from COMMANDS; payloads are only parsed
    into ON/OFF, a bounded number, a known select option or PRESS.
  * Destructive actions (rescue, reinstall, factory reset, module stack
    switch) only run while the "arm" switch is on; it switches itself off
    after arm_timeout_s and after every destructive action.
  * Runs as the unprivileged user px30-agent; privileged commands go through
    `sudo -n` with an exact-argument sudoers whitelist (30-px30-agent).
  * The backlight is owned by proximity-wake. The agent writes the display
    settings to /data/px30-agent/display.json and signals proximity-wake
    (HUP = reload, USR1 = wake, USR2 = sleep). Only when proximity-wake is not
    running does the agent write the backlight itself.

Files:
  /data/px30-agent/config.json    broker + options (written by the setup page)
  /data/px30-agent/settings.json  agent-owned persistent state (ssh wish)
  /data/px30-agent/display.json   display/proximity settings (read by proximity-wake)
  /data/px30-agent/module.json    radio module stack + firmware tag (written by px30-update)
  /run/px30-display/state.json    live display state (written by proximity-wake, planned)
  /etc/px30-release               image version (VERSION=...), part of the image

Usage:
  px30-agent.py                    run (needs python3-paho-mqtt)
  px30-agent.py --dry-run          print discovery messages + one state snapshot
  px30-agent.py --dry-run --simulate brightness=40 --simulate arm=ON --simulate factory_reset=PRESS
"""
import argparse
import glob
import json
import logging
import math
import os
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

AGENT_VERSION = "0.1.0"

DATA_DIR = "/data/px30-agent"
CONFIG_PATH = DATA_DIR + "/config.json"
SETTINGS_PATH = DATA_DIR + "/settings.json"
DISPLAY_PATH = DATA_DIR + "/display.json"
MODULE_PATH = "/data/radio/state.json"        # written by px30-radio
DISPLAY_STATE_PATH = "/run/px30-display/state.json"
RELEASE_FILE = "/etc/px30-release"
OTBR_REST = "http://127.0.0.1:8081"

log = logging.getLogger("px30-agent")

DEFAULT_CONFIG = {
    "mqtt": {
        "host": "",
        "port": 1883,
        "username": "",
        "password": "",
        "tls": False,
        "ca_file": "",          # empty = system CA store
        "tls_insecure": False,  # do not verify the broker certificate (testing only)
        "keepalive": 60,
    },
    "discovery_prefix": "homeassistant",
    "base_topic": "px30",
    "device_id": "",            # empty = derived from hostname + machine-id
    "device_name": "",          # empty = "PX30 <hostname>"
    "wifi_interface": "wlan0",
    "publish_interval_s": 30,
    "arm_timeout_s": 60,
    "update": {
        "repo": "OWNER/px30-wallpanel-releases",   # placeholder, see README
        "check_interval_h": 6,
        "include_prereleases": False,
        "firmware_tag_prefix": "v",
        "module_tag_prefix": "module-",
        "github_token": "",
    },
}

DEFAULT_DISPLAY = {
    "proximity": True,        # proximity sensor used (False: only touch wakes the screen)
    "light_sensor": True,     # light sensor used (False: no lux, no auto-brightness)
    "brightness": 80,         # % of max_brightness; manual level, or maximum when auto
    "auto_brightness": False,
    "brightness_min": 10,     # % lower bound for auto-brightness
    "screen_timeout": 60,     # s screen on after last presence/touch
    "near_delta": 60,         # proximity raw above baseline = near
    "far_delta": 30,          # hysteresis: back to far below this
}

# number entities: key in display.json -> limits + discovery fields
NUMBERS = {
    "brightness": dict(name="Brightness", min=1, max=100, step=1, unit="%",
                       icon="mdi:brightness-6", category=None, mode="slider"),
    "brightness_min": dict(name="Minimum brightness (auto)", min=1, max=100, step=1, unit="%",
                           icon="mdi:brightness-4", category="config", mode="slider"),
    "screen_timeout": dict(name="Screen off after", min=5, max=3600, step=5, unit="s",
                           icon="mdi:timer-outline", category="config", mode="box",
                           device_class="duration"),
    "near_delta": dict(name="Proximity threshold", min=5, max=1000, step=1, unit=None,
                       icon="mdi:signal-distance-variant", category="config", mode="box"),
    "far_delta": dict(name="Proximity release threshold", min=1, max=999, step=1, unit=None,
                      icon="mdi:signal-distance-variant", category="config", mode="box"),
}

MODULE_STACKS = ("thread", "zigbee")
MODULE_OPTIONS = MODULE_STACKS + ("off",)     # off: radio unused, no service
MODULE_CHANNELS = ("stable", "latest")

# The ONLY commands this agent can run with privileges. Keys are internal
# names; values are complete argv lists (no payload ever ends up in here).
# Must match agent/30-px30-agent.sudoers exactly.
CTL = "/usr/local/sbin/px30-ctl"
SYSTEMCTL = "/usr/bin/systemctl"
UPDATE = "/usr/local/sbin/px30-update"
RADIO = "/usr/local/sbin/px30-radio"
PANEL = "/usr/local/sbin/px30-panel"
COMMANDS = {
    "status":          [CTL, "status"],
    "rescue":          [CTL, "rescue"],
    "reinstall":       [CTL, "reinstall"],
    "factory_reset":   [CTL, "factory-reset"],
    "reboot":          [SYSTEMCTL, "reboot"],
    "ssh_on":          [SYSTEMCTL, "enable", "--now", "ssh.service"],
    "ssh_off":         [SYSTEMCTL, "disable", "--now", "ssh.service"],
    # px30-panel kills cog properly (a getty restart leaves the old cog running)
    "kiosk_restart":   [PANEL, "dashboard"],
    # on-screen: setup via phone (QR; also re-login to HA) / rescue menu
    "panel_setup":     [PANEL, "setup"],
    "panel_menu":      [PANEL, "menu"],
    "display_reload":  [SYSTEMCTL, "kill", "-s", "HUP", "proximity-wake.service"],
    "display_wake":    [SYSTEMCTL, "kill", "-s", "USR1", "proximity-wake.service"],
    "display_sleep":   [SYSTEMCTL, "kill", "-s", "USR2", "proximity-wake.service"],
    "fw_install":      [UPDATE, "system", "latest"],
    # radio module: stack + channel; px30-radio shows the warnings, the agent
    # only runs it while "Unlock dangerous actions" is on
    **{f"radio_{st}_{ch}": [RADIO, "switch", st, ch, "--yes"]
       for st in MODULE_STACKS for ch in MODULE_CHANNELS},
    # without flashing: radio off, or back on with the stack the chip has
    "radio_use_off":    [RADIO, "use", "off"],
    **{f"radio_use_{st}": [RADIO, "use", st] for st in MODULE_STACKS},
    "rcp_version":     ["/usr/sbin/ot-ctl", "rcp", "version"],
}
# read-only, run without sudo
QUERIES = {
    "ssh_active":   [SYSTEMCTL, "is-active", "ssh.service"],
    "prox_active":  [SYSTEMCTL, "is-active", "proximity-wake.service"],
    "otbr_active":  [SYSTEMCTL, "is-active", "otbr-agent.service"],
}
DESTRUCTIVE = {"rescue", "reinstall", "factory_reset"}


# ---------------------------------------------------------------- helpers

def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        elif not k.startswith("_"):
            out[k] = v
    return out


def read_json(path, default=None):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else default
    except (OSError, ValueError):
        return default


def write_json_atomic(path, data, mode=0o644):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def read_text(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def read_num(path, default=None):
    v = read_text(path)
    try:
        return float(v) if v is not None else default
    except ValueError:
        return default


def read_kv_file(path):
    out = {}
    for line in (read_text(path, "") or "").splitlines():
        m = re.match(r'^([A-Z_][A-Z0-9_]*)=("?)(.*)\2$', line.strip())
        if m:
            out[m.group(1)] = m.group(3)
    return out


def version_key(tag, prefix=""):
    """'v1.2.10' -> (1, 2, 10); None when not numeric."""
    t = tag[len(prefix):] if prefix and tag.startswith(prefix) else tag
    t = t.lstrip("vV")
    parts = re.findall(r"\d+", t.split("-")[0].split("+")[0])
    return tuple(int(p) for p in parts) if parts else None


def slug(s):
    return re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_") or "px30"


# ---------------------------------------------------------------- agent

class Agent:
    def __init__(self, config, dry_run=False):
        self.cfg = config
        self.dry_run = dry_run
        self.lock = threading.RLock()
        self.work = queue.Queue()
        self.client = None
        self.connected = False
        self.stop = threading.Event()

        hostname = socket.gethostname()
        mid = read_text("/data/machine-id") or read_text("/etc/machine-id") or "000000"
        self.dev_id = slug(config["device_id"] or f"px30_{hostname}_{mid[-6:]}")
        self.dev_name = config["device_name"] or f"PX30 {hostname}"
        self.base = f'{config["base_topic"].strip("/")}/{self.dev_id}'
        self.dp = config["discovery_prefix"].strip("/")
        self.t_avail = f"{self.base}/availability"
        self.t_state = f"{self.base}/state"
        self.t_cmd = f"{self.base}/cmd"
        self.t_upd_fw = f"{self.base}/update/firmware"
        self.t_upd_mod = f"{self.base}/update/module"

        self.display = deep_merge(DEFAULT_DISPLAY, read_json(DISPLAY_PATH, {}))
        self.settings = read_json(SETTINGS_PATH, {}) or {}
        self.armed_until = 0.0
        self.last_action = "start"
        try:
            uptime = float((read_text("/proc/uptime") or "0").split()[0])
        except ValueError:
            uptime = 0.0
        self.boot_epoch = time.time() - uptime
        self.boot_status = {}
        self.rcp_version = None
        self.fw_latest = None      # dict(tag, title, url, summary)
        self.mod_latest = None
        self.fw_busy = False
        self.mod_busy = False
        self.last_state_json = None

    # ------------------------------------------------------- commands
    def run_priv(self, key, timeout=60):
        argv = list(COMMANDS[key])
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            argv = ["/usr/bin/sudo", "-n"] + argv
        if self.dry_run:
            print("DRY-RUN exec:", " ".join(argv))
            return 0, ""
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.error("%s failed: %s", key, e)
            return 255, str(e)
        if p.returncode:
            log.warning("%s -> rc=%s %s", key, p.returncode, (p.stderr or p.stdout).strip()[:300])
        return p.returncode, p.stdout

    def query(self, key):
        try:
            p = subprocess.run(QUERIES[key], capture_output=True, text=True, timeout=10)
            return p.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return None

    def set_last(self, text):
        self.last_action = f'{time.strftime("%Y-%m-%d %H:%M:%S")} {text}'[:250]
        log.info("action: %s", text)

    # ------------------------------------------------------- state
    @property
    def armed(self):
        return time.monotonic() < self.armed_until

    def backlight(self):
        bl = sorted(glob.glob("/sys/class/backlight/*"))
        return bl[0] if bl else None

    def iio(self, attr):
        for d in glob.glob("/sys/bus/iio/devices/iio:device*"):
            if os.path.exists(f"{d}/{attr}_raw"):
                raw = read_num(f"{d}/{attr}_raw")
                scale = read_num(f"{d}/{attr}_scale", 1.0)
                return None if raw is None else raw * scale
        return None

    def refresh_slow(self):
        """Things that only change on reboot or after a module operation."""
        rc, out = self.run_priv("status", timeout=15) if not self.dry_run else (1, "")
        st = {}
        if rc == 0:
            for line in out.splitlines():
                if line.startswith("slot="):
                    st.update(kv.split("=", 1) for kv in line.split() if "=" in kv)
        self.boot_status = st
        if not self.dry_run and self.query("otbr_active") == "active":
            rc, out = self.run_priv("rcp_version", timeout=15)
            if rc == 0:
                lines = [l for l in out.splitlines() if l.strip() and l.strip() != "Done"]
                self.rcp_version = lines[0].strip()[:100] if lines else None

    def module_info(self):
        """(stack on the chip, version, radio enabled)"""
        m = read_json(MODULE_PATH, {}) or {}
        stack = m.get("stack") if m.get("stack") in MODULE_STACKS else "thread"
        version = m.get("version") or self.rcp_version
        return stack, version, m.get("enabled") is not False

    def module_channel(self):
        ch = self.settings.get("module_channel")
        return ch if ch in MODULE_CHANNELS else "stable"

    def ip_address(self, iface):
        try:
            out = subprocess.run(["/usr/sbin/ip", "-j", "-4", "addr", "show", "dev", iface],
                                 capture_output=True, text=True, timeout=5).stdout
            for link in json.loads(out or "[]"):
                for a in link.get("addr_info", []):
                    if a.get("family") == "inet":
                        return a.get("local")
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        return None

    def wifi_rssi(self, iface):
        for line in (read_text("/proc/net/wireless", "") or "").splitlines():
            if line.strip().startswith(iface + ":"):
                try:
                    return float(line.split()[3].rstrip("."))
                except (IndexError, ValueError):
                    return None
        return None

    def cpu_temp(self):
        temps = []
        for z in glob.glob("/sys/class/thermal/thermal_zone*"):
            t = read_num(f"{z}/temp")
            if t is not None:
                temps.append(t / 1000.0)
        return round(max(temps), 1) if temps else None

    def mem_available_mb(self):
        for line in (read_text("/proc/meminfo", "") or "").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
        return None

    def disk_free_gb(self, path):
        try:
            return round(shutil.disk_usage(path).free / 1e9, 2)
        except OSError:
            return None

    def thread_state(self):
        try:
            with urllib.request.urlopen(OTBR_REST + "/node/state", timeout=2) as r:
                return str(json.loads(r.read().decode() or "null"))[:40]
        except (OSError, ValueError, urllib.error.URLError):
            return None

    def collect(self):
        c = self.cfg
        rel = read_kv_file(RELEASE_FILE)
        bl = self.backlight()
        bl_cur = read_num(f"{bl}/brightness") if bl else None
        bl_max = read_num(f"{bl}/max_brightness") if bl else None
        bl_power = read_num(f"{bl}/bl_power", 0) if bl else None
        live = read_json(DISPLAY_STATE_PATH, None)
        presence = None
        if isinstance(live, dict) and isinstance(live.get("near"), bool):
            presence = "ON" if live["near"] else "OFF"
        stack, mod_ver, mod_on = self.module_info()
        with self.lock:              # the setup page may have changed the file
            self.display = deep_merge(DEFAULT_DISPLAY, read_json(DISPLAY_PATH, {}))
            prox_on, light_on = self.display["proximity"], self.display["light_sensor"]
        lux = self.iio("in_illuminance") if light_on else None
        prox = self.iio("in_proximity") if prox_on else None
        if not prox_on:
            presence = None
        s = {
            "ip": self.ip_address(c["wifi_interface"]),
            "wifi_rssi": self.wifi_rssi(c["wifi_interface"]),
            "boot_time": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(self.boot_epoch)),
            "cpu_temp": self.cpu_temp(),
            "load": round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else None,
            "mem_available": self.mem_available_mb(),
            "disk_root_free": self.disk_free_gb("/"),
            "disk_data_free": self.disk_free_gb("/data"),
            "boot_slot": self.boot_status.get("slot"),
            "boot_tries": self.boot_status.get("tries"),
            "os_version": rel.get("VERSION") or "dev",
            "kernel": os.uname().release if hasattr(os, "uname") else None,
            "agent_version": AGENT_VERSION,
            "module_stack": stack if mod_on else "off",
            "module_version": mod_ver,
            "module_channel": self.module_channel(),
            "thread_state": self.thread_state() if stack == "thread" and mod_on else None,
            "illuminance": round(lux, 1) if lux is not None else None,
            "proximity": round(prox) if prox is not None else None,
            "presence": presence,
            "screen": None if bl_cur is None else ("ON" if bl_cur > 0 and not bl_power else "OFF"),
            "backlight_raw": int(bl_cur) if bl_cur is not None else None,
            "backlight_max": int(bl_max) if bl_max is not None else None,
            "ssh": "ON" if self.query("ssh_active") == "active" else "OFF",
            "armed": "ON" if self.armed else "OFF",
            "last_action": self.last_action,
        }
        with self.lock:
            for k in NUMBERS:
                s[k] = self.display[k]
            s["auto_brightness"] = "ON" if self.display["auto_brightness"] else "OFF"
            s["proximity_on"] = "ON" if self.display["proximity"] else "OFF"
            s["light_on"] = "ON" if self.display["light_sensor"] else "OFF"
        return s

    # ------------------------------------------------------- discovery
    def device_block(self):
        rel = read_kv_file(RELEASE_FILE)
        dev = {
            "identifiers": [self.dev_id],
            "name": self.dev_name,
            "manufacturer": "Smatek",
            "model": "PX30 wall panel (Debian kiosk)",
            "sw_version": rel.get("VERSION") or "dev",
            "hw_version": "Rockchip PX30",
        }
        mac = read_text(f'/sys/class/net/{self.cfg["wifi_interface"]}/address')
        if mac:
            dev["connections"] = [["mac", mac]]
        return dev

    def entities(self):
        """(component, object_id, config) for every entity."""
        st = self.t_state
        cmd = lambda o: f"{self.t_cmd}/{o}"
        E = []

        def add(comp, obj, **kw):
            E.append((comp, obj, kw))

        def sensor(obj, name, key, category="diagnostic", **kw):
            add("sensor", obj, name=name, state_topic=st,
                value_template="{{ value_json.%s }}" % key, entity_category=category, **kw)

        # buttons
        add("button", "restart", name="Restart", command_topic=cmd("restart"),
            device_class="restart", entity_category="config")
        add("button", "kiosk_restart", name="Reload kiosk", command_topic=cmd("kiosk_restart"),
            icon="mdi:web-refresh", entity_category="config")
        add("button", "panel_setup", name="Set up with phone (QR, log in again)",
            command_topic=cmd("panel_setup"), icon="mdi:qrcode", entity_category="config")
        add("button", "panel_menu", name="Show rescue menu on screen", command_topic=cmd("panel_menu"),
            icon="mdi:menu", entity_category="config")
        add("button", "check_updates", name="Check for updates", command_topic=cmd("check_updates"),
            icon="mdi:update", entity_category="diagnostic")
        add("button", "rescue", name="Restart into rescue mode", command_topic=cmd("rescue"),
            icon="mdi:lifebuoy", entity_category="config")
        add("button", "reinstall", name="Reinstall (keep settings)",
            command_topic=cmd("reinstall"), icon="mdi:backup-restore", entity_category="config")
        add("button", "factory_reset", name="Factory reset (erase everything)",
            command_topic=cmd("factory_reset"), icon="mdi:delete-alert", entity_category="config")
        # switches
        add("switch", "arm", name="Unlock dangerous actions", command_topic=cmd("arm"),
            state_topic=st, value_template="{{ value_json.armed }}", icon="mdi:shield-alert",
            entity_category="config")
        add("switch", "ssh", name="SSH", command_topic=cmd("ssh"), state_topic=st,
            value_template="{{ value_json.ssh }}", icon="mdi:console-network", entity_category="config")
        add("switch", "auto_brightness", name="Automatic brightness",
            command_topic=cmd("auto_brightness"), state_topic=st,
            value_template="{{ value_json.auto_brightness }}", icon="mdi:brightness-auto")
        add("switch", "proximity_sensor", name="Proximity sensor", command_topic=cmd("proximity_sensor"),
            state_topic=st, value_template="{{ value_json.proximity_on }}",
            icon="mdi:motion-sensor", entity_category="config")
        add("switch", "light_sensor", name="Light sensor", command_topic=cmd("light_sensor"),
            state_topic=st, value_template="{{ value_json.light_on }}",
            icon="mdi:brightness-auto", entity_category="config")
        add("switch", "screen", name="Screen", command_topic=cmd("screen"), state_topic=st,
            value_template="{{ value_json.screen }}", icon="mdi:monitor")
        # numbers
        for key, n in NUMBERS.items():
            kw = dict(name=n["name"], command_topic=cmd(key), state_topic=st,
                      value_template="{{ value_json.%s }}" % key, min=n["min"], max=n["max"],
                      step=n["step"], mode=n["mode"], icon=n["icon"])
            if n["unit"]:
                kw["unit_of_measurement"] = n["unit"]
            if n.get("device_class"):
                kw["device_class"] = n["device_class"]
            if n["category"]:
                kw["entity_category"] = n["category"]
            add("number", key, **kw)
        # select
        # switching = network lost; changing the version = risk (px30-radio --help)
        add("select", "module_stack", name="Radio module (switching erases the network!)",
            command_topic=cmd("module_stack"),
            state_topic=st, value_template="{{ value_json.module_stack }}",
            options=list(MODULE_OPTIONS), icon="mdi:zigbee", entity_category="config")
        add("select", "module_channel", name="Radio module channel (stable/latest)",
            command_topic=cmd("module_channel"),
            state_topic=st, value_template="{{ value_json.module_channel }}",
            options=list(MODULE_CHANNELS), icon="mdi:source-branch", entity_category="config")
        # binary sensor
        add("binary_sensor", "presence", name="Presence", state_topic=st,
            value_template="{{ value_json.presence }}", device_class="occupancy")
        # sensors
        sensor("ip", "IP address", "ip", icon="mdi:ip-network")
        sensor("wifi_rssi", "Wi-Fi signal", "wifi_rssi", device_class="signal_strength",
               unit_of_measurement="dBm", state_class="measurement")
        sensor("boot_time", "Booted", "boot_time", device_class="timestamp")
        sensor("cpu_temp", "CPU temperature", "cpu_temp", device_class="temperature",
               unit_of_measurement="°C", state_class="measurement")
        sensor("load", "Load (1 min)", "load", icon="mdi:chip", state_class="measurement",
               enabled_by_default=False)
        sensor("mem_available", "Free memory", "mem_available", device_class="data_size",
               unit_of_measurement="MB", state_class="measurement")
        sensor("disk_root_free", "Free space system", "disk_root_free", device_class="data_size",
               unit_of_measurement="GB", state_class="measurement")
        sensor("disk_data_free", "Free space /data", "disk_data_free", device_class="data_size",
               unit_of_measurement="GB", state_class="measurement")
        sensor("boot_slot", "Boot slot", "boot_slot", icon="mdi:harddisk")
        sensor("os_version", "System version", "os_version", icon="mdi:information-outline")
        sensor("kernel", "Kernel", "kernel", icon="mdi:linux", enabled_by_default=False)
        sensor("agent_version", "Agent version", "agent_version", enabled_by_default=False)
        sensor("module_version", "Module firmware", "module_version", icon="mdi:chip")
        sensor("thread_state", "Thread state", "thread_state", icon="mdi:access-point-network")
        sensor("last_action", "Last action", "last_action", icon="mdi:history")
        sensor("illuminance", "Ambient light", "illuminance", category=None,
               device_class="illuminance", unit_of_measurement="lx", state_class="measurement")
        sensor("proximity", "Proximity (raw)", "proximity", state_class="measurement",
               icon="mdi:signal-distance-variant", enabled_by_default=False)
        # updates
        add("update", "firmware", name="Firmware", state_topic=self.t_upd_fw,
            command_topic=cmd("install_firmware"), payload_install="install",
            device_class="firmware", entity_category="config")
        add("update", "module_firmware", name="Radio module firmware (risk of network loss)",
            state_topic=self.t_upd_mod,
            command_topic=cmd("install_module"), payload_install="install",
            device_class="firmware", entity_category="config")

        dev = self.device_block()
        out = []
        for comp, obj, kw in E:
            conf = {
                "unique_id": f"{self.dev_id}_{obj}",
                "availability_topic": self.t_avail,
                "device": dev,
                "origin": {"name": "px30-agent", "sw_version": AGENT_VERSION},
            }
            conf.update({k: v for k, v in kw.items() if v is not None})
            if comp in ("button", "switch", "number", "select", "update"):
                conf["qos"] = 1
            out.append((f"{self.dp}/{comp}/{self.dev_id}/{obj}/config", conf))
        return out

    def update_payloads(self):
        rel = read_kv_file(RELEASE_FILE)
        fw_inst = rel.get("VERSION") or "dev"
        stack, mod_inst, _ = self.module_info()
        mod_inst = mod_inst or "onbekend"

        def payload(installed, latest, busy, title):
            p = {"installed_version": installed, "in_progress": busy, "title": title}
            if latest:
                p.update(latest_version=latest["tag"], release_url=latest["url"],
                         release_summary=latest["summary"], title=latest["title"] or title)
            else:
                p["latest_version"] = installed
            return p
        return {
            self.t_upd_fw: payload(fw_inst, self.fw_latest, self.fw_busy, "PX30 system"),
            self.t_upd_mod: payload(mod_inst, self.mod_latest, self.mod_busy,
                                    f"TYZS13 {stack}-firmware"),
        }

    # ------------------------------------------------------- update check
    def check_updates(self):
        u = self.cfg["update"]
        repo = u["repo"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or repo.startswith("OWNER/"):
            self.set_last("update check: no repository configured")
            return
        url = f"https://api.github.com/repos/{repo}/releases?per_page=30"
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": f"px30-agent/{AGENT_VERSION}"}
        if u.get("github_token"):
            headers["Authorization"] = f'Bearer {u["github_token"]}'
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
                releases = json.loads(r.read().decode())
        except (OSError, ValueError, urllib.error.URLError) as e:
            self.set_last(f"update check failed: {e}")
            return
        stack, _, mod_on = self.module_info()
        fw_p, mod_p = u["firmware_tag_prefix"], u["module_tag_prefix"]
        fw = mod = None
        for rel in releases if isinstance(releases, list) else []:
            if rel.get("draft") or (rel.get("prerelease") and not u["include_prereleases"]):
                continue
            tag = str(rel.get("tag_name", ""))
            info = {"tag": tag[:64], "title": str(rel.get("name") or tag)[:100],
                    "url": str(rel.get("html_url", ""))[:255],
                    "summary": str(rel.get("body") or "")[:255]}
            assets = [str(a.get("name", "")) for a in rel.get("assets", [])]
            if tag.startswith(mod_p):
                if any(f"-{stack}-" in a and a.endswith(".gbl") for a in assets):
                    if mod is None or (version_key(tag, mod_p) or ()) > (version_key(mod["tag"], mod_p) or ()):
                        mod = info
            elif tag.startswith(fw_p) and any(a.endswith((".img.zst", ".ext4.zst")) for a in assets):
                if fw is None or (version_key(tag, fw_p) or ()) > (version_key(fw["tag"], fw_p) or ()):
                    fw = info
        self.fw_latest, self.mod_latest = fw, mod
        self.set_last(f'update check: system {fw["tag"] if fw else "-"}, module {mod["tag"] if mod else "-"}')
        self.publish_updates()

    # ------------------------------------------------------- display
    def save_display(self):
        if not self.dry_run:
            write_json_atomic(DISPLAY_PATH, self.display)
        if self.dry_run or self.query("prox_active") == "active":
            self.run_priv("display_reload")
        else:
            self.apply_backlight_direct()

    def apply_backlight_direct(self, on=True):
        """Fallback when proximity-wake is not running."""
        bl = self.backlight()
        if not bl:
            return
        mx = read_num(f"{bl}/max_brightness", 255)
        val = int(round(mx * self.display["brightness"] / 100.0)) if on else 0
        try:
            with open(f"{bl}/brightness", "w") as f:
                f.write(str(val))
        except OSError as e:
            log.warning("backlight write failed (udev rule 61-px30-backlight installed?): %s", e)

    def set_screen(self, on):
        if self.dry_run or self.query("prox_active") == "active":
            self.run_priv("display_wake" if on else "display_sleep")
        else:
            self.apply_backlight_direct(on)

    # ------------------------------------------------------- command handling
    def handle(self, name, payload):
        """Validate one command. Returns a callable for the worker or None."""
        p = payload.strip()
        if name == "arm":
            if p not in ("ON", "OFF"):
                return None
            self.armed_until = time.monotonic() + self.cfg["arm_timeout_s"] if p == "ON" else 0.0
            self.set_last("unlocked" if p == "ON" else "locked")
            return self.publish_state
        if name in ("restart", "kiosk_restart", "check_updates", "panel_setup", "panel_menu") + tuple(DESTRUCTIVE):
            if p != "PRESS":
                return None
            if name in DESTRUCTIVE:
                if not self.armed:
                    self.set_last(f"{name} refused: turn on 'Unlock dangerous actions' first")
                    return self.publish_state
                self.armed_until = 0.0
            if name == "check_updates":
                return self.check_updates
            key = {"restart": "reboot"}.get(name, name)
            return lambda: (self.set_last(name), self.publish_state(), self.run_priv(key))
        if name == "ssh":
            if p not in ("ON", "OFF"):
                return None
            def do_ssh():
                self.settings["ssh"] = p == "ON"
                if not self.dry_run:
                    write_json_atomic(SETTINGS_PATH, self.settings, 0o600)
                self.run_priv("ssh_on" if p == "ON" else "ssh_off")
                self.set_last(f"ssh {p}")
                self.publish_state()
            return do_ssh
        if name == "screen":
            if p not in ("ON", "OFF"):
                return None
            return lambda: (self.set_screen(p == "ON"), time.sleep(0.3), self.publish_state())
        if name in ("proximity_sensor", "light_sensor"):
            if p not in ("ON", "OFF"):
                return None
            key = "proximity" if name == "proximity_sensor" else "light_sensor"
            def do_sensor():
                with self.lock:
                    self.display = deep_merge(DEFAULT_DISPLAY, read_json(DISPLAY_PATH, {}))
                    self.display[key] = p == "ON"
                self.save_display()
                self.set_last(f"{name} {p}")
                self.publish_state()
            return do_sensor
        if name == "auto_brightness":
            if p not in ("ON", "OFF"):
                return None
            def do_auto():
                with self.lock:
                    self.display["auto_brightness"] = p == "ON"
                self.save_display()
                self.publish_state()
            return do_auto
        if name in NUMBERS:
            n = NUMBERS[name]
            try:
                v = float(p)
            except ValueError:
                return None
            if not math.isfinite(v):
                return None
            v = int(round(min(max(v, n["min"]), n["max"]) / n["step"]) * n["step"])
            def do_num():
                with self.lock:
                    d = self.display
                    d[name] = v
                    if d["far_delta"] >= d["near_delta"]:      # keep hysteresis valid
                        if name == "far_delta":
                            d["near_delta"] = min(NUMBERS["near_delta"]["max"], v + 1)
                        else:
                            d["far_delta"] = max(1, d["near_delta"] // 2)
                    if d["brightness_min"] > d["brightness"]:
                        d["brightness_min"] = d["brightness"]
                self.save_display()
                self.publish_state()
            return do_num
        if name == "module_stack":
            if p not in MODULE_OPTIONS:
                return None
            stack, _, mod_on = self.module_info()
            if p == "off" or (p == stack and not mod_on):
                # no flashing: radio off, or back on with the stack on the chip
                def do_use():
                    rc, _ = self.run_priv(f"radio_use_{p}", timeout=180)
                    self.set_last(f"radio {p}: " + ("ok" if rc == 0 else f"error {rc}"))
                    self.publish_state()
                return do_use
            if p == stack:
                return self.publish_state
            if not self.armed:
                self.set_last("module switch refused: turn on 'Unlock dangerous actions' first")
                return self.publish_state
            self.armed_until = 0.0
            return lambda: self.module_flash(p, self.module_channel())
        if name == "module_channel":
            if p not in MODULE_CHANNELS:
                return None
            def do_channel():
                self.settings["module_channel"] = p
                if not self.dry_run:
                    write_json_atomic(SETTINGS_PATH, self.settings, 0o600)
                self.set_last(f"radio channel: {p} (apply with Radio module firmware > Install)")
                self.publish_state()
            return do_channel
        if name == "install_firmware":
            if p != "install":
                return None
            return self.install_firmware
        if name == "install_module":
            if p != "install":
                return None
            if not self.armed:
                self.set_last("module update refused: turn on 'Unlock dangerous actions' "
                              "first (risk of network loss)")
                return self.publish_state
            self.armed_until = 0.0
            return lambda: self.module_flash(self.module_info()[0], self.module_channel())
        return None

    def install_firmware(self):
        self.fw_busy = True
        self.publish_updates()
        rc, _ = self.run_priv("fw_install", timeout=3600)
        self.fw_busy = False
        self.set_last("firmware-update: " + {0: "ok", 2: "not implemented yet"}.get(rc, f"error {rc}"))
        self.publish_updates()
        self.publish_state()

    def module_flash(self, stack, channel):
        self.mod_busy = True
        self.publish_updates()
        self.publish_state()
        rc, _ = self.run_priv(f"radio_{stack}_{channel}", timeout=900)
        self.mod_busy = False
        self.set_last(f"module {stack}/{channel}: " + {0: "ok", 2: "refused or already installed",
                                                       3: "busy"}.get(rc, f"error {rc}"))
        self.refresh_slow()
        self.publish_updates()
        self.publish_state()

    # ------------------------------------------------------- MQTT
    def publish(self, topic, payload, retain=True, qos=1):
        data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        if self.dry_run:
            print(f"PUBLISH {topic} retain={retain}\n  {data}")
            return
        if self.client is not None and self.connected:
            self.client.publish(topic, data, qos=qos, retain=retain)

    def publish_discovery(self):
        for topic, conf in self.entities():
            self.publish(topic, conf)

    def publish_state(self, force=True):
        s = json.dumps(self.collect(), ensure_ascii=False)
        if force or s != self.last_state_json:
            self.last_state_json = s
            self.publish(self.t_state, s)

    def publish_updates(self):
        for topic, p in self.update_payloads().items():
            self.publish(topic, p)

    def publish_all(self):
        self.publish(self.t_avail, "online")
        self.publish_discovery()
        self.publish_state()
        self.publish_updates()

    def on_connect(self, client, userdata, flags, rc, *args):
        rc_val = getattr(rc, "value", rc)
        if rc_val != 0:
            log.error("MQTT connect refused: %s", rc)
            return
        log.info("MQTT connected")
        self.connected = True
        client.subscribe([(f"{self.t_cmd}/+", 1), (f"{self.dp}/status", 1)])
        self.work.put(self.publish_all)

    def on_disconnect(self, client, userdata, *args):
        self.connected = False
        log.warning("MQTT disconnected")

    def on_message(self, client, userdata, msg):
        try:
            payload = msg.payload[:64].decode("utf-8", "replace")
        except Exception:
            return
        if msg.topic == f"{self.dp}/status":
            if payload == "online":       # HA restarted: resend everything
                self.work.put(self.publish_all)
            return
        prefix = self.t_cmd + "/"
        if not msg.topic.startswith(prefix):
            return
        name = msg.topic[len(prefix):]
        if msg.retain:                     # never act on stale retained commands
            log.warning("ignoring retained command on %s", msg.topic)
            return
        job = self.handle(name, payload)
        if job is None:
            log.warning("rejected command %r on %s", payload[:20], name)
            return
        self.work.put(job)

    def worker(self):
        while not self.stop.is_set():
            try:
                job = self.work.get(timeout=1)
            except queue.Empty:
                continue
            try:
                job()
            except Exception:
                log.exception("job failed")

    def apply_persistent_settings(self):
        """/etc is replaced by a reinstall; re-apply wishes stored on /data."""
        if "ssh" in self.settings:
            want = bool(self.settings["ssh"])
            if (self.query("ssh_active") == "active") != want:
                self.run_priv("ssh_on" if want else "ssh_off")

    def run(self):
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            sys.exit("python3-paho-mqtt is not installed")
        m = self.cfg["mqtt"]
        if not m["host"]:
            # not an error: the panel works as a kiosk without HA integration
            log.warning("no MQTT broker configured (px30-agent-setup); "
                        "panel only manageable via SSH (ssh admin@<ip>)")
            return
        cid = f"px30-agent-{self.dev_id}"
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
        except AttributeError:                       # paho-mqtt 1.x
            client = mqtt.Client(client_id=cid)
        if m["username"]:
            client.username_pw_set(m["username"], m["password"] or None)
        if m["tls"]:
            client.tls_set(ca_certs=m["ca_file"] or None)
            if m.get("tls_insecure"):
                client.tls_insecure_set(True)
        client.will_set(self.t_avail, "offline", qos=1, retain=True)
        client.on_connect = self.on_connect
        client.on_disconnect = self.on_disconnect
        client.on_message = self.on_message
        client.reconnect_delay_set(min_delay=2, max_delay=60)
        self.client = client

        def term(*_):
            self.stop.set()
        signal.signal(signal.SIGTERM, term)
        signal.signal(signal.SIGINT, term)

        self.apply_persistent_settings()
        self.refresh_slow()
        threading.Thread(target=self.worker, daemon=True).start()
        client.connect_async(m["host"], int(m["port"]), keepalive=int(m["keepalive"]))
        client.loop_start()

        next_state = 0.0
        next_check = time.monotonic() + 60
        was_armed = False
        while not self.stop.is_set():
            now = time.monotonic()
            if was_armed and not self.armed:        # arm timeout expired
                self.work.put(self.publish_state)
            was_armed = self.armed
            if now >= next_state:
                next_state = now + self.cfg["publish_interval_s"]
                if self.connected:
                    self.work.put(lambda: self.publish_state(force=False))
            if now >= next_check:
                next_check = now + self.cfg["update"]["check_interval_h"] * 3600
                self.work.put(self.check_updates)
            self.stop.wait(1)

        if self.connected:
            client.publish(self.t_avail, "offline", qos=1, retain=True).wait_for_publish(3)
        client.disconnect()
        client.loop_stop()


def load_config(path):
    raw = read_json(path, None)
    if raw is None and os.path.exists(path):
        sys.exit(f"{path}: invalid JSON")
    cfg = deep_merge(DEFAULT_CONFIG, raw or {})
    cfg["publish_interval_s"] = max(5, int(cfg["publish_interval_s"]))
    cfg["arm_timeout_s"] = min(max(10, int(cfg["arm_timeout_s"])), 600)
    cfg["update"]["check_interval_h"] = max(1, float(cfg["update"]["check_interval_h"]))
    return cfg


def main():
    ap = argparse.ArgumentParser(description="PX30 Home Assistant MQTT agent")
    ap.add_argument("--config", default=CONFIG_PATH)
    ap.add_argument("--dry-run", action="store_true",
                    help="print discovery + state, execute nothing, no broker needed")
    ap.add_argument("--simulate", action="append", default=[], metavar="CMD=PAYLOAD",
                    help="with --dry-run: feed a command (e.g. arm=ON, brightness=40)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    agent = Agent(load_config(a.config), dry_run=a.dry_run)
    if not a.dry_run:
        agent.run()
        return
    agent.publish_all()
    for item in a.simulate:
        name, _, payload = item.partition("=")
        print(f"\nSIMULATE {agent.t_cmd}/{name} <- {payload!r}")
        job = agent.handle(name, payload)
        if job is None:
            print("  -> rejected")
        else:
            job()


if __name__ == "__main__":
    main()
