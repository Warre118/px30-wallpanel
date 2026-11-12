"""One pass over every (device, firmware type, channel): resolve the upstream
source, build when something changed, archive + publish when the result is new."""
import json, os, shutil, time, urllib.request

from . import notes, sources
from .build import BuildError
from .config import jobs
from .sources import SourceError
from .util import load_json, log, now_iso, save_json

RETRY_BASE_H = 24          # a failed build is retried after 24 h, 48 h, ... (max 7 days)


class Pipeline:
    def __init__(self, cfg, gh, backends, store, state_dir, work_dir):
        self.cfg, self.gh, self.backends, self.store = cfg, gh, backends, store
        self.state_path = os.path.join(state_dir, "state.json")
        self.cache_dir = os.path.join(state_dir, "cache")
        self.work_dir = work_dir
        self.state = load_json(self.state_path, {}) or {}
        self.state.setdefault("builds", {})
        self.report = []           # rows for `check` / the log
        self.failures = []
        self._src = {}

    # ---------------------------------------------------------------- sources
    def source(self, dname, dev, ch, chspec):
        key = (dname, ch)
        if key not in self._src:
            up = self.cfg["upstream"]
            if dev["backend"] == "sfb":
                self._src[key] = sources.resolve_sfb(self.gh, up["sfb_repo"], chspec)
            else:
                self._src[key] = sources.resolve_gsdk(self.gh, up["gsdk_repo"], chspec)
        return self._src[key]

    # ---------------------------------------------------------------- one job
    def _row(self, dname, fw, ch, src, current, action, note=""):
        self.report.append({"device": dname, "firmware": fw, "channel": ch,
                            "source": src.describe() if src else "-",
                            "current": (current or {}).get("version", "-"),
                            "action": action, "note": note})

    def _cached(self, fp):
        b = self.state["builds"].get(fp)
        if b and b.get("status") == "ok" and os.path.exists(os.path.join(self.cache_dir, fp, "firmware.gbl")):
            return b
        return None

    def _blocked(self, fp, force):
        b = self.state["builds"].get(fp)
        if not b or b.get("status") != "failed" or force:
            return None
        if time.time() < b.get("retry_after", 0):
            return b
        return None

    def process(self, dname, fw, ch, dev, spec, chspec, dry_run=False, force=False):
        current = self.store.current(dname, fw, ch)
        try:
            src = self.source(dname, dev, ch, chspec)
        except SourceError as e:
            self.failures.append(f"{dname} {fw} {ch}: upstream check failed: {e}")
            return self._row(dname, fw, ch, None, current, "error", str(e))
        if src is None:
            if current and not dry_run:
                self.store.retire(dname, fw, ch, "no upstream version for this channel")
            return self._row(dname, fw, ch, None, current, "retire" if current else "none",
                             "no upstream version for this channel")
        backend = self.backends[dev["backend"]]
        try:
            plan = backend.plan(dname, fw, spec, chspec, src)
        except (BuildError, SourceError, OSError) as e:
            self.failures.append(f"{dname} {fw} {ch}: {e}")
            return self._row(dname, fw, ch, src, current, "error", str(e))
        fp = plan.fingerprint
        if current and current.get("fingerprint") == fp and not force:
            return self._row(dname, fw, ch, src, current, "up-to-date")
        build = self._cached(fp) if not force else None
        if not build:
            blocked = self._blocked(fp, force)
            if blocked:
                return self._row(dname, fw, ch, src, current, "failed-earlier",
                                 f'{blocked.get("error", "")[:120]} (retry after '
                                 f'{time.strftime("%Y-%m-%d %H:%M", time.localtime(blocked["retry_after"]))})')
            if dry_run:
                return self._row(dname, fw, ch, src, current, "build")
            build = self._build(backend, dname, fw, ch, spec, plan)
            if not build:
                return self._row(dname, fw, ch, src, current, "build-failed",
                                 self.state["builds"][fp].get("error", "")[:200])
        # same firmware version and nothing changed on our side: upstream only moved
        # around it (docs, other boards). Keep what the channel has.
        same = current and current.get("stack_version", current.get("version")) == build["version"]
        if same and current.get("config_hash") == plan.config_hash and not force \
                and not self.cfg["publish"].get("republish_same_version"):
            return self._row(dname, fw, ch, src, current, "unchanged", "upstream moved, same firmware version")
        if dry_run:
            return self._row(dname, fw, ch, src, current, "publish", build["version"])
        info = dict(build["info"])
        revision = (current.get("revision", 1) + 1) if same else 1
        info.update(version=build["version"] + (f"-r{revision}" if revision > 1 else ""),
                    fingerprint=fp, config_hash=plan.config_hash, experimental=spec.get("experimental"),
                    stack_version=build["version"], revision=revision)
        info["notes"], info["summary"] = notes.make(self.gh, dev, fw, ch, info["version"], info, src, current)
        man = self.store.publish(dname, fw, ch, os.path.join(self.cache_dir, fp, "firmware.gbl"), info)
        self._row(dname, fw, ch, src, current, "published", man["version"])

    def _build(self, backend, dname, fw, ch, spec, plan):
        fp = plan.fingerprint
        work = os.path.join(self.work_dir, fp[:16])
        os.makedirs(work, exist_ok=True)
        log(f"build: {dname} {fw} {ch} from {plan.data['src'].describe()} ({plan.sdk})")
        prev = self.state["builds"].get(fp, {})
        try:
            res = backend.build(dname, fw, spec, plan, work)
            dst = os.path.join(self.cache_dir, fp)
            os.makedirs(dst, exist_ok=True)
            shutil.copyfile(res.artifact, os.path.join(dst, "firmware.gbl"))
            entry = {"status": "ok", "version": res.version, "info": res.info, "time": now_iso()}
        except Exception as e:           # a failed build must not stop the other jobs
            attempts = prev.get("attempts", 0) + 1
            entry = {"status": "failed", "error": str(e)[-1500:], "time": now_iso(), "attempts": attempts,
                     "retry_after": time.time() + min(attempts * RETRY_BASE_H, 168) * 3600}
            self.failures.append(f"{dname} {fw} {ch}: build failed: {str(e)[-300:]}")
            log(f"build FAILED: {dname} {fw} {ch}: {e}")
        finally:
            shutil.rmtree(work, ignore_errors=True)
        self.state["builds"][fp] = entry
        save_json(self.state_path, self.state)
        return entry if entry["status"] == "ok" else None

    # ---------------------------------------------------------------- a pass
    def run(self, device=None, firmware=None, channel=None, dry_run=False, force=False, push=True):
        self.report, self.failures, self._src = [], [], {}
        self.store.reset_changes()
        self.gh._memo.clear()
        self.store.open()
        for job in jobs(self.cfg, device, firmware, channel):
            self.process(*job, dry_run=dry_run, force=force)
        changes = list(self.store.changes)
        if not dry_run:
            self.store.write_indexes()
            self.failures += self.store.commit_push(push=push)
            self._prune_cache()
            self.state["last_run"] = now_iso()
            save_json(self.state_path, self.state)
            if changes or self.failures:
                self.notify(changes)
        return changes

    def _prune_cache(self, days=60):
        cutoff = time.time() - days * 86400
        live = {r.get("fingerprint") for r in self._live_manifests()}
        for fp in list(self.state["builds"]):
            d = os.path.join(self.cache_dir, fp)
            if fp in live:
                continue
            try:
                old = time.mktime(time.strptime(self.state["builds"][fp]["time"], "%Y-%m-%dT%H:%M:%SZ")) < cutoff
            except (KeyError, ValueError):
                old = True
            if old:
                shutil.rmtree(d, ignore_errors=True)
                del self.state["builds"][fp]

    def _live_manifests(self):
        for dname, fw, ch, *_ in jobs(self.cfg):
            man = self.store.current(dname, fw, ch)
            if man:
                yield man

    def notify(self, changes):
        """POST the outcome to a webhook (e.g. a Home Assistant webhook automation)."""
        url = os.environ.get("FWPIPE_WEBHOOK") or (self.cfg.get("notify") or {}).get("webhook")
        if not url:
            return
        body = json.dumps({"changes": changes, "failures": self.failures,
                           "time": now_iso()}).encode()
        try:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15).close()
        except OSError as e:
            log(f"notify: webhook failed: {e}")


def format_report(rows):
    if not rows:
        return "(nothing configured)"
    cols = ("device", "firmware", "channel", "current", "action", "source", "note")
    width = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols[:-1]}
    out = ["  ".join(c.ljust(width[c]) for c in cols[:-1]) + "  note"]
    for r in rows:
        out.append("  ".join(str(r[c]).ljust(width[c]) for c in cols[:-1]) + "  " + r["note"])
    return "\n".join(out)
