"""Read the metadata tag of a Gecko Bootloader (GBL) file.

A GBL is a list of tags: 4-byte id, 4-byte length (both little endian), data.
silabs-firmware-builder (and updater/gsdk/mkgbl.py) put a JSON document in the
metadata tag; universal-silabs-flasher reads the same tag.
"""
import json, struct

TAG_HEADER = 0x03A617EB
TAG_METADATA = 0xF60808F6
TAG_END = 0xFC0404FC


def tags(data):
    pos = 0
    while pos + 8 <= len(data):
        tag, length = struct.unpack_from("<II", data, pos)
        pos += 8
        if pos + length > len(data):
            raise ValueError("truncated GBL tag")
        yield tag, data[pos:pos + length]
        pos += length
        if tag == TAG_END:
            return
    raise ValueError("GBL has no end tag")


def read_metadata(path):
    """The metadata JSON of a GBL file ({} when it has none). Raises ValueError
    when the file is not a GBL."""
    with open(path, "rb") as f:
        data = f.read()
    meta = {}
    first = True
    for tag, body in tags(data):
        if first and tag != TAG_HEADER:
            raise ValueError("not a GBL file (no header tag)")
        first = False
        if tag == TAG_METADATA:
            try:
                meta = json.loads(body.decode("utf-8"))
            except ValueError:
                meta = {}
    if first:
        raise ValueError("empty GBL file")
    return meta if isinstance(meta, dict) else {}


def firmware_version(fw_type, meta, fallback=None):
    """The version a host would report for this image."""
    if fw_type == "zigbee_ncp" and meta.get("ezsp_version"):
        return str(meta["ezsp_version"])
    if fw_type == "openthread_rcp" and meta.get("ot_rcp_version"):
        # "SL-OPENTHREAD/2.5.2.0_GitHub-1fceb225b" -> "2.5.2.0"
        return str(meta["ot_rcp_version"]).split("/")[-1].split("_")[0]
    if meta.get("sdk_version"):
        return str(meta["sdk_version"])
    return fallback
