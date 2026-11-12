"""The published firmware trees. Every device publishes into its own repository
(publish.repo / publish.branch / publish.path of the device); devices that share
a repository and branch share one checkout. Without a repository the tree only
exists locally (serve it with `fwpipe serve`).

Layout under the device's path in its repository:

  README.md, index.json                        what every channel holds
  catalog.json (+ .sig)                        px30-radio catalog (output px30_catalog)
  <firmware>/<channel>/
      <device>_<firmware>_<version>.gbl        the current build of this channel
      manifest.json (+ .sig)                   version, url, sha256, size, release notes, ...
      RELEASE_NOTES.md                         what changed, with the upstream notes
      efr32_flasher.json                       tube0013's efr32_flasher format (output efr32_flasher)
  archive/<firmware>/<channel>/<version>_<time>/   earlier builds

When a channel gets a new build, the current one is moved to archive/ first.
Signatures: ECDSA P-256 over SHA-256, DER, base64, in <file>.sig. Verified by
the panel (openssl) and by the TubesZB's ESP32 (mbedTLS) before anything is used.
"""
import base64, os, shutil, time

from .config import PX30_STACK, device_publish
from .util import file_hash, load_json, log, now_iso, run, safe_name, save_json, version_key

SCHEMA = 2
SUMMARY_MAX = 250            # Home Assistant's release_summary limit is 255
CHANNEL_ORDER = ("stable", "latest", "beta")


