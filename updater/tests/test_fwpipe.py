"""Tests for fwpipe: channel resolution, overlays, GBL metadata, and the
check -> build -> archive -> publish pass with a fake upstream and fake builds.
No network, no Docker.   python3 updater/tests/test_fwpipe.py
"""
import base64, io, json, os, shutil, struct, subprocess, sys, tempfile, unittest, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import yaml  # noqa: E402

from fwpipe import gbl, overlay, sources  # noqa: E402
from fwpipe.build import BuildError, Plan, Result  # noqa: E402
from fwpipe.config import load  # noqa: E402
from fwpipe.pipeline import Pipeline  # noqa: E402
from fwpipe.store import Stores  # noqa: E402
from fwpipe.util import load_json, text_hash  # noqa: E402

UPDATER = os.path.dirname(HERE)

# Trimmed copies of upstream manifests (NabuCasa/silabs-firmware-builder, Apache-2.0):
# the Yellow NCP at main (Simplicity SDK) and at release v2026.02.23 (Gecko SDK).
YELLOW_NCP_MAIN = """
name: Yellow Zigbee
device: MGM210PA32JIA
base_project: src/zigbee_ncp
sdk: "simplicity_sdk:2026.6.1"
gbl: {fw_type: zigbee_ncp, ezsp_version: dynamic, baudrate: 115200}
sdk_extension:
  - {id: xncp, vendor: nabucasa, version: 1.0.0}
  - {id: board_activity, vendor: nabucasa, version: 1.0.0}
add_components:
- id: simple_led
  instance: [board_activity]
- {id: board_activity_led, vendor: nabucasa, package: board_activity}
- {id: xncp_core, vendor: nabucasa, package: xncp}
configuration:
  SL_CLOCK_MANAGER_PCLK_DIVIDER: SL_CLOCK_MANAGER_PCLK_DIV_MIN
c_defines:
  XNCP_MFG_MANUF_NAME: '"Nabu Casa"'
  XNCP_MFG_BOARD_NAME: '"Home Assistant Yellow"'
  SL_ZIGBEE_CHILD_TABLE_SIZE: {type: c_flag, value: 32}
  SL_IOSTREAM_USART_VCOM_BAUDRATE: 115200
  SL_IOSTREAM_USART_VCOM_FLOW_CONTROL_TYPE: usartHwFlowControlCtsAndRts
  SL_IOSTREAM_USART_VCOM_TX_PORT: SL_GPIO_PORT_A
  SL_IOSTREAM_USART_VCOM_TX_PIN: 5
  SL_IOSTREAM_USART_VCOM_RX_PORT: SL_GPIO_PORT_A
  SL_IOSTREAM_USART_VCOM_RX_PIN: 6
  SL_IOSTREAM_USART_VCOM_CTS_PORT: SL_GPIO_PORT_D
  SL_IOSTREAM_USART_VCOM_CTS_PIN: 2
  SL_IOSTREAM_USART_VCOM_RTS_PORT: SL_GPIO_PORT_C
  SL_IOSTREAM_USART_VCOM_RTS_PIN: 1
  SL_SIMPLE_LED_BOARD_ACTIVITY_PORT: SL_GPIO_PORT_B
  SL_SIMPLE_LED_BOARD_ACTIVITY_PIN: 1
  BOARD_ACTIVITY_LED_INSTANCE: sl_led_board_activity
"""
YELLOW_NCP_GECKO = YELLOW_NCP_MAIN.replace("simplicity_sdk:2026.6.1", "gecko_sdk:4.5.0") \
    .replace("SL_GPIO_PORT_", "gpioPort").replace("configuration:\n  SL_CLOCK_MANAGER_PCLK_DIVIDER: SL_CLOCK_MANAGER_PCLK_DIV_MIN\n", "")


def read_overlay(name):
    with open(os.path.join(UPDATER, "devices", "tubeszb-mgm210", name), encoding="utf-8") as f:
        return yaml.safe_load(f)


