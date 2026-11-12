"""Load and check updater/config.yaml."""
import os

import yaml

FW_TYPES = ("zigbee_ncp", "openthread_rcp", "zigbee_router")
CHANNELS = ("stable", "latest", "beta")
BACKENDS = ("sfb", "gsdk")
# firmware type -> the "stack" name px30-radio uses in its catalog
PX30_STACK = {"openthread_rcp": "thread", "zigbee_ncp": "zigbee", "zigbee_router": "router"}


class ConfigError(Exception):
    pass


def load(path):
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg["_dir"] = os.path.dirname(os.path.abspath(path))
    cfg.setdefault("publish", {})
    cfg.setdefault("upstream", {})
    cfg["upstream"].setdefault("sfb_repo", "NabuCasa/silabs-firmware-builder")
    cfg["upstream"].setdefault("gsdk_repo", "SiliconLabs/gecko_sdk")
    cfg.setdefault("check_interval_h", 12)
    pub = cfg["publish"]
    pub.setdefault("repo", "")
    pub.setdefault("branch", "firmware")
    pub.setdefault("path", "")
    pub.setdefault("keep_archived", 10)

    devices = cfg.get("devices") or {}
    if not devices:
        raise ConfigError("no devices in the config")
    for name, dev in devices.items():
        if dev.get("backend") not in BACKENDS:
            raise ConfigError(f"{name}: backend must be one of {BACKENDS}")
        for ch in dev.get("channels") or {}:
            if ch not in CHANNELS:
                raise ConfigError(f"{name}: unknown channel {ch!r}")
        for fw, spec in (dev.get("firmware") or {}).items():
            if fw not in FW_TYPES:
                raise ConfigError(f"{name}: unknown firmware type {fw!r}")
            spec.setdefault("enabled", True)
            if dev["backend"] == "sfb" and not (spec.get("base") and spec.get("overlay")):
                raise ConfigError(f"{name}/{fw}: sfb firmware needs base and overlay")
            if dev["backend"] == "gsdk" and not spec.get("target"):
                raise ConfigError(f"{name}/{fw}: gsdk firmware needs target")
    seen = {}
    for name in devices:
        pub = device_publish(cfg, name)
        key = (pub["repo"], pub["branch"], pub["path"])
        if pub["repo"] and key in seen:
            raise ConfigError(f"{name} and {seen[key]} publish to the same place ({'/'.join(key)}); "
                              "give one of them its own publish.path")
        seen[key] = name
    return cfg


def device_publish(cfg, device):
    """The publish settings of one device: the global `publish` block, overridden by
    the device's own `publish` block. FWPIPE_LOCAL_ONLY=1 publishes nothing to GitHub."""
    pub = dict(cfg.get("publish") or {})
    pub.update((cfg["devices"][device].get("publish") or {}))
    pub.setdefault("repo", "")
    pub.setdefault("branch", "firmware")
    pub["path"] = str(pub.get("path") or "").strip("/")
    if os.environ.get("FWPIPE_LOCAL_ONLY", "").lower() in ("1", "true", "yes"):
        pub["repo"] = ""
    base = pub.get("base_url") or ""
    if not base and pub["repo"]:
        base = f'https://raw.githubusercontent.com/{pub["repo"]}/{pub["branch"]}' + \
            (f'/{pub["path"]}' if pub["path"] else "")
    pub["base_url"] = base.rstrip("/")
    return pub


def jobs(cfg, device=None, firmware=None, channel=None):
    """Every enabled (device, firmware type, channel) combination, filtered."""
    for dname, dev in cfg["devices"].items():
        if device and dname != device:
            continue
        for fw, spec in (dev.get("firmware") or {}).items():
            if (firmware and fw != firmware) or not spec.get("enabled"):
                continue
            for ch, chspec in (dev.get("channels") or {}).items():
                if channel and ch != channel:
                    continue
                skip = spec.get("channels")
                if skip and ch not in skip:
                    continue
                yield dname, fw, ch, dev, spec, chspec
