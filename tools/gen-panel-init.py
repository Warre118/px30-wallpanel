#!/usr/bin/env python3
"""
Haalt panel-init-sequence uit dts/android-live.dts (Rockchip-formaat:
type, delay_ms, len, data...) en schrijft een C-array die de driver en
dsiprobe letterlijk afspelen. Gebruik: gen-panel-init.py <android-live.dts> <uit.h>
"""
import re, sys

src = open(sys.argv[1]).read()
m = re.search(r"panel-init-sequence = \[([0-9a-f ]+)\];", src)
assert m, "geen panel-init-sequence"
b = [int(x, 16) for x in m.group(1).split()]
cmds, i = [], 0
while i < len(b):
    typ, delay, n = b[i], b[i + 1], b[i + 2]
    data = b[i + 3:i + 3 + n]
    assert len(data) == n and typ in (0x05, 0x15, 0x39), f"ongeldig commando op {i}"
    cmds.append((delay, data))
    i += 3 + n
out = ["/* Gegenereerd door tools/gen-panel-init.py uit de Android-DTB. Niet met de hand wijzigen. */",
       "/* Per commando: delay_ms, len, data... (DCS, letterlijk zoals de vendor-kernel) */",
       "static const u8 smatek_px30_480_init[] = {"]
for delay, data in cmds:
    out.append("\t%d, %d, %s," % (delay, len(data), ", ".join("0x%02x" % x for x in data)))
out.append("};")
open(sys.argv[2], "w").write("\n".join(out) + "\n")
print(f"{len(cmds)} commando's, {len(b)} bytes")
