"""
px30_hotspot - Wi-Fi setup through a temporary access point (used by px30-panel).

Tested on the hardware once (2026-11-12, after a factory reset); unit tests
with a fake iwd in tests/test_hotspot.py. See docs/usage.md, "Wi-Fi setup
(hotspot)".

Flow
  1. No Wi-Fi network is known, or the known network has been unreachable for
     FAIL_S (5 min): the panel switches wlan0 to AP mode and starts the
     network PX30-xxxx (WPA2, random passphrase). The panel screen shows a
     Wi-Fi QR code (join the hotspot) and a second QR code
     (http://192.168.4.1/setup#PIN) plus the PIN.
  2. The phone joins. There is NO captive portal: the phones' connectivity
     checks get the "online" answer, so no sign-in window pops up (it traps
     the user and cannot follow the panel to the home network). The second QR
     code opens the setup page in the normal browser; every other address
     (all DNS names resolve to 192.168.4.1) redirects to it.
  3. On /setup the user picks the home network (list scanned before the AP
     started, or a hidden network typed in) and enters the password.
  4. The panel leaves AP mode and joins that network. Success: px30-panel
     continues with the normal setup (https://<home-ip>:8443/setup, same PIN,
     QR code on the screen); the phone page keeps checking
     http://<hostname>.local/ping and then opens that setup page.
     Failure (wrong password, network not found): the known-network file is
     restored, the hotspot comes back with the same name and passphrase, and
     the phone page shows the error.
  5. In hotspot mode with a known network (e.g. the router was off), the panel
     tries the home network again every RETRY_S if no phone used the setup
     page for IDLE_S.

Wi-Fi names and passwords are never logged.

Uses iwd's own AP mode and DHCP server (iwd 3.x, EnableNetworkConfiguration)
through busctl, so no extra packages are needed. Python stdlib only.
"""
import hashlib, http.server, json, os, re, secrets, socket, struct, subprocess
import sys, threading, time, urllib.parse

AP_ADDR = "192.168.4.1"

# Connectivity checks of phones and laptops, answered as "online" so the OS
# does not open a captive-portal window. path -> (status, body)
_APPLE_OK = "<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>"
ONLINE_CHECKS = {
    "/generate_204": (204, ""),                 # Android, Chrome
    "/gen_204": (204, ""),
    "/hotspot-detect.html": (200, _APPLE_OK),   # iOS, macOS
    "/library/test/success.html": (200, _APPLE_OK),
    "/connecttest.txt": (200, "Microsoft Connect Test"),  # Windows
    "/ncsi.txt": (200, "Microsoft NCSI"),
    "/success.txt": (200, "success\n"),         # Firefox
    "/check_network_status.txt": (200, "NetworkManager is online\n"),  # GNOME
}
IWD_DIR = "/var/lib/iwd"
AP_DIR = f"{IWD_DIR}/ap"
IFACE = "wlan0"
WWW = "/usr/share/px30-panel"
HTTP_PORT = 80
DNS_PORT = 53
GRACE_S = 45            # no known network: time for iwd to come up first
FAIL_S = 300            # known network unreachable this long -> hotspot
RETRY_S = 300           # hotspot + known network: retry it this often ...
IDLE_S = 180            # ... when no phone used the setup page for this long
PROBE_S = 60            # how long such a retry waits for a connection
JOIN_S = 45             # how long a join waits for a connection
DHCP_S = 20             # and then for an IPv4 address
JOINED_S = 15 * 60      # after a join: /ping stays reachable this long
BACKOFF_S = 600         # AP start failed: do not try again for this long
TICK_S = 2
PASS_CHARS = "abcdefghjkmnpqrstuvwxyz23456789"   # no 0/o, 1/l/i


def log(*a):
    print("hotspot:", *a, file=sys.stderr, flush=True)


