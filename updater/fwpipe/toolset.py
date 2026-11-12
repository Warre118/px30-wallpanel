"""Toolchains, SDKs and Python environments, fetched on demand into a cache
folder (a volume of the one fwpipe container). Nothing runs in another
container: every build uses these directly.

  <cache>/toolchains/gcc-<version>/        Arm GNU toolchain
  <cache>/sdks/<name>_<version>/           Simplicity SDK (conan) / Gecko SDK (GitHub)
  <cache>/venvs/<hash>/                    the builder's Python environment (uv)

slc-cli, the Python adapter pack, ZAP and a JRE do not change per build and
are part of the image (/opt/silabs, see updater/Dockerfile).
"""
import json, os, platform, shutil, stat, tarfile, tempfile, urllib.request, zipfile

from .util import file_hash, log, run, safe_name, text_hash

SILABS = os.environ.get("FWPIPE_SILABS", "/opt/silabs")

# GCC version as the builder names it (basever.datestamp of plugin-version.h)
# -> Arm download and its sha256. Same files as NabuCasa/silabs-firmware-builder.
ARM = "https://armkeil.blob.core.windows.net/developer/Files/downloads/gnu"
GCC = {
    "12.2.1.20221205": {
        "x86_64": (f"{ARM}/12.2.rel1/binrel/arm-gnu-toolchain-12.2.rel1-x86_64-arm-none-eabi.tar.xz",
                   "84be93d0f9e96a15addd490b6e237f588c641c8afdf90e7610a628007fc96867"),
    },
    "14.2.1.20241119": {
        "x86_64": (f"{ARM}/14.2.rel1/binrel/arm-gnu-toolchain-14.2.rel1-x86_64-arm-none-eabi.tar.xz",
                   "62a63b981fe391a9cbad7ef51b17e49aeaa3e7b0d029b36ca1e9c3b2a9b78823"),
    },
}
CONAN = "https://conan.silabs.net/v2/conans/simplicity-sdk/{v}/silabs/_"
GSDK_ZIP = "https://github.com/SiliconLabs/gecko_sdk/releases/download/v{v}/gecko-sdk.zip"


class ToolError(Exception):
    pass


def _arch():
    m = platform.machine().lower()
    return {"amd64": "x86_64", "aarch64": "arm64"}.get(m, m)