class OverlayTest(unittest.TestCase):
    def test_tubeszb_ncp_on_simplicity_sdk(self):
        m = overlay.apply(yaml.safe_load(YELLOW_NCP_MAIN), read_overlay("zigbee_ncp.yaml"))
        d = m["c_defines"]
        self.assertEqual(m["name"], "TubesZB MGM210 2022 Zigbee")
        self.assertEqual((d["SL_IOSTREAM_USART_VCOM_TX_PIN"], d["SL_IOSTREAM_USART_VCOM_RX_PIN"]), (6, 5))
        self.assertEqual(d["SL_IOSTREAM_USART_VCOM_CTS_PORT"], "SL_GPIO_PORT_C")
        self.assertEqual(d["SL_IOSTREAM_USART_VCOM_FLOW_CONTROL_TYPE"], "usartHwFlowControlNone")
        self.assertEqual(m["configuration"]["SL_IOSTREAM_USART_VCOM_FLOW_CONTROL_TYPE"], "usartHwFlowControlNone")
        self.assertEqual(m["configuration"]["SL_CLOCK_MANAGER_PCLK_DIVIDER"], "SL_CLOCK_MANAGER_PCLK_DIV_MIN")
        self.assertEqual([c["id"] for c in m["add_components"]], ["xncp_core"])
        self.assertEqual([e["id"] for e in m["sdk_extension"]], ["xncp"])
        self.assertFalse([k for k in d if "LED" in k])
        self.assertEqual(d["XNCP_MFG_MANUF_NAME"], '"TubesZB"')
        self.assertEqual(d["SL_ZIGBEE_CHILD_TABLE_SIZE"], {"type": "c_flag", "value": 32})   # untouched
        self.assertEqual(m["gbl"]["fw_variant"], "sw_flow")
        self.assertEqual(m["gbl"]["fw_type"], "zigbee_ncp")

    def test_port_notation_follows_upstream(self):
        m = overlay.apply(yaml.safe_load(YELLOW_NCP_GECKO), read_overlay("zigbee_ncp.yaml"))
        self.assertEqual(m["c_defines"]["SL_IOSTREAM_USART_VCOM_TX_PORT"], "gpioPortA")
        self.assertEqual(m["c_defines"]["SL_IOSTREAM_USART_VCOM_RTS_PORT"], "gpioPortD")
        self.assertEqual(m["sdk"], "gecko_sdk:4.5.0")

    def test_set_matching_must_match(self):
        with self.assertRaises(overlay.OverlayError):
            overlay.apply({"c_defines": {"A": 1}}, {"set_matching": {"NOT_THERE": 2}})
        with self.assertRaises(overlay.OverlayError):
            overlay.apply({}, {"typo_key": 1})

    def test_null_deletes_and_sdk_version(self):
        m = overlay.apply({"configuration": {"X": 1, "Y": 2}, "sdk": "simplicity_sdk:2026.6.1"},
                          {"set": {"configuration": {"X": None}}})
        self.assertEqual(m["configuration"], {"Y": 2})
        self.assertEqual(overlay.set_sdk_version(m, "2026.6.2")["sdk"], "simplicity_sdk:2026.6.2")

    def test_all_overlays_parse(self):
        for name in ("zigbee_ncp.yaml", "openthread_rcp.yaml", "zigbee_router.yaml"):
            self.assertIsInstance(read_overlay(name), dict)


def make_gbl(meta=None):
    def tag(i, body=b""):
        return struct.pack("<II", i, len(body)) + body
    data = tag(gbl.TAG_HEADER, b"\0" * 8) + tag(0xFE0101FE, b"prog")
    if meta is not None:
        data += tag(gbl.TAG_METADATA, json.dumps(meta).encode())
    return data + tag(gbl.TAG_END, b"\0\0\0\0")


class GblTest(unittest.TestCase):
    def test_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "a.gbl")
            with open(p, "wb") as f:
                f.write(make_gbl({"fw_type": "zigbee_ncp", "ezsp_version": "9.1.1.1", "baudrate": 115200}))
            meta = gbl.read_metadata(p)
            self.assertEqual(gbl.firmware_version("zigbee_ncp", meta), "9.1.1.1")
            with open(p, "wb") as f:
                f.write(make_gbl())
            self.assertEqual(gbl.read_metadata(p), {})
            with open(p, "wb") as f:
                f.write(b"not a gbl file at all")
            with self.assertRaises(ValueError):
                gbl.read_metadata(p)
        self.assertEqual(gbl.firmware_version(
            "openthread_rcp", {"ot_rcp_version": "SL-OPENTHREAD/2.5.2.0_GitHub-1fceb225b"}), "2.5.2.0")


