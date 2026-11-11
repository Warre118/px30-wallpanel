"""Offline tests for the Wi-Fi hotspot setup (px30_hotspot).

Runs on a PC (Windows or Linux, Python 3.9+), no Wi-Fi hardware needed:
iwd is replaced by a fake, the HTTP and DNS servers run on 127.0.0.1.

    python -m unittest discover -s tests -v
"""
import hashlib, http.client, io, json, os, socket, struct, sys, tempfile, threading, time, unittest
from contextlib import redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "rootfs/overlay/usr/local/lib/px30"))
sys.dont_write_bytecode = True
import px30_hotspot as H  # noqa: E402

WWW = os.path.join(ROOT, "rootfs/overlay/usr/share/px30-panel")
SECRET_SSID = "Home Net;42"
SECRET_PW = "s3cret pass\\word"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeIwd:
    """Behaves like iwd for the state machine."""

    def __init__(self, known=0, state="disconnected", iwd_dir=None):
        self.mode = "station"
        self.state = state
        self.known = known
        self.ap = None
        self.iwd_dir = iwd_dir
        self.join_result = (True, None)
        self.ip = "192.168.1.50"
        self.calls = []
        self.come_back = False      # network returns when switched to station

    def status(self):
        return {"mode": self.mode, "state": self.state if self.mode == "station" else "",
                "known": self.known}

    def scan(self, wait=6):
        self.calls.append("scan")
        return [{"ssid": SECRET_SSID, "signal": -60, "security": "psk", "known": False},
                {"ssid": "Cafe", "signal": -80, "security": "open", "known": False}]

    def set_mode(self, mode):
        self.calls.append(f"mode:{mode}")
        self.mode = mode
        if mode == "station" and self.come_back:
            self.state = "connected"
        return True

    def ap_start(self, ssid):
        self.calls.append("ap_start")
        if self.mode != "ap":
            return False, "NotSupported"
        self.ap = ssid
        return True, ""

    def ap_stop(self):
        self.calls.append("ap_stop")
        self.ap = None

    def connect(self, ssid, hidden=False):
        self.calls.append("connect")
        ok, err = self.join_result
        if ok:
            self.state = "connected"
            self.known += 1
        return ok, err

    def ipv4(self):
        return self.ip if self.state == "connected" else None

    def mac_suffix(self):
        return "A1B2"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def make(known=0, state="disconnected"):
    tmp = tempfile.mkdtemp()
    clock = Clock()
    iwd = FakeIwd(known, state)
    ev = {"show": 0, "joined": None, "done": 0}
    hs = H.Hotspot(iwd=iwd, on_show=lambda: ev.__setitem__("show", ev["show"] + 1),
                   on_joined=lambda ip, pin: ev.__setitem__("joined", (ip, pin)),
                   on_done=lambda: ev.__setitem__("done", ev["done"] + 1),
                   http_port=free_port(), dns_port=0, dns_addr="127.0.0.1", www=WWW,
                   hostname="px30-test", clock=clock, sleep=clock.sleep,
                   ap_dir=os.path.join(tmp, "ap"), iwd_dir=tmp)
    return hs, iwd, clock, ev, tmp


def run_until(hs, clock, cond, limit=2000):
    for _ in range(limit):
        if cond():
            return True
        hs.tick()
        clock.sleep(H.TICK_S)
    return cond()


def req(hs, method, path, body=None, pin=None):
    c = http.client.HTTPConnection("127.0.0.1", hs.http_port, timeout=5)
    h = {"Content-Type": "application/json"}
    if pin is not None:
        h["X-Pin"] = pin
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