def _run(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout if p.returncode == 0 else p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


# ------------------------------------------------------------------ helpers
def known_file(ssid, security="psk", base=IWD_DIR):
    """File name iwd uses for a known network: the SSID itself if it only has
    [A-Za-z0-9_-], otherwise '=' + hex of the UTF-8 bytes."""
    raw = ssid.encode()
    name = ssid if re.fullmatch(r"[A-Za-z0-9_-]+", ssid) else "=" + raw.hex()
    return f"{base}/{name}.{security}"


def ell_escape(v):
    """Escape a value for an iwd (ELL) settings file."""
    v = v.replace("\\", "\\\\")
    if v.startswith(" "):
        v = "\\s" + v[1:]
    if v.endswith(" ") and len(v) > 1:
        v = v[:-1] + "\\s"
    return v


def valid_ssid(ssid):
    return isinstance(ssid, str) and 1 <= len(ssid.encode()) <= 32 and "\n" not in ssid and "\0" not in ssid


def valid_passphrase(pw):
    """WPA2/3 passphrase: 8..63 printable ASCII characters (or a 64-hex PSK)."""
    if not isinstance(pw, str):
        return False
    if re.fullmatch(r"[0-9A-Fa-f]{64}", pw):
        return True
    return 8 <= len(pw) <= 63 and all(32 <= ord(c) <= 126 for c in pw)


def known_content(ssid, password, hidden=False):
    """(security, file content) for a new known network."""
    settings = "[Settings]\nAutoConnect=true\n" + ("Hidden=true\n" if hidden else "")
    if not password:
        return "open", settings
    if re.fullmatch(r"[0-9A-Fa-f]{64}", password):
        return "psk", f"[Security]\nPreSharedKey={password.lower()}\n\n" + settings
    psk = hashlib.pbkdf2_hmac("sha1", password.encode(), ssid.encode(), 4096, 32).hex()
    # Passphrase for WPA3-SAE, PreSharedKey saves iwd the PBKDF2 on WPA2
    return "psk", (f"[Security]\nPassphrase={ell_escape(password)}\nPreSharedKey={psk}\n\n" + settings)


def wifi_qr(ssid, password):
    """Payload of a 'join this Wi-Fi' QR code (ZXing WIFI: format)."""
    def esc(s):
        return re.sub(r'([\\;,:"])', r"\\\1", s)
    return f"WIFI:T:WPA;S:{esc(ssid)};P:{esc(password)};;"


def dns_reply(q, addr=AP_ADDR, ttl=10):
    """Answer every A query with addr; other types get an empty answer."""
    if len(q) < 12:
        return None
    tid, flags, qd = struct.unpack(">HHH", q[:6])
    if flags & 0x8000 or (flags >> 11) & 0xF or qd != 1:
        return None
    i = 12
    while True:
        if i >= len(q):
            return None
        n = q[i]
        if n == 0:
            i += 1
            break
        if n & 0xC0:
            return None
        i += 1 + n
    if i + 4 > len(q):
        return None
    qtype, qclass = struct.unpack(">HH", q[i:i + 4])
    question = q[12:i + 4]
    rflags = 0x8000 | 0x0400 | (flags & 0x0100) | 0x0080   # QR AA RD RA
    if qtype == 1 and qclass == 1:
        ans = b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, ttl, 4) + socket.inet_aton(addr)
        return struct.pack(">HHHHHH", tid, rflags, 1, 1, 0, 0) + question + ans
    return struct.pack(">HHHHHH", tid, rflags, 1, 0, 0, 0) + question


def unwrap(v):
    """busctl --json values: {"type": t, "data": d} -> d, recursively."""
    if isinstance(v, dict):
        if set(v) == {"type", "data"}:
            return unwrap(v["data"])
        return {k: unwrap(x) for k, x in v.items()}
    if isinstance(v, list):
        return [unwrap(x) for x in v]
    return v


