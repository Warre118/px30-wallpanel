#!/usr/bin/env python3
"""mkgbl.py <elf> <out.gbl> - make a Gecko Bootloader image from a linked ELF.

Replaces `commander gbl create` in the build container (Simplicity Commander
cannot be downloaded without a browser). Uses pygbl, like Nabu Casa's
silabs-firmware-builder. $MKGBL_METADATA (JSON) goes into the metadata tag that
universal-silabs-flasher reads (firmware type, version, baud rate).
"""
import json, os, sys

from pygbl import build_application_gbl3

elf, out = sys.argv[1], sys.argv[2]
meta = os.environ.get("MKGBL_METADATA", "").strip()
metadata = json.dumps(json.loads(meta), sort_keys=True).encode() if meta else None
with open(elf, "rb") as f:
    image = build_application_gbl3(f, metadata=metadata)
with open(out, "wb") as f:
    f.write(image.serialize(block_size=4))      # commander pads to 4 bytes as well
print(f"mkgbl: {out} ({os.path.getsize(out)} bytes)")
