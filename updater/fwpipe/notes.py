"""Release notes for a published build: what this build is, what changed since
the build it replaces, and the upstream notes (the builder's or SDK's GitHub
release, or the commits since the previous build for a branch channel)."""
from .sources import SourceError
from .util import now_iso

FW_NAMES = {"zigbee_ncp": "Zigbee coordinator (EmberZNet NCP)",
            "openthread_rcp": "Thread / Matter radio (OpenThread RCP)",
            "zigbee_router": "Zigbee router"}
UPSTREAM_MAX = 6000          # characters of upstream notes kept in RELEASE_NOTES.md
COMMITS_MAX = 30


def _sdk_label(sdk):
    name, _, ver = str(sdk or "").partition(":")
    return {"simplicity_sdk": "Simplicity SDK", "gecko_sdk": "Gecko SDK"}.get(name, name) + (f" {ver}" if ver else "")


def _upstream(gh, src, prev):
    """(heading, markdown) of the upstream changes, best effort."""
    try:
        if src.extra.get("branch"):
            base = ((prev or {}).get("source") or {}).get("commit")
            if not base or base == src.commit:
                return None
            cmp = gh.api(f"/repos/{src.repo}/compare/{base}...{src.commit}")
            commits = cmp.get("commits") or []
            lines = [f'- {c["commit"]["message"].splitlines()[0]} ({c["sha"][:7]})' for c in commits[-COMMITS_MAX:]]
            if len(commits) > COMMITS_MAX:
                lines.insert(0, f"- ... {len(commits) - COMMITS_MAX} older commits")
            return (f"Changes in {src.repo} since {base[:7]}", "\n".join(reversed(lines)) or "(no commits)")
        body = src.extra.get("body")
        if body is None and src.label:
            body = gh.api(f"/repos/{src.repo}/releases/tags/{src.label}").get("body")
        body = (body or "").strip()
        if not body:
            return None
        if len(body) > UPSTREAM_MAX:
            body = body[:UPSTREAM_MAX].rsplit("\n", 1)[0] + f"\n\n... (full notes: {src.url})"
        return (f"{src.repo} {src.label} release notes", body)
    except (SourceError, KeyError, TypeError, ValueError):
        return None


def make(gh, dev, fw, channel, version, info, src, prev):
    """-> (markdown, summary). summary is one line for Home Assistant (<= 255 chars)."""
    title = dev.get("title", "")
    sdk = _sdk_label(info.get("sdk"))
    flow = "hardware flow control" if info.get("flow_control") == "hardware" else "no flow control"
    was = (prev or {}).get("version")
    lines = [f"# {FW_NAMES.get(fw, fw)} {version}", "",
             f"**{title}**, channel **{channel}**.", "",
             f"- Firmware version: {version}" + (f" (replaces {was})" if was and was != version else ""),
             f"- SDK: {sdk}" if sdk else None,
             f"- Serial: {info.get('baudrate') or 115200} baud, {flow}",
             f"- Built from: [{src.describe()}]({src.url})" if src.url else f"- Built from: {src.describe()}",
             f"- Built: {info.get('built') or now_iso()}"]
    if was and was == version.split("-r")[0]:
        lines.append("- Same firmware version as before; rebuilt because the build recipe changed.")
    if info.get("experimental"):
        lines.append("- **Experimental**: not tested on hardware yet.")
    if info.get("description"):
        lines += ["", info["description"]]
    up = _upstream(gh, src, prev)
    if up:
        lines += ["", f"## {up[0]}", "", up[1]]
    lines += ["", "Flash at your own risk; back up your Zigbee/Thread network first."]
    md = "\n".join(l for l in lines if l is not None) + "\n"

    parts = [f"{version}"]
    if was and was != version:
        parts[0] += f" (was {was})"
    if sdk:
        parts.append(sdk)
    parts.append(f"{src.label}" if not src.extra.get("branch") else f"{src.label}@{src.commit[:7]}")
    if info.get("experimental"):
        parts.append("experimental")
    return md, f"{FW_NAMES.get(fw, fw)} " + " · ".join(parts)
