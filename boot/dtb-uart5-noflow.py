#!/usr/bin/env python3
"""
Debug variant of the DTB: UART5 (Zigbee) without RTS/CTS, so the kernel
console on ttyS5 does not stall when the Zigbee module pulls CTS low (its
buffer fills up with console text). Works on the decompiled .dts (dtc -O dts).
Usage: dtb-uart5-noflow.py <in.dts> <out.dts>
"""
import re, sys

s = open(sys.argv[1]).read()
i = s.index("\tserial@ff178000 {")
j = s.index("\n\t};", i)
blk = s[i:j]
m = re.search(r"pinctrl-0 = <(0x[0-9a-f]+) 0x[0-9a-f]+ 0x[0-9a-f]+>;", blk)
assert m and "uart-has-rtscts;" in blk, "UART5 node not as expected"
nb = blk.replace(m.group(0), f"pinctrl-0 = <{m.group(1)}>;").replace("\n\t\tuart-has-rtscts;", "")
assert "rtscts" not in nb
open(sys.argv[2], "w").write(s[:i] + nb + s[j:])
print("uart5: xfer pins only, no RTS/CTS")
