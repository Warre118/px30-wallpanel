"""Build backends. Builds run directly in the fwpipe container, with the
toolchains and SDKs from fwpipe.toolset (downloaded on first use).

  sfb   NabuCasa/silabs-firmware-builder at the commit of the channel's source,
        with a manifest generated from the upstream one + our overlay
  gsdk  this repo's zigbee/ scripts for the TYZS13 (Gecko SDK 4.x),
        through updater/gsdk/build.sh
"""
import glob, os, shutil, sys
from dataclasses import dataclass, field

import yaml

from . import RECIPE_VERSION, gbl, overlay as ovl
from .sources import SourceError, newest_sdk_in_dockerfile
from .toolset import Toolset, ToolError
from .util import load_json, log, now_iso, run, safe_name, text_hash

APP_ROOT = os.environ.get("FWPIPE_APP_ROOT") or \
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class BuildError(Exception):
    pass


@dataclass
class Plan:
    fingerprint: str          # changes whenever a rebuild is needed
    config_hash: str          # changes when OUR side (overlay, recipe) changed
    sdk: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class Result:
    artifact: str
    version: str
    info: dict


def _one_gbl(outdir):
    files = sorted(glob.glob(os.path.join(outdir, "*.gbl")))
    if len(files) != 1:
        raise BuildError(f"expected one .gbl in {outdir}, found {len(files)}")
    return files[0]


# --------------------------------------------------------------------- sfb
class SfbBackend:
    name = "sfb"
    # top-level entries of the builder repo that influence a build
    RELEVANT = ("src", "extension", "tools", "Dockerfile", "pyproject.toml", "uv.lock", "requirements.txt")

    def __init__(self, gh, cfg, tools=None):
        self.gh, self.cfg = gh, cfg
        self.tools = tools or Toolset(os.environ.get("FWPIPE_CACHE", "/data/cache"))

    def plan(self, device, fw, spec, chspec, src):
        base_text = self.gh.raw(src.repo, src.commit, spec["base"])
        with open(os.path.join(self.cfg["_dir"], spec["overlay"]), encoding="utf-8") as f:
            overlay_text = f.read()
        try:
            manifest = ovl.apply(yaml.safe_load(base_text), yaml.safe_load(overlay_text))
        except ovl.OverlayError as e:
            raise BuildError(f"overlay {spec['overlay']} does not fit {spec['base']} at {src.label}: {e}") from e
        if chspec.get("sdk") == "newest" and str(manifest.get("sdk", "")).startswith("simplicity_sdk:"):
            newest = newest_sdk_in_dockerfile(self.gh.raw(src.repo, src.commit, "Dockerfile"))
            if newest:
                ovl.set_sdk_version(manifest, newest)
        tree = self.gh.top_tree(src.repo, src.commit)
        relevant = {k: tree[k] for k in self.RELEVANT if k in tree}
        config_hash = text_hash(RECIPE_VERSION, spec["base"], overlay_text)
        text = yaml.safe_dump(manifest, sort_keys=True)
        return Plan(text_hash(config_hash, text, sorted(relevant.items())), config_hash,
                    sdk=str(manifest.get("sdk", "")),
                    data={"manifest": manifest, "base": spec["base"], "src": src})

    def build(self, device, fw, spec, plan, work):
        src = plan.data["src"]
        checkout, out = os.path.join(work, "sfb"), os.path.join(work, "out")
        for d in (checkout, out):
            shutil.rmtree(d, ignore_errors=True)
        os.makedirs(checkout)
        os.makedirs(out)
        g = ["git", "-C", checkout]
        run(g + ["init", "-q"])
        run(g + ["remote", "add", "origin", f"https://github.com/{src.repo}.git"])
        run(g + ["fetch", "-q", "--depth", "1", "origin", src.commit])
        run(g + ["checkout", "-q", "FETCH_HEAD"])
        # next to the upstream manifest: relative paths in it (config_file) keep working
        rel = os.path.join(os.path.dirname(plan.data["base"]), f"fwpipe_{safe_name(device)}_{fw}.yaml")
        with open(os.path.join(checkout, rel), "w", encoding="utf-8", newline="\n") as f:
            yaml.safe_dump(plan.data["manifest"], f, sort_keys=False)
        man = plan.data["manifest"]
        try:
            sdk = self.tools.sdk(man["sdk"])
            gcc = self.tools.gcc(man["toolchain"])
            python = self.tools.venv(checkout)
        except (ToolError, OSError, KeyError) as e:
            raise BuildError(f"tools for {src.label}: {e}") from e
        entry = ["-m", "tools.build_project"] if os.path.exists(os.path.join(checkout, "tools", "__init__.py")) \
            else ["tools/build_project.py"]
        argv = [python, *entry, "--manifest", rel.replace(os.sep, "/"), "--output", "gbl",
                "--output-dir", out,
                # inside the checkout: newer builders patch the copied SDK with paths
                # relative to the builder repository
                "--build-dir", os.path.join(checkout, "build"),
                "--sdk", sdk, "--toolchain", gcc]
        run(argv, cwd=checkout, timeout=3 * 3600,
            env=dict(os.environ, HOME=os.environ.get("HOME", "/tmp"), SILABS_FIRMWARE_BUILD_CONTAINER="1"))
        artifact = _one_gbl(out)
        meta = gbl.read_metadata(artifact)
        version = gbl.firmware_version(fw, meta)
        if not version:
            raise BuildError(f"no version in the GBL metadata of {os.path.basename(artifact)}: {meta}")
        flow = str((man.get("c_defines") or {}).get("SL_IOSTREAM_USART_VCOM_FLOW_CONTROL_TYPE", ""))
        return Result(artifact, version, {
            "baudrate": meta.get("baudrate") or (man.get("gbl") or {}).get("baudrate"),
            "flow_control": "hardware" if "CtsAndRts" in flow else "none",
            "sdk": plan.sdk, "metadata": meta, "built": now_iso(),
            "description": spec.get("description") or man.get("name", ""),
            "source": {"repo": src.repo, "ref": src.label, "commit": src.commit, "url": src.url}})


