"""Tests for `px30-update radio sync` against a local HTTP server, including the
catalog the update pipeline (updater/fwpipe) publishes.
Runs on Linux (WSL) or Windows: python3 tests/test_update.py"""
import base64, contextlib, functools, hashlib, http.server, io, json, os, shutil, subprocess, sys, tempfile
import threading, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "agent", "px30-update")


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.www = os.path.join(t, "www")
        os.makedirs(os.path.join(self.www, "px30-tyzs13"))
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=self.www))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.g = {"__name__": "px30update"}
        with open(SRC, encoding="utf-8") as f:
            exec(compile(f.read(), "px30-update", "exec"), self.g)
        self.g.update(ALLOW_HTTP=True, AGENT_CONFIG=os.path.join(t, "config.json"),
                      RADIO_DATA=os.path.join(t, "radio"), PUBKEY=os.path.join(t, "radio-update.pub"))
        self.set_url(f"{self.base}/px30-tyzs13/catalog.json")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def set_url(self, url):
        with open(self.g["AGENT_CONFIG"], "w") as f:
            json.dump({"update": {"radio_catalog_url": url}}, f)

    def put(self, rel, data):
        path = os.path.join(self.www, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def catalog(self, entries):
        self.put("px30-tyzs13/catalog.json", json.dumps({"firmware": entries}).encode())

    def entry(self, stack, channel, name, data, **kw):
        self.put(f"px30-tyzs13/fw/{name}", data)
        e = {"stack": stack, "channel": channel, "version": "1.0", "file": name,
             "url": f"{self.base}/px30-tyzs13/fw/{name}", "sha256": sha(data), "baudrate": 115200}
        e.update(kw)
        return e

    def sync(self):
        """(exit code, stdout JSON or None, stderr)"""
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                self.g["radio_sync"]()
            except SystemExit as e:
                code = e.code
        return code, (json.loads(out.getvalue()) if out.getvalue().strip() else None), err.getvalue()

    def radio(self, *parts):
        return os.path.join(self.g["RADIO_DATA"], *parts)

    def read(self, *parts):
        with open(self.radio(*parts), "rb") as f:
            return f.read()

    def test_downloads_then_is_quiet(self):
        self.catalog([self.entry("thread", "stable", "rcp-1.gbl", b"rcp one"),
                      self.entry("zigbee", "beta", "ncp-b.gbl", b"ncp beta", version="7.5.2.0"),
                      self.entry("router", "stable", "router.gbl", b"router")])     # not for this px30-radio
        code, res, err = self.sync()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(res, {"changed": True, "entries": 2, "downloaded": ["ncp-b.gbl", "rcp-1.gbl"]})
        self.assertEqual(self.read("firmware", "rcp-1.gbl"), b"rcp one")
        self.assertFalse(os.path.exists(self.radio("firmware", "router.gbl")))
        cat = json.loads(self.read("catalog.json"))
        self.assertEqual([(e["stack"], e["channel"], e["version"]) for e in cat["firmware"]],
                         [("thread", "stable", "1.0"), ("zigbee", "beta", "7.5.2.0")])
        self.assertNotIn("url", cat["firmware"][0])
        self.assertEqual(self.sync()[1], {"changed": False, "entries": 2, "downloaded": []})

    def test_new_build_replaces_and_removes_the_old_file(self):
        self.catalog([self.entry("thread", "stable", "rcp-1.gbl", b"one")])
        self.sync()
        self.catalog([self.entry("thread", "stable", "rcp-2.gbl", b"two", version="2.0")])
        self.assertEqual(self.sync()[1]["downloaded"], ["rcp-2.gbl"])
        self.assertEqual(os.listdir(self.radio("firmware")), ["rcp-2.gbl"])

    def test_wrong_checksum_changes_nothing(self):
        self.catalog([self.entry("thread", "stable", "rcp-1.gbl", b"good")])
        self.sync()
        before = self.read("catalog.json")
        self.catalog([self.entry("thread", "stable", "rcp-2.gbl", b"evil", sha256=sha(b"something else"))])
        code, res, err = self.sync()
        self.assertEqual(code, 1)
        self.assertIn("checksum", err)
        self.assertEqual(self.read("catalog.json"), before)
        self.assertEqual(os.listdir(self.radio("firmware")), ["rcp-1.gbl"])

    def test_refuses_other_host_bad_names_and_plain_http(self):
        e = self.entry("thread", "stable", "rcp-1.gbl", b"x")
        self.catalog([dict(e, url="http://198.51.100.7/rcp-1.gbl")])
        self.assertIn("another host", self.sync()[2])
        self.catalog([dict(e, file="../../etc/passwd.gbl")])
        self.assertIn("bad file name", self.sync()[2])
        self.catalog([e])
        self.g["ALLOW_HTTP"] = False
        code, _, err = self.sync()
        self.assertEqual(code, 1)
        self.assertIn("https only", err)
        self.assertFalse(os.path.exists(self.radio("catalog.json")))

    def test_not_configured(self):
        self.set_url("")
        self.assertEqual(self.sync()[0], 2)

    @unittest.skipUnless(shutil.which("openssl"), "openssl not installed")
    def test_signature_required_when_a_key_is_installed(self):
        key = os.path.join(self.tmp.name, "key.pem")
        subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", key],
                       check=True, capture_output=True)
        subprocess.run(["openssl", "ec", "-in", key, "-pubout", "-out", self.g["PUBKEY"]],
                       check=True, capture_output=True)
        self.catalog([self.entry("thread", "stable", "rcp-1.gbl", b"signed")])
        cat = os.path.join(self.www, "px30-tyzs13", "catalog.json")
        code, _, err = self.sync()                      # no .sig published
        self.assertEqual(code, 1)
        sig = os.path.join(self.tmp.name, "sig")
        subprocess.run(["openssl", "dgst", "-sha256", "-sign", key, "-out", sig, cat],
                       check=True, capture_output=True)
        with open(sig, "rb") as f:
            self.put("px30-tyzs13/catalog.json.sig", base64.b64encode(f.read()) + b"\n")
        self.assertEqual(self.sync()[0], 0)
        self.catalog([self.entry("thread", "stable", "rcp-9.gbl", b"tampered", version="9")])   # old signature
        code, _, err = self.sync()
        self.assertEqual(code, 1)
        self.assertIn("signature", err)
        self.assertEqual(os.listdir(self.radio("firmware")), ["rcp-1.gbl"])

    def test_catalog_published_by_the_pipeline(self):
        """fwpipe's Store writes the tree; the panel syncs from it; px30-radio reads the result."""
        sys.path.insert(0, os.path.join(ROOT, "updater"))
        try:
            from fwpipe.config import load
            from fwpipe.store import Stores
        except ImportError as e:
            self.skipTest(f"fwpipe not importable ({e})")
        cfg_path = os.path.join(self.tmp.name, "fwpipe.yaml")
        with open(cfg_path, "w") as f:
            f.write(f"""
publish: {{repo: ""}}
devices:
  px30-tyzs13:
    backend: gsdk
    publish: {{base_url: "{self.base}/px30-tyzs13"}}
    channels: {{stable: {{source: pinned, version: v4.4.6}}, latest: {{source: release}}}}
    firmware:
      zigbee_ncp: {{target: ncp}}
      openthread_rcp: {{target: ot-rcp}}
    outputs: [px30_catalog]
""")
        store = Stores(os.path.join(self.tmp.name, "repos"), load(cfg_path))
        art = os.path.join(self.tmp.name, "a.gbl")
        for fw, ch, version, body in (("openthread_rcp", "stable", "gsdk-4.4.6", b"rcp446"),
                                      ("openthread_rcp", "latest", "gsdk-4.5.1", b"rcp451"),
                                      ("zigbee_ncp", "stable", "7.5.0.0", b"ncp750")):
            with open(art, "wb") as f:
                f.write(body)
            store.publish("px30-tyzs13", fw, ch, art, {"version": version, "baudrate": 115200,
                                                       "description": f"{fw} {version}"})
        store.write_indexes()
        shutil.rmtree(os.path.join(self.www, "px30-tyzs13"))
        shutil.copytree(os.path.join(self.tmp.name, "repos", "local_px30-tyzs13"), os.path.join(self.www, "px30-tyzs13"))
        code, res, err = self.sync()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(res["entries"], 3)
        # px30-radio's own catalog reader, pointed at the synced data
        g = {"__name__": "px30radio"}
        try:
            import fcntl  # noqa: F401
        except ImportError:
            import types
            sys.modules["fcntl"] = types.SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=lambda *a: None)
        with open(os.path.join(ROOT, "rootfs/overlay/usr/local/sbin/px30-radio"), encoding="utf-8") as f:
            exec(compile(f.read(), "px30-radio", "exec"), g)
        g["DATA"] = self.g["RADIO_DATA"]
        g["SHARE"] = os.path.join(self.tmp.name, "no-share")
        cat = g["catalog"]()
        e = cat[("thread", "latest")]
        self.assertEqual(e["version"], "gsdk-4.5.1")
        self.assertEqual(g["sha256"](e["path"]), e["sha256"])
        with open(e["path"], "rb") as f:
            self.assertEqual(f.read(), b"rcp451")
        self.assertEqual(cat[("zigbee", "stable")]["version"], "7.5.0.0")


if __name__ == "__main__":
    unittest.main(verbosity=1)