class Store:
    """One checkout (repository + branch) with one or more devices in it."""

    def __init__(self, root, cfg, devices=None, pub=None):
        self.root = root
        self.cfg = cfg
        self.devices = list(devices if devices is not None else cfg["devices"])
        self.pub = pub or device_publish(cfg, self.devices[0])
        self.changes = []          # human-readable lines, for the commit message / webhook

    # ------------------------------------------------------------ git
    def _git(self, *args, check=True, capture=False):
        argv = ["git", "-C", self.root]
        token = os.environ.get("GITHUB_TOKEN", "")
        if token and args and args[0] in ("clone", "fetch", "pull", "push", "ls-remote"):
            basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            argv += ["-c", f"http.https://github.com/.extraheader=Authorization: Basic {basic}"]
        return run(argv + list(args), check=check, capture=capture)

    def open(self):
        """Clone or update the checkout (no-op without a repository). A branch that
        does not exist yet starts empty (an orphan branch: no source code in it)."""
        os.makedirs(self.root, exist_ok=True)
        repo, branch = self.pub["repo"], self.pub["branch"]
        if not repo:
            return
        if not os.path.isdir(os.path.join(self.root, ".git")):
            if os.listdir(self.root):
                raise RuntimeError(f"{self.root} is not empty and not a git checkout")
            self._git("init", "-q", "-b", branch)
            self._git("remote", "add", "origin", f"https://github.com/{repo}.git")
            self._git("config", "user.name", self.pub.get("git_name", "fwpipe"))
            self._git("config", "user.email", self.pub.get("git_email", "fwpipe@localhost"))
        if self._git("fetch", "-q", "--depth", "1", "origin", branch, check=False, capture=True).returncode == 0:
            self._git("checkout", "-q", "-B", branch, "FETCH_HEAD")
            self._git("reset", "-q", "--hard", "FETCH_HEAD")
            self._git("clean", "-q", "-fdx")
        else:
            log(f"store: {repo} has no branch {branch} yet (the first publish creates it)")

    def commit_push(self, push=True):
        if not self.pub["repo"] or not self.changes:
            return False
        self._git("add", "-A")
        if self._git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return False
        subject = self.changes[0] if len(self.changes) == 1 else f"{len(self.changes)} firmware changes"
        self._git("commit", "-q", "-m", subject + "\n\n" + "\n".join(self.changes))
        if push:
            self._git("push", "-q", "origin", f'HEAD:refs/heads/{self.pub["branch"]}')
        return True

    # ------------------------------------------------------------ paths
    def _pub(self, device):
        return device_publish(self.cfg, device)

    def device_dir(self, device):
        path = self._pub(device)["path"]
        return os.path.join(self.root, *path.split("/")) if path else self.root

    def live_dir(self, device, fw, channel):
        return os.path.join(self.device_dir(device), fw, channel)

    def url(self, device, *parts):
        base = self._pub(device)["base_url"]
        rel = "/".join(parts)
        return f"{base}/{rel}" if base else rel

    def page_url(self, device, *parts):
        """Human-readable GitHub page of a file in the tree (release notes link)."""
        pub = self._pub(device)
        if not pub["repo"]:
            return ""
        rel = "/".join(p for p in (pub["path"], *parts) if p)
        return f'https://github.com/{pub["repo"]}/blob/{pub["branch"]}/{rel}'

    def current(self, device, fw, channel):
        return load_json(os.path.join(self.live_dir(device, fw, channel), "manifest.json"))

    # ------------------------------------------------------------ changes
    def archive(self, device, fw, channel):
        """Move the channel's current build to archive/. Returns the archive path (relative) or None."""
        live = self.live_dir(device, fw, channel)
        cur = self.current(device, fw, channel)
        if not cur:
            if os.path.isdir(live):
                shutil.rmtree(live)
            return None
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        rel = "/".join(["archive", fw, channel, f'{safe_name(cur.get("version", "unknown"))}_{stamp}'])
        dst = os.path.join(self.device_dir(device), *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(live, dst)
        cur["archived"] = now_iso()
        cur["url"] = self.url(device, rel, cur["file"])
        save_json(os.path.join(dst, "manifest.json"), cur)
        for extra in ("efr32_flasher.json", "manifest.json.sig"):
            p = os.path.join(dst, extra)
            if os.path.exists(p):
                os.remove(p)        # an archived build is no longer offered to devices
        self._prune(device, fw, channel)
        return rel

    def _prune(self, device, fw, channel):
        keep = int(self._pub(device).get("keep_archived", 10))
        base = os.path.join(self.device_dir(device), "archive", fw, channel)
        if keep <= 0 or not os.path.isdir(base):
            return
        entries = sorted(os.listdir(base), key=lambda n: n.rsplit("_", 1)[-1])
        for old in entries[:-keep]:
            shutil.rmtree(os.path.join(base, old))

    def publish(self, device, fw, channel, artifact, info):
        """Archive the channel's current build and put the new one in its place."""
        dev = self.cfg["devices"][device]
        prev = self.current(device, fw, channel)
        archived = self.archive(device, fw, channel)
        live = self.live_dir(device, fw, channel)
        os.makedirs(live, exist_ok=True)
        fname = f'{safe_name(device)}_{safe_name(fw)}_{safe_name(info["version"])}.gbl'
        path = os.path.join(live, fname)
        shutil.copyfile(artifact, path)
        notes = info.get("notes") or ""
        if notes:
            with open(os.path.join(live, "RELEASE_NOTES.md"), "w", encoding="utf-8", newline="\n") as f:
                f.write(notes.rstrip() + "\n")
        summary = (info.get("summary") or "").strip()
        if len(summary) > SUMMARY_MAX:
            summary = summary[:SUMMARY_MAX - 1].rstrip() + "…"
        man = {
            "schema": SCHEMA, "device": device, "firmware": fw, "channel": channel,
            "version": info["version"], "file": fname,
            "url": self.url(device, fw, channel, fname),
            "sha256": file_hash(path), "md5": file_hash(path, "md5"), "size": os.path.getsize(path),
            "baudrate": info.get("baudrate"), "flow_control": info.get("flow_control"),
            "bootloader_baudrate": info.get("bootloader_baudrate", 115200),
            "sdk": info.get("sdk"), "description": info.get("description", ""),
            "release_summary": summary,
            "release_url": (self.page_url(device, fw, channel, "RELEASE_NOTES.md") if notes else "")
                           or (info.get("source") or {}).get("url", ""),
            "experimental": bool(info.get("experimental")),
            "source": info.get("source"), "fingerprint": info.get("fingerprint"),
            "config_hash": info.get("config_hash"), "built": info.get("built") or now_iso(),
            "published": now_iso(),
        }
        for k in ("stack_version", "revision"):
            if k in info:
                man[k] = info[k]
        if prev:
            man["previous"] = {"version": prev.get("version"), "archive": archived}
        self.write_manifest(device, fw, channel, man)
        if "efr32_flasher" in (dev.get("outputs") or []):
            # tube0013/esphome-components efr32_flasher ("flat" manifest)
            save_json(os.path.join(live, "efr32_flasher.json"),
                      {"version": man["version"], "fw_url": man["url"], "md5": man["md5"],
                       "size": man["size"], "baudrate": man["baudrate"]})
        old = f' (was {prev.get("version")})' if prev else ""
        self.changes.append(f'{device} {fw} {channel}: {man["version"]}{old}')
        return man

    def write_manifest(self, device, fw, channel, man):
        path = os.path.join(self.live_dir(device, fw, channel), "manifest.json")
        save_json(path, man)
        self.sign(path)

    def retire(self, device, fw, channel, reason):
        """The channel has no source any more (e.g. the beta became the release)."""
        cur = self.current(device, fw, channel)
        if not cur:
            return False
        self.archive(device, fw, channel)
        self.changes.append(f'{device} {fw} {channel}: removed ({reason}; was {cur.get("version")})')
        return True

    # ------------------------------------------------------------ derived files
    def write_indexes(self):
        """Per device: index.json, README.md and (output px30_catalog) catalog.json."""
        out = {}
        for dname in self.devices:
            out[dname] = self._write_device_index(dname, self.cfg["devices"][dname])
        return {"devices": out}

    def _write_device_index(self, dname, dev):
        index = {"schema": SCHEMA, "generated": now_iso(), "device": dname,
                 "title": dev.get("title", dname), "firmware": {}}
        for fw in dev.get("firmware") or {}:
            for ch in dev.get("channels") or {}:
                man = self.current(dname, fw, ch)
                if man:
                    index["firmware"].setdefault(fw, {})[ch] = {
                        k: man.get(k) for k in ("version", "file", "url", "sha256", "md5", "size",
                                                "baudrate", "flow_control", "sdk", "experimental",
                                                "release_summary", "release_url", "published")}
        if "px30_catalog" in (dev.get("outputs") or []):
            self._write_px30_catalog(dname, dev)
        path = os.path.join(self.device_dir(dname), "index.json")
        old = load_json(path, {})
        if {k: v for k, v in old.items() if k != "generated"} != \
           {k: v for k, v in index.items() if k != "generated"}:
            if index["firmware"] or old:
                save_json(path, index)
                self._write_readme(dname, index)
        return index

    def _write_px30_catalog(self, dname, dev):
        entries = []
        for fw in dev.get("firmware") or {}:
            if fw not in PX30_STACK:
                continue
            for ch in dev.get("channels") or {}:
                man = self.current(dname, fw, ch)
                if man:
                    entries.append({
                        "stack": PX30_STACK[fw], "channel": ch, "version": man["version"],
                        "description": man.get("description", ""), "file": man["file"],
                        "url": man["url"], "sha256": man["sha256"], "size": man["size"],
                        "baudrate": man.get("baudrate") or 115200,
                        "bootloader_baudrate": man.get("bootloader_baudrate") or 115200,
                        "release_summary": man.get("release_summary", ""),
                        "release_url": man.get("release_url", "")})
        entries.sort(key=lambda e: (e["stack"], e["channel"]))
        path = os.path.join(self.device_dir(dname), "catalog.json")
        old = load_json(path, {})
        if old.get("firmware") == entries or (not entries and not old):
            return
        save_json(path, {"_comment": "px30-radio catalog, written by fwpipe. Fetched by "
                         "'px30-update radio sync' into /data/radio.",
                         "generated": now_iso(), "firmware": entries})
        self.sign(path)

    def sign(self, path):
        """ECDSA P-256 / SHA-256 signature (DER, base64) in <path>.sig, when a key is configured."""
        key = os.environ.get("FWPIPE_SIGNING_KEY") or self.pub.get("signing_key")
        if not key:
            return
        sig = path + ".sig.der"
        try:
            run(["openssl", "dgst", "-sha256", "-sign", key, "-out", sig, path], quiet=True)
            with open(sig, "rb") as f:
                raw = f.read()
        finally:
            if os.path.exists(sig):
                os.remove(sig)
        with open(path + ".sig", "w", newline="\n") as f:
            f.write(base64.b64encode(raw).decode() + "\n")

    def _write_readme(self, dname, index):
        lines = [f'# Radio firmware: {index["title"]}', "",
                 "Built and published automatically by fwpipe (the `updater/` folder of "
                 "[px30-wallpanel](https://github.com/Warre118/px30-wallpanel)). Every channel "
                 "folder holds the current build, its `manifest.json` (signed: `manifest.json.sig`) "
                 "and `RELEASE_NOTES.md`. Earlier builds are in `archive/`.", "",
                 "| Firmware | Channel | Version | SDK | Notes | File |", "|---|---|---|---|---|---|"]
        for fw, chans in sorted(index["firmware"].items()):
            for ch, e in sorted(chans.items(), key=lambda kv: CHANNEL_ORDER.index(kv[0])):
                exp = " (experimental)" if e.get("experimental") else ""
                notes = f"[notes]({fw}/{ch}/RELEASE_NOTES.md)" if e.get("release_summary") else ""
                lines.append(f'| {fw}{exp} | {ch} | {e["version"]} | {e.get("sdk") or ""} | {notes} '
                             f'| [{e["file"]}]({fw}/{ch}/{e["file"]}) |')
        lines += ["", "Unofficial builds. Flash at your own risk and make a backup of your "
                  "Zigbee/Thread network first.", ""]
        with open(os.path.join(self.device_dir(dname), "README.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))


class Stores:
    """All checkouts, one per (repository, branch); calls are routed by device."""

    def __init__(self, base, cfg):
        self.cfg = cfg
        groups = {}
        for dname in cfg["devices"]:
            pub = device_publish(cfg, dname)
            key = (pub["repo"], pub["branch"]) if pub["repo"] else ("", dname)
            groups.setdefault(key, []).append(dname)
        self.stores, self.by_device = [], {}
        for (repo, branch), names in groups.items():
            folder = safe_name(f"{repo.replace('/', '_')}@{branch}") if repo else safe_name(f"local_{branch}")
            st = Store(os.path.join(base, folder), cfg, names, device_publish(cfg, names[0]))
            self.stores.append(st)
            for n in names:
                self.by_device[n] = st

    def __getattr__(self, name):
        # device-first methods: current/publish/archive/retire/live_dir/write_manifest/url/...
        def route(device, *a, **kw):
            return getattr(self.by_device[device], name)(device, *a, **kw)
        return route

    @property
    def changes(self):
        return [c for st in self.stores for c in st.changes]

    def reset_changes(self):
        for st in self.stores:
            st.changes = []

    def open(self, devices=None):
        for st in self.stores:
            if devices is None or set(st.devices) & set(devices):
                st.open()

    def write_indexes(self):
        out = {"devices": {}}
        for st in self.stores:
            out["devices"].update(st.write_indexes()["devices"])
        return out

    def commit_push(self, push=True):
        return [st.commit_push(push=push) for st in self.stores]


def newer(a, b):
    """True when version a sorts above b (numeric parts)."""
    return version_key(a) > version_key(b)