def _fetch(url, dest, sha256=None, opener=urllib.request.urlopen):
    log(f"download {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "fwpipe"})
    with opener(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    if sha256 and file_hash(dest) != sha256:
        raise ToolError(f"checksum mismatch for {url}")


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "fwpipe"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _member_path(dest, name, strip):
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")][strip:]
    if not parts or ".." in parts:
        return None
    path = os.path.join(dest, *parts)
    if not os.path.abspath(path).startswith(os.path.abspath(dest) + os.sep):
        return None
    return path


def _unpack(archive, dest, strip=0):
    """.zip / .tar.* into dest, keeping executable bits and symlinks; members that
    would land outside dest are skipped."""
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                path = _member_path(dest, info.filename, strip)
                if path is None:
                    continue
                mode = info.external_attr >> 16
                if info.is_dir():
                    os.makedirs(path, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if stat.S_ISLNK(mode):
                    if os.path.lexists(path):
                        os.remove(path)
                    os.symlink(z.read(info).decode(), path)
                    continue
                with z.open(info) as src, open(path, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                if mode & 0o111:
                    os.chmod(path, (mode & 0o777) | 0o600)
        return
    with tarfile.open(archive) as t:
        for m in t.getmembers():
            path = _member_path(dest, m.name, strip)
            if path is None:
                continue
            m.name = os.path.relpath(path, dest)
            if m.islnk():
                link = _member_path(dest, m.linkname, strip)
                if link is None:
                    continue
                m.linkname = os.path.relpath(link, dest)
            t.extract(m, dest, set_attrs=True, filter="fully_trusted" if m.issym() else "tar")


class Toolset:
    def __init__(self, cache):
        self.cache = cache

    def _install(self, kind, name, fill):
        """Fill <cache>/<kind>/<name> once; a half-finished install is never used."""
        final = os.path.join(self.cache, kind, name)
        if os.path.exists(os.path.join(final, ".complete")):
            return final
        os.makedirs(os.path.dirname(final), exist_ok=True)
        tmp = tempfile.mkdtemp(prefix=f".{name}.", dir=os.path.dirname(final))
        try:
            fill(tmp)
            open(os.path.join(tmp, ".complete"), "w").close()
            shutil.rmtree(final, ignore_errors=True)
            os.replace(tmp, final)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return final

    # ---------------------------------------------------------- toolchains
    def gcc(self, version):
        version = str(version).split(":", 1)[-1]          # "gcc:14.2.1.20241119"
        urls = GCC.get(version, {})
        if _arch() not in urls:
            raise ToolError(f"no Arm GCC {version} known for {_arch()} (known: {', '.join(GCC)})")
        url, sha = urls[_arch()]

        def fill(d):
            a = os.path.join(d, "gcc.tar.xz")
            _fetch(url, a, sha)
            _unpack(a, d, strip=1)
            os.remove(a)
        return self._install("toolchains", f"gcc-{version}", fill)

    # ---------------------------------------------------------------- SDKs
    def sdk(self, spec):
        """'simplicity_sdk:2026.6.1' / 'gecko_sdk:4.5.0' -> folder with *_sdk.slcs."""
        name, _, version = str(spec).partition(":")
        if not version:
            raise ToolError(f"SDK without a version: {spec}")
        if name == "simplicity_sdk":
            return self._install("sdks", f"simplicity_sdk_{safe_name(version)}",
                                 lambda d: self._conan_sdk(version, d))
        if name == "gecko_sdk":
            def fill(d):
                a = os.path.join(d, "sdk.zip")
                _fetch(GSDK_ZIP.format(v=version), a)
                _unpack(a, d)
                os.remove(a)
                if not os.path.exists(os.path.join(d, "gecko_sdk.slcs")):
                    raise ToolError(f"gecko-sdk.zip {version} has no gecko_sdk.slcs")
            return self._install("sdks", f"gecko_sdk_{safe_name(version)}", fill)
        raise ToolError(f"unknown SDK {name}")

    @staticmethod
    def _conan_sdk(version, d):
        # recipe revision, package id and package revision are content hashes,
        # discoverable over conan's REST API (no conan client needed)
        base = CONAN.format(v=version)
        rrev = _get_json(f"{base}/latest")["revision"]
        pkg = sorted(_get_json(f"{base}/revisions/{rrev}/search"))[0]
        prev = _get_json(f"{base}/revisions/{rrev}/packages/{pkg}/latest")["revision"]
        a = os.path.join(d, "sdk.tgz")
        _fetch(f"{base}/revisions/{rrev}/packages/{pkg}/revisions/{prev}/files/conan_package.tgz", a)
        _unpack(a, d)
        os.remove(a)

    # ------------------------------------------------------- Python (uv)
    def venv(self, project_dir):
        """The builder's own Python environment, from uv.lock (newer builder) or
        requirements.txt (older), cached by the hash of those files."""
        lock = [f for f in ("pyproject.toml", "uv.lock", "requirements.txt")
                if os.path.exists(os.path.join(project_dir, f))]
        if not lock:
            raise ToolError(f"{project_dir}: no pyproject.toml/uv.lock/requirements.txt")
        parts = []
        for f in lock:
            with open(os.path.join(project_dir, f), "rb") as fh:
                parts.append(fh.read())
        key = text_hash(*lock, *parts)[:16]
        env = dict(os.environ, UV_PYTHON_INSTALL_DIR=os.path.join(self.cache, "pythons"),
                   UV_CACHE_DIR=os.path.join(self.cache, "uv-cache"))

        def fill(d):
            if "uv.lock" in lock:
                py = _requires_python(os.path.join(project_dir, ".python-version"))
                tmp_proj = os.path.join(d, ".project")
                os.makedirs(tmp_proj)
                for f in ("pyproject.toml", "uv.lock", ".python-version"):
                    if os.path.exists(os.path.join(project_dir, f)):
                        shutil.copy(os.path.join(project_dir, f), tmp_proj)
                run(["uv", "sync", "--project", tmp_proj, "--frozen", "--no-dev", "--no-install-project"]
                    + (["-p", py] if py else []),
                    env=dict(env, UV_PROJECT_ENVIRONMENT=os.path.join(d, "venv")))
            else:
                run(["uv", "venv", "-p", "3.13", os.path.join(d, "venv")], env=env)
                run(["uv", "pip", "install", "--python", os.path.join(d, "venv", "bin", "python"),
                     "-r", os.path.join(project_dir, "requirements.txt")], env=env)
        return os.path.join(self._install("venvs", key, fill), "venv", "bin", "python")

    # ------------------------------------------------------------- image
    @staticmethod
    def adapter_packs():
        """slc adapter packs in the image (python, zap, java)."""
        if not os.path.isdir(SILABS):
            return []
        return sorted(os.path.join(SILABS, d) for d in os.listdir(SILABS)
                      if os.path.exists(os.path.join(SILABS, d, "apack.json")))

    def prune(self, keep):
        """Remove cached toolchains/SDKs/venvs whose names are not in `keep`."""
        for kind in ("toolchains", "sdks", "venvs"):
            root = os.path.join(self.cache, kind)
            for name in os.listdir(root) if os.path.isdir(root) else ():
                if name not in keep and not name.startswith("."):
                    log(f"cache: removing {kind}/{name}")
                    shutil.rmtree(os.path.join(root, name), ignore_errors=True)


def _requires_python(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None
