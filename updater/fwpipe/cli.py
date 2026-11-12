"""fwpipe command line.

  fwpipe check                      what would be built / published (no build, no write)
  fwpipe run [--no-push] [--force]  one pass: check, build, archive + publish
  fwpipe loop                       run forever, every check_interval_h hours
  fwpipe status                     what every channel currently holds
  fwpipe serve [--port 8080]        serve the published tree over HTTP (LAN-only setups)

  -c/--config FILE   default $FWPIPE_CONFIG or updater/config.yaml
  --device/--firmware/--channel     limit check/run to a part
Folders (defaults for the container): $FWPIPE_STORE=/data/repos (checkouts of the
device repositories), $FWPIPE_STATE=/data/state, $FWPIPE_CACHE=/data/cache
(toolchains, SDKs), $FWPIPE_WORK=/work (build folders).
FWPIPE_LOCAL_ONLY=1: publish into local folders only, never to GitHub.
"""
import argparse, functools, http.server, os, sys, time

from . import __version__
from .build import GsdkBackend, SfbBackend
from .config import ConfigError, load
from .pipeline import Pipeline, format_report
from .sources import GitHub
from .store import Stores
from .util import die, log

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_pipeline(args):
    try:
        cfg = load(args.config)
    except (OSError, ConfigError) as e:
        die(f"config {args.config}: {e}")
    gh = GitHub()
    store = Stores(os.environ.get("FWPIPE_STORE", "/data/repos"), cfg)
    backends = {"sfb": SfbBackend(gh, cfg), "gsdk": GsdkBackend(gh, cfg)}
    return Pipeline(cfg, gh, backends, store,
                    os.environ.get("FWPIPE_STATE", "/data/state"), os.environ.get("FWPIPE_WORK", "/work"))


def one_pass(p, args, dry_run):
    changes = p.run(args.device, args.firmware, args.channel, dry_run=dry_run,
                    force=getattr(args, "force", False), push=not getattr(args, "no_push", False))
    print(format_report(p.report))
    for c in changes:
        log(f"published: {c}")
    for f in p.failures:
        log(f"FAILED: {f}")
    return 1 if p.failures else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fwpipe", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("-c", "--config", default=os.environ.get("FWPIPE_CONFIG", os.path.join(HERE, "config.yaml")))
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "run"):
        sp = sub.add_parser(name)
        sp.add_argument("--device")
        sp.add_argument("--firmware")
        sp.add_argument("--channel")
        if name == "run":
            sp.add_argument("--no-push", action="store_true", help="commit locally, do not push")
            sp.add_argument("--force", action="store_true", help="rebuild and republish even when unchanged")
    sub.add_parser("loop")
    sub.add_parser("status")
    sp = sub.add_parser("serve")
    sp.add_argument("--port", type=int, default=8080)
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        root = os.environ.get("FWPIPE_STORE", "/data/repos")
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=root)
        log(f"serving {root} on port {args.port}")
        http.server.ThreadingHTTPServer(("0.0.0.0", args.port), handler).serve_forever()
    p = make_pipeline(args)
    if args.cmd == "check":
        return one_pass(p, args, dry_run=True)
    if args.cmd == "run":
        return one_pass(p, args, dry_run=False)
    if args.cmd == "status":
        idx = p.store.write_indexes()
        for dname, d in idx["devices"].items():
            for fw, chans in d["firmware"].items():
                for ch, e in chans.items():
                    print(f'{dname:18} {fw:15} {ch:7} {e["version"]:14} {e.get("sdk") or "":28} {e["published"]}')
        return 0
    if args.cmd == "loop":
        args.device = args.firmware = args.channel = None
        while True:
            try:
                p = make_pipeline(args)          # re-read the config every pass
                one_pass(p, args, dry_run=False)
            except Exception as e:               # keep the service alive; next pass retries
                log(f"pass failed: {e}")
            hours = float(p.cfg.get("check_interval_h", 12))
            log(f"next check in {hours:g} h")
            time.sleep(max(600, hours * 3600))


if __name__ == "__main__":
    sys.exit(main())
