"""Tests for px30-radio's probe / use / off logic, with a fake chip and systemctl.
Runs on Linux (WSL) or Windows: python3 tests/test_radio.py"""
import io, json, os, subprocess, sys, tempfile, types, unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "rootfs/overlay/usr/local/sbin/px30-radio")

try:
    import fcntl  # noqa: F401
except ImportError:               # Windows: a no-op stand-in, enough for the lock
    sys.modules["fcntl"] = types.SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=lambda *a: None)


def load(tmp):
    g = {"__name__": "px30radio"}
    exec(compile(open(SRC, encoding="utf-8").read(), "px30-radio", "exec"), g)
    g["DATA"] = tmp
    g["STATE"] = f"{tmp}/state.json"
    g["PROBE"] = f"{tmp}/probe.json"
    g["LOCK"] = f"{tmp}/lock"
    g["USF"] = sys.executable          # "exists and is executable"; the run is faked
    return g


class Fake:
    """systemctl + universal-silabs-flasher."""
    def __init__(self, chip="SPINEL", active=()):
        self.chip = chip
        self.active = set(active)
        self.enabled = set(active)
        self.calls = []

    def run(self, argv, **kw):
        self.calls.append(list(argv))
        out = ""
        rc = 0
        if argv[0] == "systemctl":
            verb, unit = argv[1], argv[-1]
            if verb == "is-active":
                rc = 0 if unit in self.active else 3
            elif verb == "is-enabled":
                rc = 0 if unit in self.enabled else 1
            elif verb == "stop":
                self.active.discard(unit)
            elif verb == "start":
                self.active.add(unit)
            elif verb in ("enable", "disable"):
                (self.enabled.add if verb == "enable" else self.enabled.discard)(unit)
                if "--now" in argv:
                    (self.active.add if verb == "enable" else self.active.discard)(unit)
        elif "probe" in argv:
            # the UART must be free while probing
            assert not self.active, f"services still running during probe: {self.active}"
            if self.chip:
                out = f"INFO Detected ApplicationType.{self.chip}, version 'v-{self.chip}' (1.0) at 115200"
        return subprocess.CompletedProcess(argv, rc, out, "")


class RadioTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.g = load(self.tmp)

    def with_fake(self, fake):
        self.g["subprocess"] = types.SimpleNamespace(run=fake.run, CompletedProcess=subprocess.CompletedProcess,
                                                     TimeoutExpired=subprocess.TimeoutExpired)
        return fake

    def call(self, fn, *a):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.g[fn](*a)
        return buf.getvalue()

    def state(self):
        return json.load(open(f"{self.tmp}/state.json"))

    def test_probe_thread_restores_services(self):
        f = self.with_fake(Fake("SPINEL", active={"otbr-agent.service"}))
        res = self.g["probe"]()
        self.assertEqual(res["stack"], "thread")
        self.assertEqual(res["chip_version"], "v-SPINEL")
        self.assertIn("otbr-agent.service", f.active)          # running again afterwards
        self.assertEqual(json.load(open(f"{self.tmp}/probe.json"))["stack"], "thread")

    def test_probe_zigbee_and_bootloader(self):
        self.with_fake(Fake("EZSP"))
        self.assertEqual(self.g["probe"]()["stack"], "zigbee")
        self.with_fake(Fake("GECKO_BOOTLOADER"))
        self.assertEqual(self.g["probe"]()["stack"], "bootloader")
        self.with_fake(Fake(None))
        self.assertEqual(self.g["probe"]()["stack"], "unknown")

    def test_use_after_reset_probes_and_enables(self):
        # nothing recorded (factory reset): use thread asks the chip, then turns otbr on
        f = self.with_fake(Fake("SPINEL"))
        self.call("cmd_use", "thread", False, False)
        self.assertEqual(self.state()["stack"], "thread")
        self.assertTrue(self.state()["enabled"])
        self.assertIn("otbr-agent.service", f.enabled)
        self.assertNotIn("px30-zigbee-bridge.service", f.enabled)

    def test_use_other_stack_needs_flash(self):
        f = self.with_fake(Fake("SPINEL"))
        switched = []
        self.g["cmd_switch"] = lambda *a: switched.append(a)
        with self.assertRaises(SystemExit) as e:
            self.call("cmd_use", "zigbee", False, False)
        self.assertEqual(e.exception.code, 2)                 # refused without --flash
        self.assertEqual(switched, [])
        self.call("cmd_use", "zigbee", True, True)
        self.assertEqual(switched[0][0], "zigbee")
        # before flashing, the state knows the chip runs thread (cross-flash flag)
        self.assertEqual(self.state()["stack"], "thread")

    def test_off_and_back_on_without_probe(self):
        json.dump({"stack": "zigbee", "enabled": True}, open(f"{self.tmp}/state.json", "w", encoding="utf-8"))
        f = self.with_fake(Fake("EZSP", active={"px30-zigbee-bridge.service"}))
        self.call("cmd_use", "off", False, False)
        self.assertFalse(self.state()["enabled"])
        self.assertEqual(self.state()["stack"], "zigbee")      # the chip's stack is kept
        self.assertEqual(f.active, set())
        self.assertEqual(f.enabled, set())
        n = len(f.calls)
        self.call("cmd_use", "zigbee", False, False)
        self.assertTrue(self.state()["enabled"])
        self.assertEqual(f.active, {"px30-zigbee-bridge.service"})
        self.assertFalse(any("probe" in c for c in f.calls[n:]))  # known stack: no probe

    def test_apply_off_keeps_both_off(self):
        json.dump({"stack": "thread", "enabled": False}, open(f"{self.tmp}/state.json", "w", encoding="utf-8"))
        f = self.with_fake(Fake("SPINEL", active={"otbr-agent.service"}))
        self.call("cmd_apply")
        self.assertEqual(f.active, set())

    def test_status_reports_enabled_and_probe(self):
        json.dump({"stack": "thread", "enabled": False}, open(f"{self.tmp}/state.json", "w", encoding="utf-8"))
        json.dump({"stack": "thread", "chip_version": "x"}, open(f"{self.tmp}/probe.json", "w"))
        self.with_fake(Fake())
        s = json.loads(self.call("cmd_status", True))
        self.assertFalse(s["enabled"])
        self.assertEqual(s["probe"]["chip_version"], "x")


if __name__ == "__main__":
    unittest.main()
