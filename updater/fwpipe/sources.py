"""Resolve a channel to an upstream source (what to build from).

Two kinds of upstream:
  sfb   NabuCasa/silabs-firmware-builder (series 2 chips, e.g. the MGM210):
          release     newest full release           -> "stable"
          prerelease  newest pre-release, only when it is newer than the
                      newest full release           -> "beta" (may not exist)
          branch      head of a branch (main)       -> "latest"
  gsdk  SiliconLabs/gecko_sdk releases (series 1 chips, e.g. the TYZS13):
          pinned      a fixed tag from the config   -> "stable" (hand-promoted)
          release     highest released version      -> "latest"
          prerelease  highest pre-release above the highest release -> "beta"
"""
import json, os, re, urllib.error, urllib.request
from dataclasses import dataclass, field

from .util import version_key

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"


class SourceError(Exception):
    pass


@dataclass
class Source:
    kind: str                 # sfb | gsdk
    repo: str
    label: str                # tag or branch name
    commit: str = ""          # sfb: commit sha; gsdk: empty
    version: str = ""         # gsdk: SDK version without the leading v
    url: str = ""
    extra: dict = field(default_factory=dict)

    def describe(self):
        return f"{self.repo}@{self.label}" + (f" ({self.commit[:10]})" if self.commit else "")


class GitHub:
    """Minimal read-only GitHub client (token optional: only raises the rate limit)."""

    def __init__(self, token=None, opener=None):
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN", "")
        self._open = opener or urllib.request.urlopen
        self._memo = {}

    def _get(self, url, accept):
        if url in self._memo:
            return self._memo[url]
        headers = {"Accept": accept, "User-Agent": "fwpipe"}
        if self.token and url.startswith(API):
            headers["Authorization"] = f"Bearer {self.token}"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        try:
            with self._open(urllib.request.Request(url, headers=headers), timeout=30) as r:
                data = r.read()
        except urllib.error.HTTPError as e:
            raise SourceError(f"GET {url}: HTTP {e.code}") from e
        except (OSError, urllib.error.URLError) as e:
            raise SourceError(f"GET {url}: {e}") from e
        self._memo[url] = data
        return data

    def api(self, path):
        return json.loads(self._get(f"{API}{path}", "application/vnd.github+json").decode())

    def raw_bytes(self, repo, ref, path):
        return self._get(f"{RAW}/{repo}/{ref}/{path}", "*/*")

    def raw(self, repo, ref, path):
        return self.raw_bytes(repo, ref, path).decode("utf-8")

    def releases(self, repo):
        return [r for r in self.api(f"/repos/{repo}/releases?per_page=50") if not r.get("draft")]

    def commit_sha(self, repo, ref):
        return self.api(f"/repos/{repo}/commits/{ref}")["sha"]

    def top_tree(self, repo, commit):
        """{name: git sha} of the top-level entries at a commit."""
        t = self.api(f"/repos/{repo}/git/trees/{commit}")
        return {e["path"]: e["sha"] for e in t.get("tree", [])}


def resolve_sfb(gh, repo, spec):
    """spec: {source: release|prerelease|branch, branch: main}. None = channel has no source."""
    kind = spec.get("source")
    if kind == "branch":
        branch = spec.get("branch", "main")
        sha = gh.commit_sha(repo, branch)
        return Source("sfb", repo, branch, sha, url=f"https://github.com/{repo}/commit/{sha}",
                      extra={"branch": True})
    rels = gh.releases(repo)
    full = sorted((r for r in rels if not r.get("prerelease")), key=lambda r: r["published_at"])
    pre = sorted((r for r in rels if r.get("prerelease")), key=lambda r: r["published_at"])
    if kind == "release":
        if not full:
            return None
        rel = full[-1]
    elif kind == "prerelease":
        if not pre or (full and pre[-1]["published_at"] <= full[-1]["published_at"]):
            return None       # no pre-release newer than the release: no beta right now
        rel = pre[-1]
    elif kind == "pinned":
        tag = spec.get("version") or spec.get("tag")
        if not tag:
            raise SourceError("pinned source needs a version")
        rel = {"tag_name": tag, "html_url": f"https://github.com/{repo}/releases/tag/{tag}"}
    else:
        raise SourceError(f"unknown sfb source {kind!r}")
    tag = rel["tag_name"]
    return Source("sfb", repo, tag, gh.commit_sha(repo, tag), url=rel.get("html_url", ""),
                  extra={"body": rel["body"]} if "body" in rel else {})


def resolve_gsdk(gh, repo, spec):
    kind = spec.get("source")
    if kind == "pinned":
        tag = str(spec.get("version") or "")
        if not tag:
            raise SourceError("pinned source needs a version")
        tag = tag if tag.startswith("v") else "v" + tag
        return Source("gsdk", repo, tag, version=tag.lstrip("v"),
                      url=f"https://github.com/{repo}/releases/tag/{tag}")
    rels = gh.releases(repo)
    prefix = str(spec.get("series") or "")          # e.g. "4." to stay on GSDK 4.x

    def pick(pre):
        c = [r for r in rels if bool(r.get("prerelease")) == pre
             and r["tag_name"].lstrip("v").startswith(prefix) and version_key(r["tag_name"])]
        return max(c, key=lambda r: version_key(r["tag_name"])) if c else None

    full, pre = pick(False), pick(True)
    if kind == "release":
        rel = full
    elif kind == "prerelease":
        rel = pre if pre and (not full or version_key(pre["tag_name"]) > version_key(full["tag_name"])) else None
    else:
        raise SourceError(f"unknown gsdk source {kind!r}")
    if not rel:
        return None
    tag = rel["tag_name"]
    return Source("gsdk", repo, tag, version=tag.lstrip("v"), url=rel.get("html_url", ""),
                  extra={"body": rel.get("body") or ""})


def newest_sdk_in_dockerfile(dockerfile):
    """The newest Simplicity SDK the builder image carries (ARG SDK_VERSIONS / SDK_VERSION)."""
    m = re.search(r'^ARG\s+SDK_VERSIONS?="?([^"\n]+)"?', dockerfile, re.M)
    if not m:
        return None
    versions = m.group(1).split()
    return max(versions, key=version_key) if versions else None
