#!/usr/bin/env python3
"""Import the boot animation from the panel's own Android firmware.

The vendor artwork is not part of this repository. Copy
/oem/media/bootanimation.zip from your panel (see docs/flashing.md, it is
also in the eMMC backup) and run, before building the rootfs:

    python3 tools/import-bootanim.py bootanimation.zip

Writes into rootfs/overlay/usr/share/:
  plymouth/themes/px30-wallpanel/intro.png   part0 (shown once)
  plymouth/themes/px30-wallpanel/f<N>.png    part1 frames (looped by Plymouth)
  kiosk/bootanim.png                         part1 as APNG for the loader page

Without these files the panel boots with a black splash instead.
Needs python3-pil.
"""
import io
import os
import sys
import zipfile

from PIL import Image

here = os.path.dirname(os.path.abspath(__file__))
share = os.path.join(here, "..", "rootfs", "overlay", "usr", "share")
theme = os.path.join(share, "plymouth", "themes", "px30-wallpanel")
apng = os.path.join(share, "kiosk", "bootanim.png")

if len(sys.argv) != 2:
    sys.exit(__doc__)
z = zipfile.ZipFile(sys.argv[1])
names = sorted(n for n in z.namelist() if n.lower().endswith(".png"))
part0 = [n for n in names if n.startswith("part0/")]
part1 = [n for n in names if n.startswith("part1/")]
if not part0 or not part1:
    sys.exit("unexpected bootanimation.zip layout: need part0/ and part1/")
w, h, fps = (int(x) for x in z.read("desc.txt").decode().split()[:3])
print(f"{w}x{h} @ {fps} fps, part1: {len(part1)} frames")


def load(n):
    return Image.open(io.BytesIO(z.read(n))).convert("RGB")


load(part0[0]).save(os.path.join(theme, "intro.png"), optimize=True)
frames = []
for i, n in enumerate(part1):
    im = load(n)
    im.save(os.path.join(theme, f"f{i}.png"), optimize=True)
    frames.append(im)
frames[0].save(apng, save_all=True, append_images=frames[1:], duration=1000 // fps, loop=0, optimize=True)

script = os.path.join(theme, "px30-wallpanel.script")
s = open(script).read()
if f"FRAMES = {len(part1)};" not in s:
    print(f"note: set FRAMES = {len(part1)}; in {script}")
print("done:", theme, apng)
