"""Dry run of px30-panel's hotspot integration, without hardware or root.

Loads px30-panel with its paths moved to a temp dir, replaces iwd by the fake
from test_hotspot and walks: start -> panel screen state (QR codes) -> join
from the "phone" -> normal setup continues with the same PIN.

    python3 tests/smoke_panel_hotspot.py        (Linux/WSL; needs openssl)
"""
import json, os, sys, tempfile, threading, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import test_hotspot as T  # noqa: E402

tmp = tempfile.mkdtemp()
src = open(os.path.join(ROOT, "rootfs/overlay/usr/local/sbin/px30-panel"), encoding="utf-8").read()
for a, b in (('"/run/px30-panel"', repr(f"{tmp}/run")), ('"/data/px30-panel"', repr(f"{tmp}/data")),
             ('"/data/kiosk/kiosk.conf"', repr(f"{tmp}/kiosk.conf")),
             ('"/usr/share/px30-panel"', repr(os.path.join(ROOT, "rootfs/overlay/usr/share/px30-panel"))),
             ('"/usr/local/lib/px30"', repr(os.path.join(ROOT, "rootfs/overlay/usr/local/lib/px30"))),
             ("LOCAL_PORT = 8088", f"LOCAL_PORT = {T.free_port()}"),
             ("SETUP_PORT = 8443", f"SETUP_PORT = {T.free_port()}")):
    assert a in src, a
    src = src.replace(a, b)
g = {"__name__": "px30_panel_smoke"}
exec(compile(src, "px30-panel", "exec"), g)
P = g["px30_hotspot"]
assert g["HOTSPOT"] is not None, "hotspot module not loaded"

# the real Hotspot object, re-created with the fake iwd and test ports
clock = T.Clock()
iwd = T.FakeIwd(known=0)
hs = P.Hotspot(iwd=iwd, on_show=g["_hotspot_show"], on_joined=g["_hotspot_joined"],
               on_done=g["_hotspot_done"], http_port=T.free_port(), dns_port=0, dns_addr="127.0.0.1",
               www=g["WWW"], hostname="px30-test", clock=clock, sleep=clock.sleep,
               ap_dir=f"{tmp}/iwd/ap", iwd_dir=f"{tmp}/iwd")
g["HOTSPOT"] = hs
g["sh"] = lambda *cmd, timeout=30: (0, "") if cmd[:1] == ("pkill",) else g["subprocess"].run(
    cmd, capture_output=True, text=True).returncode and (1, "") or (0, "")

srv = g["http"].server.ThreadingHTTPServer(("127.0.0.1", g["LOCAL_PORT"]), g["LocalHandler"])
threading.Thread(target=srv.serve_forever, daemon=True).start()
KIOSK = g["KIOSK"]


def local(path, method="GET", body=None):
    rq = urllib.request.Request(f"{g['LOCAL']}{path}", method=method,
                                data=json.dumps(body).encode() if body else None,
                                headers={"X-Token": KIOSK.token or "", "Content-Type": "application/json"})
    with urllib.request.urlopen(rq, timeout=30) as r:
        return json.load(r)


hs.start()
hs.tick()
assert hs.phase == "ap", hs.phase
assert KIOSK.mode == "hotspot", KIOSK.mode
url = open(g["URL_FILE"]).read().strip()
assert "/hotspot?k=" in url, url
st = local("/api/state?qr=1")
assert st["phase"] == "ap" and st["pin"] == hs.pin and st["ssid"] == "PX30-A1B2"
assert "wifi_qr" not in st
have_qr = bool(st.get("qr1")) and bool(st.get("qr2"))
print("screen state ok; QR codes rendered:", have_qr, "(needs python3-qrcode)")

# the phone joins the home network
pin = hs.pin
c = T.req(hs, "POST", "/api/join", {"ssid": "Home", "password": "password123"}, pin=pin)
assert c[0] == 200, c
hs.tick()
assert hs.phase == "joined", hs.phase
time.sleep(0.5)
S = g["SETUP"]
assert S.active and S.pin == pin, "setup did not continue with the same PIN"
assert KIOSK.mode == "setup", KIOSK.mode
st = local("/api/state")
assert st["pin"] == pin
print("join ok; normal setup continues with the same PIN on", st["url"])

# the cancel button on the panel screen (known network present)
iwd.state, iwd.known = "disconnected", 1
hs.phase = "idle"
hs.start()
hs.tick()
assert KIOSK.mode == "hotspot"
r = local("/api/action", "POST", {"action": "hotspot_cancel"})
hs.tick()
assert hs.phase == "idle" and iwd.mode == "station"
print("cancel ok; screen mode now:", KIOSK.mode)
S.stop()
hs._stop_http()
print("SMOKE TEST PASSED")