# -------------------------------------------------------------------- gsdk
class GsdkBackend:
    name = "gsdk"
    RECIPE = ("zigbee/gen-ot-rcp.sh", "zigbee/build-ot-rcp.sh", "zigbee/patch-ot-rcp.py",
              "zigbee/gen-ncp.sh", "zigbee/build-ncp.sh", "zigbee/patch-ncp.py",
              "updater/gsdk/build.sh", "updater/gsdk/mkgbl.py")
    GCC = "12.2.1.20221205"       # what the Gecko SDK 4.4/4.5 were released with

    def __init__(self, gh, cfg, tools=None, app_root=APP_ROOT):
        self.gh, self.cfg, self.app_root = gh, cfg, app_root
        self.tools = tools or Toolset(os.environ.get("FWPIPE_CACHE", "/data/cache"))

    def _recipe_hash(self):
        parts = [RECIPE_VERSION]
        for rel in self.RECIPE:
            with open(os.path.join(self.app_root, rel), "rb") as f:
                parts.append(f.read().replace(b"\r\n", b"\n"))
        return text_hash(*parts)

    def plan(self, device, fw, spec, chspec, src):
        config_hash = text_hash(self._recipe_hash(), spec["target"], spec.get("baudrate", 115200))
        return Plan(text_hash(config_hash, src.version), config_hash, sdk=f"gecko_sdk:{src.version}",
                    data={"src": src})

    def build(self, device, fw, spec, plan, work):
        src = plan.data["src"]
        try:
            sdk = self.tools.sdk(f"gecko_sdk:{src.version.lstrip('v')}")
            gcc = self.tools.gcc(self.GCC)
        except (ToolError, OSError) as e:
            raise BuildError(f"tools for Gecko SDK {src.version}: {e}") from e
        out = os.path.join(work, "out")
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(out)
        silabs = os.environ.get("FWPIPE_SILABS", "/opt/silabs")
        env = dict(os.environ, SDK=sdk, GCC_DIR=gcc, BAUD=str(spec.get("baudrate", 115200)),
                   T=silabs,
                   SLC_TOOL_PATH=":".join(Toolset.adapter_packs()),
                   ZIGBEE_DIR=os.path.join(self.app_root, "zigbee"), TMPDIR=work)
        # slc runs the SDK's Jinja template generators with `python3` from PATH:
        # that must be the adapter pack's (it has jinja2), not Debian's or the
        # venv's. The zigbee/ scripts call slc directly, past updater/bin/slc.
        pack_py = os.path.join(silabs, "python", "bin")
        if os.path.isdir(pack_py):
            env["PATH"] = pack_py + os.pathsep + env.get("PATH", "")
        for k in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            env.pop(k, None)
        env.setdefault("PYTHON", sys.executable)   # mkgbl.py needs pygbl from fwpipe's venv
        if spec.get("router"):
            env["ROUTER"] = "1"
        run(["bash", os.path.join(self.app_root, "updater/gsdk/build.sh"), spec["target"], out],
            env=env, timeout=3 * 3600)
        artifact = _one_gbl(out)
        info = load_json(os.path.join(out, "build-info.json"), {})
        if not info.get("version"):
            raise BuildError("the gsdk build wrote no build-info.json with a version")
        return Result(artifact, str(info["version"]), {
            "baudrate": info.get("baudrate", 115200), "flow_control": info.get("flow_control", "hardware"),
            "sdk": plan.sdk, "metadata": info, "built": now_iso(),
            "description": spec.get("description") or info.get("description", ""),
            "source": {"repo": src.repo, "ref": src.label, "commit": "", "url": src.url}})