# ---------------------------------------------------------------- iwd (D-Bus)
class Iwd:
    """Minimal iwd control through busctl (python3-dbus is not in the image)."""
    SVC = "net.connman.iwd"
    I = "net.connman.iwd."

    def __init__(self, iface=IFACE, run=_run):
        self.iface = iface
        self.run = run

    def _bus(self, *args, timeout=30):
        return self.run(["busctl", "--system", "--json=short", f"--timeout={timeout}", *args],
                        timeout=timeout + 5)

    def objects(self):
        rc, out = self._bus("call", self.SVC, "/", "org.freedesktop.DBus.ObjectManager",
                            "GetManagedObjects")
        if rc:
            return None
        try:
            return unwrap(json.loads(out)["data"][0])
        except (ValueError, KeyError, IndexError, TypeError):
            return None

    def device(self, objs):
        for path, ifs in objs.items():
            d = ifs.get(self.I + "Device")
            if d and d.get("Name") == self.iface:
                return path, ifs
        return None, None

    def status(self):
        """{'mode', 'state', 'known'} or None if iwd/the device is missing."""
        objs = self.objects()
        if objs is None:
            return None
        path, ifs = self.device(objs)
        if not path:
            return None
        st = ifs.get(self.I + "Station") or {}
        known = sum(1 for i in objs.values() if self.I + "KnownNetwork" in i)
        return {"mode": ifs[self.I + "Device"].get("Mode", ""),
                "state": st.get("State", ""),
                "known": known}

    def scan(self, wait=6):
        """Scan in station mode: [{'ssid', 'signal' (dBm), 'security', 'known'}]."""
        objs = self.objects() or {}
        path, _ = self.device(objs)
        if not path:
            return []
        self._bus("call", self.SVC, path, self.I + "Station", "Scan")
        time.sleep(wait)
        rc, out = self._bus("call", self.SVC, path, self.I + "Station", "GetOrderedNetworks")
        if rc:
            return []
        objs = self.objects() or {}
        res, seen = [], set()
        try:
            for npath, sig in unwrap(json.loads(out)["data"][0]):
                n = objs.get(npath, {}).get(self.I + "Network")
                if not n or n.get("Name") in seen or n.get("Type") not in ("psk", "open"):
                    continue   # 802.1x needs more than a password: not offered
                seen.add(n["Name"])
                res.append({"ssid": n["Name"], "signal": round(sig / 100), "security": n["Type"],
                            "known": bool(n.get("KnownNetwork"))})
        except (ValueError, KeyError, IndexError, TypeError):
            pass
        return res

    def set_mode(self, mode):
        objs = self.objects() or {}
        path, _ = self.device(objs)
        if not path:
            return False
        rc, _ = self._bus("set-property", self.SVC, path, self.I + "Device", "Mode", "s", mode)
        return rc == 0

    def ap_start(self, ssid):
        objs = self.objects() or {}
        path, _ = self.device(objs)
        rc, out = self._bus("call", self.SVC, path or "/", self.I + "AccessPoint", "StartProfile", "s", ssid)
        return rc == 0, out

    def ap_stop(self):
        objs = self.objects() or {}
        path, _ = self.device(objs)
        if path:
            self._bus("call", self.SVC, path, self.I + "AccessPoint", "Stop")

    def connect(self, ssid, hidden=False, wait_find=15):
        """Connect to ssid (its known-network file must exist).
        Returns (ok, error_kind) with error_kind in (None, 'notfound', 'auth', 'failed')."""
        deadline = time.monotonic() + wait_find
        while True:
            objs = self.objects() or {}
            dev, _ = self.device(objs)
            net = None
            for p, ifs in objs.items():
                n = ifs.get(self.I + "Network")
                if n and n.get("Name") == ssid and n.get("Device") == dev:
                    net = p
            if net or time.monotonic() > deadline or not dev:
                break
            time.sleep(2)
        if net:
            rc, out = self._bus("call", self.SVC, net, self.I + "Network", "Connect", timeout=JOIN_S)
        elif hidden and dev:
            rc, out = self._bus("call", self.SVC, dev, self.I + "Station", "ConnectHiddenNetwork", "s",
                                ssid, timeout=JOIN_S)
        else:
            return False, "notfound"
        if rc == 0:
            return True, None
        if "NotFound" in out or "ServiceSetOverlap" in out:
            return False, "notfound"
        if "Failed" in out or "InvalidFormat" in out or "NotConfigured" in out:
            return False, "auth"
        return False, "failed"

    def ipv4(self):
        rc, out = self.run(["ip", "-4", "-o", "addr", "show", "dev", self.iface])
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/", out or "")
        return m.group(1) if m else None

    def mac_suffix(self):
        try:
            mac = open(f"/sys/class/net/{self.iface}/address").read().strip()
            return mac.replace(":", "")[-4:].upper()
        except OSError:
            return secrets.token_hex(2).upper()


# ----------------------------------------------------------------- servers
class DNSServer:
    def __init__(self, addr=AP_ADDR, port=DNS_PORT):
        self.addr, self.port = addr, port
        self.sock = None
        self.stop_ev = threading.Event()

    def start(self, tries=20):
        """Bind (the AP address appears a moment after the AP starts)."""
        for _ in range(tries):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((self.addr, self.port))
                self.port = s.getsockname()[1]
                s.settimeout(1.0)
                self.sock = s
                break
            except OSError:
                s.close()
                time.sleep(1)
        if not self.sock:
            log("dns: cannot bind")
            return False
        self.stop_ev.clear()
        threading.Thread(target=self._loop, daemon=True).start()
        return True

    def _loop(self):
        s = self.sock
        while not self.stop_ev.is_set():
            try:
                q, peer = s.recvfrom(512)
            except socket.timeout:
                continue
            except OSError:
                break
            r = dns_reply(q, self.addr)
            if r:
                try:
                    s.sendto(r, peer)
                except OSError:
                    pass
        s.close()

    def stop(self):
        self.stop_ev.set()
        self.sock = None