class FakeGitHub(sources.GitHub):
    """Answers from a dict {url suffix: python object or text}."""

    def __init__(self, data):
        super().__init__(token="")
        self.data = data

    def _get(self, url, accept):
        for key, val in self.data.items():
            if url.endswith(key) or (key.endswith("/") and key in url):
                return val.encode() if isinstance(val, str) else json.dumps(val).encode()
        raise sources.SourceError(f"GET {url}: HTTP 404")


def rel(tag, when, pre=False):
    return {"tag_name": tag, "published_at": when, "prerelease": pre, "draft": False,
            "html_url": f"https://example/{tag}"}


class SourcesTest(unittest.TestCase):
    def gh(self, rels):
        data = {"/releases?per_page=50": rels}
        for r in rels:
            data[f'/commits/{r["tag_name"]}'] = {"sha": "c0ffee" + r["tag_name"]}
        data["/commits/main"] = {"sha": "abcdef0123456789"}
        return FakeGitHub(data)

    def test_sfb_channels(self):
        gh = self.gh([rel("v2026.09.17-beta2", "2026-09-17", True), rel("v2026.02.23", "2026-02-24"),
                      rel("v2026.01.11-beta2", "2026-01-11", True), rel("v2025.11.24", "2025-11-24")])
        self.assertEqual(sources.resolve_sfb(gh, "o/r", {"source": "release"}).label, "v2026.02.23")
        self.assertEqual(sources.resolve_sfb(gh, "o/r", {"source": "prerelease"}).label, "v2026.09.17-beta2")
        s = sources.resolve_sfb(gh, "o/r", {"source": "branch", "branch": "main"})
        self.assertEqual((s.label, s.commit), ("main", "abcdef0123456789"))

    def test_no_beta_when_release_is_newer(self):
        gh = self.gh([rel("v2", "2026-03-01"), rel("v2-beta1", "2026-02-01", True)])
        self.assertIsNone(sources.resolve_sfb(gh, "o/r", {"source": "prerelease"}))

    def test_gsdk_picks_highest_version_not_newest_date(self):
        gh = self.gh([rel("v4.2.7", "2025-01-15"), rel("v4.4.5", "2024-10-25"), rel("v4.5.1", "2026-08-20"),
                      rel("v4.4.6", "2025-02-28")])
        self.assertEqual(sources.resolve_gsdk(gh, "o/r", {"source": "release", "series": "4."}).version, "4.5.1")
        self.assertIsNone(sources.resolve_gsdk(gh, "o/r", {"source": "prerelease", "series": "4."}))
        self.assertEqual(sources.resolve_gsdk(gh, "o/r", {"source": "pinned", "version": "v4.4.6"}).version, "4.4.6")

    def test_newest_sdk_in_dockerfile(self):
        self.assertEqual(sources.newest_sdk_in_dockerfile('ARG SDK_VERSIONS="2026.6.1 2026.6.2"\n'), "2026.6.2")
        self.assertEqual(sources.newest_sdk_in_dockerfile("ARG SDK_VERSION=2026.6.1\n"), "2026.6.1")
        self.assertIsNone(sources.newest_sdk_in_dockerfile("FROM debian\n"))


CONFIG = """
publish: {repo: "", keep_archived: 2}
devices:
  tubeszb-mgm210:
    title: TubesZB
    backend: sfb
    publish: {base_url: "https://fw.example/tzb"}
    outputs: [efr32_flasher]
    channels:
      stable: {source: release}
      beta: {source: prerelease}
      latest: {source: branch, branch: main}
    firmware:
      zigbee_ncp: {base: b.yaml, overlay: o.yaml}
  px30-tyzs13:
    title: Panel
    backend: gsdk
    publish: {base_url: "https://fw.example/panel"}
    channels:
      stable: {source: pinned, version: v4.4.6}
      latest: {source: release}
    firmware:
      zigbee_ncp: {target: ncp, description: ncp}
      openthread_rcp: {target: ot-rcp, description: rcp}
    outputs: [px30_catalog]
"""


