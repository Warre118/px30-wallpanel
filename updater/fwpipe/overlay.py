"""Turn an upstream silabs-firmware-builder manifest into one for our board.

The upstream manifests change between builder releases (other SDK, other
define names, extra components). Instead of keeping a full copy per release,
an overlay describes only what differs on our board, and is applied to the
upstream manifest of a board with the same module at the wanted builder ref.

Overlay keys (all optional, applied in this order):
  set:                 deep-merged into the manifest (null deletes a key)
  remove_components:   ids dropped from add_components
  remove_sdk_extensions: ids dropped from sdk_extension
  remove_defines:      regexes; matching keys are dropped from c_defines,
                       configuration and slcp_defines
  set_matching:        regex -> value; every EXISTING key in c_defines /
                       configuration that matches is set. A regex that matches
                       nothing is an error (the upstream manifest changed).
  pins:                prefix -> {TX: PA06, RX: PA05, CTS: PC01, RTS: PD02};
                       sets <prefix><SIG>_PORT / _PIN in c_defines in the port
                       notation the upstream manifest uses (gpioPortA in the
                       Gecko SDK, SL_GPIO_PORT_A in the Simplicity SDK)
  add_components:      appended to add_components
  set_defines:         merged into c_defines (added when missing)
"""
import copy, re


class OverlayError(Exception):
    pass


def deep_merge(dst, src):
    for k, v in src.items():
        if v is None:
            dst.pop(k, None)
        elif isinstance(v, dict):
            if not isinstance(dst.get(k), dict):
                dst[k] = {}
            deep_merge(dst[k], v)
            if not dst[k] and not v:
                dst[k] = {}
        else:
            dst[k] = copy.deepcopy(v)
    return dst


def _port_style(defines):
    for k, v in defines.items():
        if k.endswith("_PORT") and isinstance(v, str):
            if v.startswith("gpioPort"):
                return "gpioPort{}"
            if v.startswith("SL_GPIO_PORT_"):
                return "SL_GPIO_PORT_{}"
    return "SL_GPIO_PORT_{}"


def _value_slot(defines, key):
    """c_defines values are either scalars or {type: c_flag, value: x}."""
    cur = defines.get(key)
    return isinstance(cur, dict) and "value" in cur


def _set_define(defines, key, value):
    if _value_slot(defines, key):
        defines[key]["value"] = value
    else:
        defines[key] = value


def apply(base, overlay):
    """Return a new manifest: base with the overlay applied."""
    m = copy.deepcopy(base)
    ov = overlay or {}
    unknown = set(ov) - {"set", "remove_components", "remove_sdk_extensions", "remove_defines",
                         "set_matching", "pins", "add_components", "set_defines"}
    if unknown:
        raise OverlayError(f"unknown overlay keys: {sorted(unknown)}")

    deep_merge(m, ov.get("set") or {})

    drop = set(ov.get("remove_components") or [])
    if drop:
        m["add_components"] = [c for c in m.get("add_components") or [] if c.get("id") not in drop]
    drop = set(ov.get("remove_sdk_extensions") or [])
    if drop:
        m["sdk_extension"] = [e for e in m.get("sdk_extension") or [] if e.get("id") not in drop]
    for key in ("add_components", "sdk_extension"):
        if key in m and not m[key]:
            del m[key]

    sections = [m[s] for s in ("c_defines", "configuration", "slcp_defines") if isinstance(m.get(s), dict)]
    for pat in ov.get("remove_defines") or []:
        rx = re.compile(pat)
        for sec in sections:
            for k in [k for k in sec if rx.fullmatch(k)]:
                del sec[k]

    for pat, value in (ov.get("set_matching") or {}).items():
        rx = re.compile(pat)
        hits = 0
        for sec in sections[:2]:
            for k in [k for k in sec if rx.fullmatch(k)]:
                _set_define(sec, k, value)
                hits += 1
        if not hits:
            raise OverlayError(f"set_matching: nothing in the upstream manifest matches {pat!r}")

    defines = m.setdefault("c_defines", {})
    style = _port_style(defines)
    for prefix, sigs in (ov.get("pins") or {}).items():
        for sig, pin in sigs.items():
            mm = re.fullmatch(r"P([A-K])(\d{1,2})", str(pin).upper())
            if not mm:
                raise OverlayError(f"pins: {pin!r} is not a pin name like PA06")
            _set_define(defines, f"{prefix}{sig}_PORT", style.format(mm.group(1)))
            _set_define(defines, f"{prefix}{sig}_PIN", int(mm.group(2)))

    if ov.get("add_components"):
        m.setdefault("add_components", []).extend(copy.deepcopy(ov["add_components"]))
    for k, v in (ov.get("set_defines") or {}).items():
        if v is None:
            defines.pop(k, None)
        else:
            defines[k] = copy.deepcopy(v)
    return m


def set_sdk_version(manifest, version):
    """Replace the version in 'sdk: "simplicity_sdk:2026.6.1"', keeping the SDK family."""
    family = str(manifest.get("sdk", "")).split(":")[0]
    if not family:
        raise OverlayError("upstream manifest has no sdk field")
    manifest["sdk"] = f"{family}:{version}"
    return manifest
