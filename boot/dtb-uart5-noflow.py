#!/usr/bin/env python3
"""
Debugvariant van de DTB: UART5 (Zigbee) zonder RTS/CTS, zodat de kernelconsole
op ttyS5 niet stilvalt wanneer de Zigbee-module CTS laag trekt (haar buffer
loopt vol met consoletekst). Werkt op de gedecompileerde .dts (dtc -O dts).
Gebruik: dtb-uart5-noflow.py <in.dts> <uit.dts>
"""
import re, sys

s = open(sys.argv[1]).read()
i = s.index("\tserial@ff178000 {")
j = s.index("\n\t};", i)
blk = s[i:j]
m = re.search(r"pinctrl-0 = <(0x[0-9a-f]+) 0x[0-9a-f]+ 0x[0-9a-f]+>;", blk)
assert m and "uart-has-rtscts;" in blk, "UART5-node niet zoals verwacht"
nb = blk.replace(m.group(0), f"pinctrl-0 = <{m.group(1)}>;").replace("\n\t\tuart-has-rtscts;", "")
assert "rtscts" not in nb
open(sys.argv[2], "w").write(s[:i] + nb + s[j:])
print("uart5: enkel xfer-pins, geen RTS/CTS")