class PortalHandler(http.server.BaseHTTPRequestHandler):
    """Port 80. Hotspot: phone setup page + API (PIN); connectivity checks
    answered as online, anything else redirected to /setup.
    After a join: only /ping (the phone page checks <hostname>.local)."""
    server_version = "px30-panel"
    timeout = 20
    hs = None            # set by Hotspot

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        hs = self.hs
        u = urllib.parse.urlsplit(self.path)
        if u.path == "/ping":
            return self.send(200, {"ok": True}, extra={"Access-Control-Allow-Origin": "*"})
        if hs.phase not in ("ap", "joining"):
            return self.send(404, "not found", "text/plain")
        if u.path == "/setup":
            hs.touch()
            return self.send(200, hs.page("hotspot-setup.html"), "text/html; charset=utf-8")
        if u.path.startswith("/api/"):
            if not hs.check_pin(self.headers.get("X-Pin")):
                return self.send(403, {"error": "wrong PIN"})
            hs.touch()
            if u.path == "/api/networks":
                return self.send(200, {"networks": hs.networks})
            if u.path == "/api/state":
                return self.send(200, hs.phone_state())
            return self.send(404, {"error": "not found"})
        if u.path in ONLINE_CHECKS:
            code, body = ONLINE_CHECKS[u.path]
            return self.send(code, body, "text/plain" if code == 200 and not body.startswith("<") else "text/html")
        # anything else (any host name resolves here): the setup page
        return self.send(302, "", "text/plain", extra={"Location": f"http://{AP_ADDR}/setup"})

    def do_POST(self):
        hs = self.hs
        if hs.phase != "ap" or self.path != "/api/join":
            return self.send(404, {"error": "not found"})
        if not hs.check_pin(self.headers.get("X-Pin")):
            return self.send(403, {"error": "wrong PIN"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 4096:
                raise ValueError("too large")
            d = json.loads(self.rfile.read(n) or b"{}")
            ok, msg = hs.request_join(d.get("ssid"), d.get("password") or "", bool(d.get("hidden")))
        except (ValueError, TypeError, AttributeError) as e:
            ok, msg = False, f"invalid input: {e}"
        self.send(200 if ok else 400, {"ok": ok, "msg": msg, "host": hs.hostname})


# -------------------------------------------------------------- state machine
class Hotspot:
    """Phases: idle (station, watching) -> ap -> joining -> joined / ap.
    Callbacks into px30-panel:
      on_show()          show the hotspot screen
      on_joined(ip, pin) joined the home network: continue the setup there
      on_done()          hotspot ended without a join (network back, cancel)"""

    def __init__(self, iwd=None, on_show=None, on_joined=None, on_done=None,
                 http_port=HTTP_PORT, dns_port=DNS_PORT, dns_addr=AP_ADDR, www=WWW, hostname=None,
                 clock=time.monotonic, sleep=time.sleep, ap_dir=AP_DIR, iwd_dir=IWD_DIR):
        self.iwd = iwd or Iwd()
        self.on_show = on_show or (lambda: None)
        self.on_joined = on_joined or (lambda ip, pin: None)
        self.on_done = on_done or (lambda: None)
        self.http_port, self.dns_port, self.dns_addr, self.www = http_port, dns_port, dns_addr, www
        self.hostname = hostname or socket.gethostname()
        self.clock, self.sleep = clock, sleep
        self.ap_dir, self.iwd_dir = ap_dir, iwd_dir
        self.lock = threading.RLock()
        self.phase = "idle"
        self.down_since = None
        self.backoff_until = 0
        self.ssid = self.passphrase = self.pin = None
        self.networks = []
        self.error = ""
        self.msg = ""
        self.ap_since = 0
        self.last_use = 0
        self.joined_until = 0
        self.joined_ip = None
        self.fails = 0
        self.pending = None          # (ssid, password, hidden) waiting for the loop
        self.has_known = False
        self.cmd = None              # "start" / "cancel" from px30-panel
        self.http = None
        self.dns = None

    # ---- helpers for the servers
    def page(self, name):
        with open(f"{self.www}/{name}", "rb") as f:
            return f.read()

    def touch(self):
        self.last_use = self.clock()

    def check_pin(self, pin):
        with self.lock:
            if not self.pin or self.fails >= 10:
                return False
            ok = pin is not None and secrets.compare_digest(str(pin), self.pin)
            if not ok:
                self.fails += 1
            return ok

    def phone_state(self):
        return {"phase": self.phase, "error": self.error, "msg": self.msg, "host": self.hostname}

    def screen_state(self):
        """For the panel screen (local page)."""
        return {"phase": self.phase, "ssid": self.ssid, "passphrase": self.passphrase,
                "pin": self.pin, "setup_url": f"http://{AP_ADDR}/setup",
                "wifi_qr": wifi_qr(self.ssid or "", self.passphrase or ""),
                "setup_qr": f"http://{AP_ADDR}/setup#{self.pin}",
                "error": self.error, "msg": self.msg, "can_cancel": self.can_cancel()}

    def can_cancel(self):
        return self.has_known

    def request_join(self, ssid, password, hidden):
        if not valid_ssid(ssid):
            return False, "invalid network name"
        if password and not valid_passphrase(password):
            return False, "a Wi-Fi password has 8 to 63 characters"
        with self.lock:
            if self.phase != "ap" or self.pending:
                return False, "busy"
            self.pending = (ssid, password, hidden)
            self.phase = "joining"
            self.error = ""
            self.msg = "joining your network..."
        return True, "joining"

    def start(self):
        """Menu / CLI: start the hotspot now (even if Wi-Fi works)."""
        self.cmd = "start"

    def cancel(self):
        self.cmd = "cancel"

    # ---- the loop
    def recover(self):
        """After a crash/restart of px30-panel: no AP left behind."""
        st = self.iwd.status()
        if st and st["mode"] == "ap":
            log("device was left in AP mode: back to station")
            self.iwd.ap_stop()
            self.iwd.set_mode("station")
        try:
            for f in os.listdir(self.ap_dir):
                if f.startswith("PX30-") and f.endswith(".ap"):
                    os.unlink(os.path.join(self.ap_dir, f))
        except OSError:
            pass

    def run(self):
        self.recover()
        while True:
            try:
                self.tick()
            except Exception as e:     # noqa: BLE001 - never let the thread die
                # type only: a message may hold a file name = a Wi-Fi name
                log("error:", type(e).__name__)
            self.sleep(TICK_S)

    def tick(self):
        now = self.clock()
        cmd, self.cmd = self.cmd, None
        if self.phase == "joined":
            if now > self.joined_until:
                self._stop_http()
                self.phase = "idle"
            elif cmd != "start":
                return
        if self.phase == "joining" and self.pending:
            return self._join()
        if self.phase == "ap":
            if cmd == "cancel":
                return self._leave_ap(done=True)
            return self._ap_tick(now)
        # idle
        if cmd == "start":
            return self._start_ap("menu")
        st = self.iwd.status()
        if st is None:
            return
        self.has_known = bool(st["known"])
        if st["state"] in ("connected", "roaming"):
            self.down_since = None
            return
        if self.down_since is None:
            self.down_since = now
        if now < self.backoff_until:
            return
        if not st["known"] and now - self.down_since >= GRACE_S:
            return self._start_ap("no known network")
        if st["known"] and now - self.down_since >= FAIL_S:
            return self._start_ap("network unreachable")

    def _start_ap(self, why):
        log("starting, reason:", why)
        st = self.iwd.status()
        self.has_known = bool(st and st["known"])
        if st and st["mode"] == "station":
            self.networks = self.iwd.scan()
        with self.lock:
            if not self.ssid:
                self.ssid = f"PX30-{self.iwd.mac_suffix()}"
                self.passphrase = "".join(secrets.choice(PASS_CHARS) for _ in range(10))
                self.pin = f"{secrets.randbelow(10**6):06d}"
                self.fails = 0
        if not self._ap_up():
            log("could not start the access point")
            self._write_ap_profile(remove=True)
            self.iwd.set_mode("station")
            self.phase = "idle"
            self.backoff_until = self.clock() + BACKOFF_S
            self.down_since = None
            return
        self.phase = "ap"
        self.msg = "waiting for your phone..."
        self.ap_since = self.last_use = self.clock()
        self._start_http()
        self.on_show()

    def _ap_up(self):
        self._write_ap_profile()
        if not self.iwd.set_mode("ap"):
            return False
        ok, _ = self.iwd.ap_start(self.ssid)
        if not ok:
            return False
        self.dns = DNSServer(self.dns_addr, self.dns_port)
        threading.Thread(target=self.dns.start, daemon=True).start()
        return True

    def _ap_down(self):
        if self.dns:
            self.dns.stop()
            self.dns = None
        self.iwd.ap_stop()
        self.iwd.set_mode("station")

    def _write_ap_profile(self, remove=False):
        path = f"{self.ap_dir}/{self.ssid}.ap"
        if remove:
            try:
                os.unlink(path)
            except OSError:
                pass
            return
        os.makedirs(self.ap_dir, mode=0o700, exist_ok=True)
        body = (f"[Security]\nPassphrase={self.passphrase}\n\n"
                f"[IPv4]\nAddress={AP_ADDR}\nGateway={AP_ADDR}\nNetmask=255.255.255.0\n"
                f"DNSList={AP_ADDR}\n")
        fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(body)
        os.replace(path + ".tmp", path)

    def _ap_tick(self, now):
        if not self.has_known:
            return
        if now - self.ap_since < RETRY_S or now - self.last_use < IDLE_S:
            return
        # nobody is setting it up: maybe the home network is back
        log("retrying the known network")
        self.phase = "probe"
        self._ap_down()
        deadline = self.clock() + PROBE_S
        while self.clock() < deadline:
            self.sleep(3)
            st = self.iwd.status()
            if st and st["state"] == "connected":
                log("known network is back")
                return self._end(done=True)
        if self._ap_up():
            self.phase = "ap"
            self.ap_since = self.last_use = self.clock()
        else:
            self._end(done=True)

    def _leave_ap(self, done):
        self._ap_down()
        self._end(done)

    def _end(self, done):
        self._write_ap_profile(remove=True)
        self._stop_http()
        self.phase = "idle"
        self.down_since = None
        self.ssid = self.passphrase = self.pin = None
        if done:
            self.on_done()

    def _join(self):
        ssid, password, hidden = self.pending
        self.sleep(2)                  # let the phone get its answer first
        self._ap_down()
        sec, content = known_content(ssid, password, hidden)
        path = known_file(ssid, sec, self.iwd_dir)
        old = None
        try:
            with open(path) as f:
                old = f.read()
        except OSError:
            pass
        try:
            fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(content)
            os.replace(path + ".tmp", path)
            self.sleep(3)              # iwd picks the new file up (inotify)
            ok, err = self.iwd.connect(ssid, hidden)
        except Exception as e:         # noqa: BLE001 - always get back to a usable state
            log("join error:", type(e).__name__)
            ok, err = False, "failed"
        ip = None
        if ok:
            deadline = self.clock() + DHCP_S
            while self.clock() < deadline and not ip:
                ip = self.iwd.ipv4()
                if not ip:
                    self.sleep(1)
            if not ip:
                # Found on the hardware: iwd may report the Connect call as done
                # (it auto-connects as soon as the file appears) while the
                # router keeps rejecting the password (deauth, reason 2). Only
                # call it a DHCP problem when the link really is up.
                st = self.iwd.status() or {}
                ok, err = False, ("dhcp" if st.get("state") == "connected" else "auth")
        with self.lock:
            self.pending = None
        if ok:
            log("joined the home network")
            pin = self.pin
            self._write_ap_profile(remove=True)
            self.phase = "joined"
            self.joined_ip = ip
            self.joined_until = self.clock() + JOINED_S
            self.msg = "connected"
            self.ssid = self.passphrase = None
            self.on_joined(ip, pin)
            return
        log("join failed:", err)
        try:
            if old is None:
                os.unlink(path)
            else:
                with open(path, "w") as f:
                    f.write(old)
        except OSError:
            pass
        self.error = {"auth": "Could not connect: wrong password?",
                      "notfound": "Network not found. Check the name, or move closer to the router.",
                      "dhcp": "Connected, but the network gave no IP address.",
                      }.get(err, "Could not connect to this network.")
        self.msg = ""
        if self._ap_up():
            self.phase = "ap"
            self.ap_since = self.last_use = self.clock()
            self.on_show()
        else:
            self._end(done=True)

    def _start_http(self):
        if self.http:
            return
        handler = type("H", (PortalHandler,), {"hs": self})
        try:
            srv = http.server.ThreadingHTTPServer(("0.0.0.0", self.http_port), handler)
        except OSError as e:
            log("http:", e)
            return
        self.http = srv
        threading.Thread(target=srv.serve_forever, daemon=True).start()

    def _stop_http(self):
        if self.http:
            srv, self.http = self.http, None
            threading.Thread(target=lambda: (srv.shutdown(), srv.server_close()), daemon=True).start()