class FakeBackend:
    """A build is a GBL whose version comes from `versions[source label]`."""

    def __init__(self, versions, fail=()):
        self.versions, self.fail, self.builds = versions, set(fail), []

    def plan(self, device, fw, spec, chspec, src):
        return Plan(text_hash(device, fw, src.label, src.commit, src.version, self.versions.get("_recipe", "")),
                    text_hash(self.versions.get("_recipe", "")), sdk="sdk:x", data={"src": src})

    def build(self, device, fw, spec, plan, work):
        src = plan.data["src"]
        self.builds.append((device, fw, src.label))
        if src.label in self.fail:
            raise BuildError("compiler exploded")
        version = self.versions[src.label]
        path = os.path.join(work, "x.gbl")
        with open(path, "wb") as f:
            f.write(make_gbl({"v": version, "fw": fw}))
        return Result(path, version, {"baudrate": 115200, "flow_control": "none", "sdk": plan.sdk,
                                      "description": spec.get("description", ""),
                                      "source": {"repo": src.repo, "ref": src.label, "commit": src.commit}})


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        with open(os.path.join(t, "config.yaml"), "w") as f:
            f.write(CONFIG)
        self.cfg = load(os.path.join(t, "config.yaml"))
        self.rels = [rel("v1", "2026-01-01")]
        self.gsdk = [rel("v4.4.6", "2025-02-28")]
        self.main = "aaaa"
        self.versions = {"v1": "7.5.0.0", "main": "9.1.1.1", "v4.4.6": "7.5.0.0"}
        self.compare = {"commits": []}

    def tearDown(self):
        self.tmp.cleanup()

    def pipeline(self, fail=()):
        data = {"NabuCasa/silabs-firmware-builder/releases?per_page=50": self.rels,
                "SiliconLabs/gecko_sdk/releases?per_page=50": self.gsdk,
                "/commits/main": {"sha": self.main}, "/compare/": self.compare}
        for r in self.rels:
            data[f'/commits/{r["tag_name"]}'] = {"sha": "sha-" + r["tag_name"]}
        t = self.tmp.name
        self.backend = FakeBackend(self.versions, fail)
        store = Stores(os.path.join(t, "repos"), self.cfg)
        return Pipeline(self.cfg, FakeGitHub(data), {"sfb": self.backend, "gsdk": self.backend}, store,
                        os.path.join(t, "state"), os.path.join(t, "work"))

    def root(self, device, *parts):
        return os.path.join(self.tmp.name, "repos", f"local_{device}", *parts)

    def man(self, device, *parts):
        return load_json(self.root(device, *parts, "manifest.json"))

    def actions(self, p):
        return {(r["device"], r["firmware"], r["channel"]): r["action"] for r in p.report}

    def test_first_run_publishes_and_second_is_quiet(self):
        p = self.pipeline()
        changes = p.run()
        # tubeszb stable+latest, panel 2 firmware x (stable, latest = same pinned build)
        self.assertEqual(len(changes), 6)
        self.assertEqual(self.actions(p)[("tubeszb-mgm210", "zigbee_ncp", "beta")], "none")
        m = self.man("tubeszb-mgm210", "zigbee_ncp", "latest")
        self.assertEqual(m["version"], "9.1.1.1")
        self.assertEqual(m["url"], "https://fw.example/tzb/zigbee_ncp/latest/" + m["file"])
        self.assertTrue(os.path.exists(self.root("tubeszb-mgm210", "zigbee_ncp",
                                                    "latest", m["file"])))
        flat = load_json(self.root("tubeszb-mgm210", "zigbee_ncp", "latest",
                                      "efr32_flasher.json"))
        self.assertEqual((flat["version"], flat["fw_url"], flat["md5"]), (m["version"], m["url"], m["md5"]))
        # the panel: one build serves stable and latest; catalog in px30-radio's format
        self.assertEqual(sum(1 for b in self.backend.builds if b[0] == "px30-tyzs13"), 2)
        cat = load_json(self.root("px30-tyzs13", "catalog.json"))
        self.assertEqual(sorted((e["stack"], e["channel"]) for e in cat["firmware"]),
                         [("thread", "latest"), ("thread", "stable"), ("zigbee", "latest"), ("zigbee", "stable")])
        self.assertTrue(all(e["url"].startswith("https://fw.example/panel/") for e in cat["firmware"]))

        p2 = self.pipeline()
        self.assertEqual(p2.run(), [])
        self.assertEqual(self.backend.builds, [])
        self.assertEqual(set(self.actions(p2).values()), {"up-to-date", "none"})

    def test_new_version_archives_the_old_one(self):
        self.pipeline().run()
        self.main, self.versions["main"] = "bbbb", "9.1.2.1"
        p = self.pipeline()
        changes = p.run()
        self.assertEqual(changes, ["tubeszb-mgm210 zigbee_ncp latest: 9.1.2.1 (was 9.1.1.1)"])
        m = self.man("tubeszb-mgm210", "zigbee_ncp", "latest")
        self.assertEqual((m["version"], m["previous"]["version"]), ("9.1.2.1", "9.1.1.1"))
        arch = self.root("tubeszb-mgm210", *m["previous"]["archive"].split("/"))
        old = load_json(os.path.join(arch, "manifest.json"))
        self.assertEqual(old["version"], "9.1.1.1")
        self.assertTrue(os.path.exists(os.path.join(arch, old["file"])))
        self.assertIn("/archive/", old["url"])
        live = os.listdir(self.root("tubeszb-mgm210", "zigbee_ncp", "latest"))
        self.assertEqual(sorted(live), sorted([m["file"], "manifest.json", "efr32_flasher.json", "RELEASE_NOTES.md"]))
        # stable was not touched
        self.assertEqual(self.man("tubeszb-mgm210", "zigbee_ncp", "stable")["version"], "7.5.0.0")

    def test_upstream_moves_but_same_version_is_not_republished(self):
        self.pipeline().run()
        self.main = "cccc"
        p = self.pipeline()
        self.assertEqual(p.run(), [])
        self.assertEqual(self.actions(p)[("tubeszb-mgm210", "zigbee_ncp", "latest")], "unchanged")
        self.assertEqual(len(self.backend.builds), 1)        # built once to learn the version
        p3 = self.pipeline()
        p3.run()
        self.assertEqual(self.backend.builds, [])            # and not again

    def test_our_change_with_same_version_gets_a_revision(self):
        self.pipeline().run()
        self.versions["_recipe"] = "pinout fixed"
        self.pipeline().run()
        m = self.man("tubeszb-mgm210", "zigbee_ncp", "stable")
        self.assertEqual((m["version"], m["stack_version"], m["revision"]), ("7.5.0.0-r2", "7.5.0.0", 2))

    def test_beta_appears_and_is_retired(self):
        self.pipeline().run()
        self.rels.append(rel("v2-beta1", "2026-05-01", True))
        self.versions["v2-beta1"] = "9.0.0.0"
        self.assertEqual(self.pipeline().run(), ["tubeszb-mgm210 zigbee_ncp beta: 9.0.0.0"])
        # the beta becomes the release: stable moves up, beta disappears into the archive
        self.rels.append(rel("v2", "2026-06-01"))
        self.versions["v2"] = "9.0.0.0"
        changes = self.pipeline().run()
        self.assertIn("tubeszb-mgm210 zigbee_ncp stable: 9.0.0.0 (was 7.5.0.0)", changes)
        self.assertTrue(any("beta: removed" in c for c in changes))
        self.assertIsNone(self.man("tubeszb-mgm210", "zigbee_ncp", "beta"))
        self.assertTrue(os.listdir(self.root("tubeszb-mgm210", "archive",
                                                "zigbee_ncp", "beta")))

    def test_failed_build_keeps_channel_and_does_not_stop_the_rest(self):
        self.pipeline().run()
        self.main, self.versions["main"] = "dddd", "9.9.9.9"
        self.gsdk.append(rel("v4.5.1", "2026-08-20"))
        self.versions["v4.5.1"] = "7.5.2.0"
        p = self.pipeline(fail={"main"})
        changes = p.run()
        self.assertEqual(self.man("tubeszb-mgm210", "zigbee_ncp", "latest")["version"], "9.1.1.1")
        self.assertEqual(self.actions(p)[("tubeszb-mgm210", "zigbee_ncp", "latest")], "build-failed")
        self.assertEqual(len(p.failures), 1)
        self.assertEqual(sorted(changes), ["px30-tyzs13 openthread_rcp latest: 7.5.2.0 (was 7.5.0.0)",
                                           "px30-tyzs13 zigbee_ncp latest: 7.5.2.0 (was 7.5.0.0)"])
        self.assertEqual(self.man("px30-tyzs13", "zigbee_ncp", "stable")["version"], "7.5.0.0")   # pinned
        # not retried right away
        p2 = self.pipeline(fail={"main"})
        p2.run()
        self.assertEqual(self.backend.builds, [])
        self.assertEqual(self.actions(p2)[("tubeszb-mgm210", "zigbee_ncp", "latest")], "failed-earlier")

    def test_check_writes_nothing(self):
        p = self.pipeline()
        self.assertEqual(p.run(dry_run=True), [])
        self.assertEqual(self.actions(p)[("tubeszb-mgm210", "zigbee_ncp", "stable")], "build")
        self.assertFalse(os.path.exists(self.root("tubeszb-mgm210", "index.json")))
        self.assertEqual(self.backend.builds, [])

    def test_archive_is_pruned(self):
        self.pipeline().run()
        for i in range(4):
            self.main, self.versions["main"] = f"m{i}", f"9.2.{i}.0"
            self.pipeline().run()
        arch = self.root("tubeszb-mgm210", "archive", "zigbee_ncp", "latest")
        self.assertEqual(len(os.listdir(arch)), 2)           # keep_archived: 2

    def test_release_notes(self):
        self.rels[0]["body"] = "## What's changed\n* Bump EmberZNet"
        self.pipeline().run()
        m = self.man("tubeszb-mgm210", "zigbee_ncp", "stable")
        self.assertIn("7.5.0.0", m["release_summary"])
        self.assertLessEqual(len(m["release_summary"]), 255)
        with open(self.root("tubeszb-mgm210", "zigbee_ncp", "stable", "RELEASE_NOTES.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Bump EmberZNet", text)
        self.assertIn("channel **stable**", text)
        # a branch channel lists the commits since the previous build
        self.main, self.versions["main"] = "bbbb", "9.1.2.1"
        self.compare = {"commits": [{"sha": "bbbb000", "commit": {"message": "Fix the thing\n\nlong"}}]}
        self.pipeline().run()
        m = self.man("tubeszb-mgm210", "zigbee_ncp", "latest")
        self.assertIn("was 9.1.1.1", m["release_summary"])
        with open(self.root("tubeszb-mgm210", "zigbee_ncp", "latest", "RELEASE_NOTES.md"), encoding="utf-8") as f:
            self.assertIn("- Fix the thing (bbbb000)", f.read())

    @unittest.skipUnless(shutil.which("openssl"), "openssl not installed")
    def test_manifests_are_signed(self):
        key = os.path.join(self.tmp.name, "key.pem")
        pub = os.path.join(self.tmp.name, "pub.pem")
        subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", key], check=True)
        subprocess.run(["openssl", "ec", "-in", key, "-pubout", "-out", pub], check=True, capture_output=True)
        os.environ["FWPIPE_SIGNING_KEY"] = key
        try:
            self.pipeline().run()
        finally:
            del os.environ["FWPIPE_SIGNING_KEY"]
        for path in (self.root("tubeszb-mgm210", "zigbee_ncp", "stable", "manifest.json"),
                     self.root("px30-tyzs13", "catalog.json")):
            with open(path + ".sig") as f:
                der = base64.b64decode(f.read())
            sig = os.path.join(self.tmp.name, "sig.der")
            with open(sig, "wb") as f:
                f.write(der)
            r = subprocess.run(["openssl", "dgst", "-sha256", "-verify", pub, "-signature", sig, path],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_real_config_loads(self):
        cfg = load(os.path.join(UPDATER, "config.yaml"))
        self.assertIn("tubeszb-mgm210", cfg["devices"])
        from fwpipe.config import device_publish
        self.assertEqual(device_publish(cfg, "tubeszb-mgm210")["base_url"],
                         "https://raw.githubusercontent.com/Warre118/TubesZB_MGM210_Warre_Edition/firmware")
        self.assertEqual(device_publish(cfg, "px30-tyzs13")["repo"], "Warre118/px30-wallpanel")
        self.assertFalse(cfg["devices"]["tubeszb-mgm210"]["firmware"]["zigbee_router"]["enabled"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
