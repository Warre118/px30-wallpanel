"""Tests for the "Name and region" section of px30-panel (hostname, time zone,
Wi-Fi country): validation, what is written to /data and which commands run.
Uses a fake zoneinfo directory; no root, no hardware.

    python3 tests/test_region.py
"""
import os, sys, tempfile, types, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "rootfs/overlay/usr/local/sbin/px30-panel")
sys.dont_write_bytecode = True

ISO3166 = "# code\tname\nBE\tBelgium\nCH\tSwitzerland\nFR\tFrance\n"
ZONE_TAB = "# cc\tcoordinates\tTZ\nFR\t+4852+00220\tEurope/Paris\nBE\t+5050+00420\tEurope/Brussels\n"


def load(tmp):
    with open(SRC, encoding="utf-8") as f:
        src = f.read()
    for a, b in (('"/run/px30-panel"', repr(f"{tmp}/run")), ('"/data/px30-panel"', repr(f"{tmp}/data")),
                 ('"/data/kiosk/kiosk.conf"', repr(f"{tmp}/kiosk.conf")),
                 ('"/usr/share/px30-panel"', repr(os.path.join(ROOT, "rootfs/overlay/usr/share/px30-panel"))),
                 ('"/usr/local/lib/px30"', repr(os.path.join(ROOT, "rootfs/overlay/usr/local/lib/px30")))):
        assert a in src, a
        src = src.replace(a, b)
    g = {"__name__": "px30_panel_region_test"}
    exec(compile(src, "px30-panel", "exec"), g)
    zi = f"{tmp}/zoneinfo"
    os.makedirs(f"{zi}/Europe")
    for name, text in (("iso3166.tab", ISO3166), ("zone.tab", ZONE_TAB)):
        with open(f"{zi}/{name}", "w") as f:
            f.write(text)
    for z in ("Europe/Paris", "Europe/Brussels", "UTC"):
        open(f"{zi}/{z}", "w").close()
    with open(f"{tmp}/hosts", "w") as f:
        f.write("127.0.0.1\tlocalhost\n127.0.1.1\tpx30-panel\n")
    g.update(ZONEINFO=zi, HOSTNAME_FILE=f"{tmp}/data/hostname", TZ_FILE=f"{tmp}/data/timezone",
             COUNTRY_FILE=f"{tmp}/data/wifi-country", HOSTS_FILE=f"{tmp}/hosts")
    return g


class RegionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.g = load(self.tmp)
        self.calls = []
        self.host = "px30-panel"
        self.g["sh"] = lambda *cmd, timeout=30: (self.calls.append(list(cmd)), (0, ""))[1]
        self.g["socket"] = types.SimpleNamespace(gethostname=lambda: self.host)

    def read(self, name):
        with open(f"{self.tmp}/data/{name}") as f:
            return f.read().strip()

    def test_state_lists_and_mapping(self):
        st = self.g["region_state"]()
        self.assertEqual(st["hostname"], "px30-panel")
        self.assertIn("Europe/Paris", st["zones"])
        self.assertIn("UTC", st["zones"])
        self.assertEqual(st["zone_country"]["Europe/Paris"], "FR")
        self.assertIn(["FR", "France"], [list(c) for c in st["countries"]])
        self.assertFalse(st["timezone_set"])
        self.assertEqual(st["country"], "")

    def test_apply_all_three(self):
        msgs = self.g["region_apply"]({"hostname": "kitchen", "timezone": "Europe/Paris", "country": "FR"})
        self.assertEqual(self.read("hostname"), "kitchen")
        self.assertEqual(self.read("timezone"), "Europe/Paris")
        self.assertEqual(self.read("wifi-country"), "FR")
        self.assertIn(["hostnamectl", "set-hostname", "kitchen"], self.calls)
        self.assertIn(["timedatectl", "set-timezone", "Europe/Paris"], self.calls)
        self.assertIn(["iw", "reg", "set", "FR"], self.calls)
        with open(f"{self.tmp}/hosts") as f:
            self.assertIn("127.0.1.1\tkitchen", f.read())
        self.assertEqual(len(msgs), 3)

    def test_invalid_values_change_nothing(self):
        bad = ({"hostname": "Kitchen"}, {"hostname": "-x"}, {"hostname": "a" * 64}, {"hostname": "a_b"},
               {"timezone": "../../etc/passwd"}, {"timezone": "Europe/Nowhere"}, {"timezone": 5},
               {"country": "fr"}, {"country": "XX"}, {"country": "FRA"},
               # one bad field stops the good ones too
               {"hostname": "kitchen", "country": "XX"})
        for r in bad:
            with self.subTest(r=r):
                with self.assertRaises(self.g["HAError"]):
                    self.g["region_apply"](r)
        self.assertEqual(self.calls, [])
        self.assertFalse(os.path.exists(f"{self.tmp}/data/hostname"))

    def test_unchanged_values_do_nothing(self):
        self.g["region_apply"]({"timezone": "Europe/Paris", "country": "BE"})
        self.calls.clear()
        msgs = self.g["region_apply"]({"hostname": "px30-panel", "timezone": "Europe/Paris", "country": "BE"})
        self.assertEqual(msgs, [])
        self.assertEqual(self.calls, [])

    def test_timezone_saved_even_if_same_as_running(self):
        # first setup: the panel runs UTC and the phone also says UTC -> still recorded on /data
        msgs = self.g["region_apply"]({"timezone": "UTC"})
        self.assertEqual(self.read("timezone"), "UTC")
        self.assertEqual(len(msgs), 1)

    def test_command_failure_is_reported_not_fatal(self):
        self.g["sh"] = lambda *cmd, timeout=30: (1, "no iw")
        msgs = self.g["region_apply"]({"country": "FR"})
        self.assertEqual(self.read("wifi-country"), "FR")
        self.assertIn("next start", msgs[0])


if __name__ == "__main__":
    unittest.main()