class Helpers(unittest.TestCase):
    def test_known_file_names(self):
        self.assertEqual(H.known_file("MyNet_5G-x", base="/d"), "/d/MyNet_5G-x.psk")
        self.assertEqual(H.known_file("a b", "open", base="/d"), "/d/=612062.open")
        self.assertEqual(H.known_file("café", base="/d"), "/d/=636166c3a9.psk")

    def test_psk_matches_ieee_vector(self):
        # IEEE 802.11i-2004 H.4.1: passphrase "password", SSID "IEEE"
        sec, body = H.known_content("IEEE", "password")
        self.assertEqual(sec, "psk")
        self.assertIn("PreSharedKey=f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e", body)
        self.assertIn("Passphrase=password\n", body)

    def test_known_content_variants(self):
        self.assertEqual(H.known_content("x", "")[0], "open")
        self.assertIn("Hidden=true", H.known_content("x", "", True)[1])
        hexpsk = "AB" * 32
        sec, body = H.known_content("x", hexpsk)
        self.assertIn("PreSharedKey=" + hexpsk.lower(), body)
        self.assertNotIn("Passphrase", body)
        self.assertIn("Passphrase=\\sa\\\\b\\s\n", H.known_content("x", " a\\b ")[1])

    def test_validation(self):
        self.assertTrue(H.valid_passphrase("12345678"))
        self.assertFalse(H.valid_passphrase("1234567"))
        self.assertFalse(H.valid_passphrase("x" * 64))
        self.assertTrue(H.valid_passphrase("a" * 64))           # hex PSK
        self.assertFalse(H.valid_passphrase("ünicode!!"))
        self.assertTrue(H.valid_ssid("x" * 32))
        self.assertFalse(H.valid_ssid("x" * 33))
        self.assertFalse(H.valid_ssid(""))
        self.assertFalse(H.valid_ssid("a\nb"))

    def test_wifi_qr_escaping(self):
        self.assertEqual(H.wifi_qr('a;b', 'p:"q\\'), 'WIFI:T:WPA;S:a\\;b;P:p\\:\\"q\\\\;;')

    def test_dns_reply(self):
        q = struct.pack(">HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0) + b"\x07example\x03com\x00" + struct.pack(">HH", 1, 1)
        r = H.dns_reply(q, "192.168.4.1")
        tid, flags, qd, an = struct.unpack(">HHHH", r[:8])
        self.assertEqual((tid, qd, an), (0x1234, 1, 1))
        self.assertTrue(flags & 0x8000)
        self.assertEqual(r[-4:], socket.inet_aton("192.168.4.1"))
        aaaa = q[:-4] + struct.pack(">HH", 28, 1)
        self.assertEqual(struct.unpack(">H", H.dns_reply(aaaa)[6:8])[0], 0)
        self.assertIsNone(H.dns_reply(b"\x00" * 5))
        self.assertIsNone(H.dns_reply(r))                     # a response, not a query

    def test_unwrap_busctl_json(self):
        v = {"type": "a{sv}", "data": [{"Name": {"type": "s", "data": "wlan0"}}]}
        self.assertEqual(H.unwrap(v["data"][0]), {"Name": "wlan0"})


class IwdParsing(unittest.TestCase):
    OBJS = {"type": "a{oa{sa{sv}}}", "data": [{
        "/net/connman/iwd/0/4": {
            "net.connman.iwd.Device": {"Name": {"type": "s", "data": "wlan0"},
                                       "Mode": {"type": "s", "data": "station"}},
            "net.connman.iwd.Station": {"State": {"type": "s", "data": "disconnected"}}},
        "/net/connman/iwd/486f6d65_psk": {
            "net.connman.iwd.KnownNetwork": {"Name": {"type": "s", "data": "Home"}}},
        "/net/connman/iwd/0/4/486f6d65_psk": {
            "net.connman.iwd.Network": {"Name": {"type": "s", "data": "Home"},
                                        "Type": {"type": "s", "data": "psk"},
                                        "Device": {"type": "o", "data": "/net/connman/iwd/0/4"},
                                        "KnownNetwork": {"type": "o", "data": "/net/connman/iwd/486f6d65_psk"}}},
        "/net/connman/iwd/0/4/77_8021x": {
            "net.connman.iwd.Network": {"Name": {"type": "s", "data": "Corp"},
                                        "Type": {"type": "s", "data": "8021x"},
                                        "Device": {"type": "o", "data": "/net/connman/iwd/0/4"}}},
    }]}
    ORDERED = {"type": "a(on)", "data": [[["/net/connman/iwd/0/4/486f6d65_psk", -5600],
                                          ["/net/connman/iwd/0/4/77_8021x", -6000]]]}

    def run_fake(self, cmd, timeout=30):
        self.cmds.append(cmd)
        if "GetManagedObjects" in cmd:
            return 0, json.dumps(self.OBJS)
        if "GetOrderedNetworks" in cmd:
            return 0, json.dumps(self.ORDERED)
        if "Connect" in cmd:
            return self.connect_rc
        return 0, ""

    def setUp(self):
        self.cmds = []
        self.connect_rc = (0, "")
        self.iwd = H.Iwd(run=self.run_fake)

    def test_status(self):
        self.assertEqual(self.iwd.status(), {"mode": "station", "state": "disconnected", "known": 1})

    def test_scan_skips_8021x(self):
        self.assertEqual(self.iwd.scan(wait=0),
                         [{"ssid": "Home", "signal": -56, "security": "psk", "known": True}])

    def test_set_mode_command(self):
        self.assertTrue(self.iwd.set_mode("ap"))
        
        self.assertIn("set-property", self.cmds[-1])
        self.assertEqual(self.cmds[-1][-3:], ["Mode", "s", "ap"])

    def test_connect_errors(self):
        self.assertEqual(self.iwd.connect("Home", wait_find=0), (True, None))
        self.connect_rc = (1, "Call failed: Operation failed (net.connman.iwd.Failed)")
        self.assertEqual(self.iwd.connect("Home", wait_find=0), (False, "auth"))
        self.assertEqual(self.iwd.connect("Nope", wait_find=0), (False, "notfound"))
        self.connect_rc = (0, "")
        self.assertEqual(self.iwd.connect("Hidden1", hidden=True, wait_find=0), (True, None))
        self.assertIn("ConnectHiddenNetwork", self.cmds[-1])


class StateMachine(unittest.TestCase):
    def setUp(self):
        self.err = io.StringIO()
        self._redir = redirect_stderr(self.err)
        self._redir.__enter__()
        self.servers = []

    def tearDown(self):
        for hs in self.servers:
            hs._stop_http()
            if hs.dns:
                hs.dns.stop()
        self._redir.__exit__(None, None, None)
        log = self.err.getvalue()
        # never a Wi-Fi name or password in the log
        for secret in (SECRET_SSID, SECRET_PW, "Cafe"):
            self.assertNotIn(secret, log)
        for hs in self.servers:
            if hs.passphrase:
                self.assertNotIn(hs.passphrase, log)

    def mk(self, **kw):
        r = make(**kw)
        self.servers.append(r[0])
        return r

    def test_connected_never_starts(self):
        hs, iwd, clock, ev, _ = self.mk(known=1, state="connected")
        run_until(hs, clock, lambda: False, limit=600)       # 20 min
        self.assertEqual(hs.phase, "idle")
        self.assertNotIn("mode:ap", iwd.calls)

    def test_no_known_network_starts_after_grace(self):
        hs, iwd, clock, ev, tmp = self.mk(known=0)
        hs.tick()
        clock.sleep(H.GRACE_S - 5)
        hs.tick()
        self.assertEqual(hs.phase, "idle")
        self.assertTrue(run_until(hs, clock, lambda: hs.phase == "ap", limit=10))
        self.assertEqual(iwd.mode, "ap")
        self.assertEqual(iwd.ap, "PX30-A1B2")
        self.assertEqual(ev["show"], 1)
        prof = open(os.path.join(tmp, "ap", "PX30-A1B2.ap")).read()
        self.assertIn("Passphrase=" + hs.passphrase, prof)
        self.assertIn("Address=192.168.4.1", prof)
        self.assertEqual(len(hs.passphrase), 10)
        self.assertRegex(hs.pin, r"^\d{6}$")
        self.assertEqual(len(hs.networks), 2)

    def test_known_network_down_waits_fail_time(self):
        hs, iwd, clock, ev, _ = self.mk(known=1)
        hs.tick()
        run_until(hs, clock, lambda: False, limit=int((H.FAIL_S - 10) / H.TICK_S))
        self.assertEqual(hs.phase, "idle")
        self.assertTrue(run_until(hs, clock, lambda: hs.phase == "ap", limit=20))

    def test_brief_outage_does_not_start(self):
        hs, iwd, clock, ev, _ = self.mk(known=1)
        hs.tick()
        run_until(hs, clock, lambda: False, limit=int(H.FAIL_S / 2 / H.TICK_S))
        iwd.state = "connected"
        hs.tick()
        iwd.state = "disconnected"
        run_until(hs, clock, lambda: False, limit=int((H.FAIL_S - 20) / H.TICK_S))
        self.assertEqual(hs.phase, "idle")

    def start_ap(self, **kw):
        hs, iwd, clock, ev, tmp = self.mk(**kw)
        hs.start()
        hs.tick()
        self.assertEqual(hs.phase, "ap")
        return hs, iwd, clock, ev, tmp

    def test_portal_and_pin(self):
        hs, iwd, clock, ev, _ = self.start_ap()
        st, body = req(hs, "GET", "/generate_204")
        self.assertEqual(st, 200)
        self.assertIn(b"second QR code", body)
        st, body = req(hs, "GET", "/setup")
        self.assertEqual(st, 200)
        self.assertIn(b"Connect the panel to your Wi-Fi", body)
        self.assertEqual(req(hs, "GET", "/api/networks")[0], 403)
        self.assertEqual(req(hs, "GET", "/api/networks", pin="000000" if hs.pin != "000000" else "111111")[0], 403)
        st, body = req(hs, "GET", "/api/networks", pin=hs.pin)
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body)["networks"][0]["ssid"], SECRET_SSID)

    def test_pin_lockout(self):
        hs, *_ = self.start_ap()
        wrong = "000000" if hs.pin != "000000" else "111111"
        for _ in range(10):
            req(hs, "GET", "/api/state", pin=wrong)
        self.assertEqual(req(hs, "GET", "/api/state", pin=hs.pin)[0], 403)

    def test_join_success(self):
        hs, iwd, clock, ev, tmp = self.start_ap()
        pin = hs.pin
        st, body = req(hs, "POST", "/api/join", {"ssid": SECRET_SSID, "password": SECRET_PW}, pin=pin)
        self.assertEqual(st, 200, body)
        self.assertEqual(json.loads(body)["host"], "px30-test")
        self.assertEqual(hs.phase, "joining")
        hs.tick()
        self.assertEqual(hs.phase, "joined")
        self.assertEqual(ev["joined"], ("192.168.1.50", pin))
        self.assertEqual(iwd.mode, "station")
        path = H.known_file(SECRET_SSID, "psk", tmp)
        body = open(path).read()
        self.assertIn("PreSharedKey=", body)
        self.assertFalse(os.path.exists(os.path.join(tmp, "ap", "PX30-A1B2.ap")))
        self.assertIsNone(hs.passphrase)
        st, body = req(hs, "GET", "/ping")
        self.assertEqual(st, 200)
        self.assertEqual(req(hs, "GET", "/api/state", pin=pin)[0], 404)
        # the /ping window closes
        clock.sleep(H.JOINED_S + 1)
        hs.tick()
        self.assertEqual(hs.phase, "idle")

    def test_join_wrong_password_back_to_hotspot(self):
        hs, iwd, clock, ev, tmp = self.start_ap()
        ssid, pw, pin = hs.ssid, hs.passphrase, hs.pin
        iwd.join_result = (False, "auth")
        req(hs, "POST", "/api/join", {"ssid": SECRET_SSID, "password": SECRET_PW}, pin=pin)
        hs.tick()
        self.assertEqual(hs.phase, "ap")
        self.assertEqual((hs.ssid, hs.passphrase, hs.pin), (ssid, pw, pin))   # phone rejoins by itself
        self.assertEqual(iwd.mode, "ap")
        self.assertIn("wrong password", hs.error)
        self.assertFalse(os.path.exists(H.known_file(SECRET_SSID, "psk", tmp)))
        st, body = req(hs, "GET", "/api/state", pin=pin)
        self.assertIn("wrong password", json.loads(body)["error"])

    def test_join_failure_restores_existing_file(self):
        hs, iwd, clock, ev, tmp = self.start_ap()
        path = H.known_file(SECRET_SSID, "psk", tmp)
        with open(path, "w") as f:
            f.write("[Security]\nPassphrase=oldvalue1\n")
        iwd.join_result = (False, "auth")
        req(hs, "POST", "/api/join", {"ssid": SECRET_SSID, "password": SECRET_PW}, pin=hs.pin)
        hs.tick()
        self.assertEqual(open(path).read(), "[Security]\nPassphrase=oldvalue1\n")

    def test_join_write_error_recovers(self):
        hs, iwd, clock, ev, tmp = self.start_ap()
        hs.iwd_dir = os.path.join(tmp, "missing-dir")          # write fails
        req(hs, "POST", "/api/join", {"ssid": SECRET_SSID, "password": SECRET_PW}, pin=hs.pin)
        hs.tick()
        self.assertEqual(hs.phase, "ap")
        self.assertIsNone(hs.pending)
        self.assertEqual(iwd.mode, "ap")

    def test_join_rejects_bad_input(self):
        hs, *_ = self.start_ap()
        st, body = req(hs, "POST", "/api/join", {"ssid": SECRET_SSID, "password": "short"}, pin=hs.pin)
        self.assertEqual(st, 400)
        st, body = req(hs, "POST", "/api/join", {"ssid": "", "password": "longenough"}, pin=hs.pin)
        self.assertEqual(st, 400)
        self.assertEqual(hs.phase, "ap")

    def test_retry_known_network_when_idle(self):
        hs, iwd, clock, ev, _ = self.mk(known=1)
        hs.tick()
        self.assertTrue(run_until(hs, clock, lambda: hs.phase == "ap", limit=400))
        iwd.come_back = True
        self.assertTrue(run_until(hs, clock, lambda: hs.phase == "idle",
                                  limit=int((H.RETRY_S + H.IDLE_S) / H.TICK_S) + 50))
        self.assertEqual(ev["done"], 1)
        self.assertEqual(iwd.mode, "station")

    def test_no_retry_while_phone_active(self):
        hs, iwd, clock, ev, _ = self.mk(known=1)
        hs.tick()
        run_until(hs, clock, lambda: hs.phase == "ap", limit=400)
        iwd.come_back = True
        for _ in range(int(H.RETRY_S * 2 / H.TICK_S)):
            hs.touch()                          # phone uses the setup page
            hs.tick()
            clock.sleep(H.TICK_S)
        self.assertEqual(hs.phase, "ap")

    def test_retry_fails_restarts_same_hotspot(self):
        hs, iwd, clock, ev, _ = self.mk(known=1)
        hs.tick()
        run_until(hs, clock, lambda: hs.phase == "ap", limit=400)
        name, pw = hs.ssid, hs.passphrase
        clock.sleep(H.RETRY_S + H.IDLE_S)
        hs.tick()
        self.assertEqual(hs.phase, "ap")
        self.assertEqual((hs.ssid, hs.passphrase), (name, pw))
        self.assertGreaterEqual(iwd.calls.count("ap_start"), 2)

    def test_cancel(self):
        hs, iwd, clock, ev, tmp = self.start_ap(known=1)
        hs.cancel()
        hs.tick()
        self.assertEqual(hs.phase, "idle")
        self.assertEqual(iwd.mode, "station")
        self.assertEqual(ev["done"], 1)
        self.assertEqual(os.listdir(os.path.join(tmp, "ap")), [])

    def test_ap_start_failure_backs_off(self):
        hs, iwd, clock, ev, _ = self.mk(known=0)
        iwd.ap_start = lambda ssid: (False, "NotSupported")
        hs.tick()
        run_until(hs, clock, lambda: "mode:station" in iwd.calls, limit=100)
        self.assertEqual(hs.phase, "idle")
        self.assertEqual(iwd.mode, "station")
        n = iwd.calls.count("mode:ap")
        run_until(hs, clock, lambda: False, limit=int((H.BACKOFF_S - 30) / H.TICK_S))
        self.assertEqual(iwd.calls.count("mode:ap"), n)

    def test_recover_from_left_over_ap(self):
        hs, iwd, clock, ev, tmp = self.mk(known=1)
        os.makedirs(os.path.join(tmp, "ap"))
        open(os.path.join(tmp, "ap", "PX30-A1B2.ap"), "w").close()
        iwd.mode = "ap"
        hs.recover()
        self.assertEqual(iwd.mode, "station")
        self.assertEqual(os.listdir(os.path.join(tmp, "ap")), [])

    def test_screen_state(self):
        hs, *_ = self.start_ap()
        s = hs.screen_state()
        self.assertEqual(s["setup_qr"], f"http://192.168.4.1/setup#{hs.pin}")
        self.assertTrue(s["wifi_qr"].startswith("WIFI:T:WPA;S:PX30-A1B2;P:"))
        self.assertFalse(s["can_cancel"])


class DNS(unittest.TestCase):
    def test_udp_server(self):
        d = H.DNSServer("127.0.0.1", 0)
        self.assertTrue(d.start(tries=3))
        try:
            q = struct.pack(">HHHHHH", 7, 0x0100, 1, 0, 0, 0) + b"\x11connectivitycheck\x07gstatic\x03com\x00" + struct.pack(">HH", 1, 1)
            c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            c.settimeout(3)
            c.sendto(q, ("127.0.0.1", d.port))
            r, _ = c.recvfrom(512)
            c.close()
            self.assertEqual(r[-4:], socket.inet_aton("127.0.0.1"))
        finally:
            d.stop()


if __name__ == "__main__":
    unittest.main()
