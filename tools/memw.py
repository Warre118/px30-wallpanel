#!/usr/bin/env python3
"""32-bit register lezen/schrijven via /dev/mem (debug).
Gebruik: memw.py <adres-hex>                -> lezen
         memw.py <adres-hex> <waarde-hex>   -> schrijven en teruglezen
         memw.py <adres-hex> set|clr <bits-hex>
"""
import mmap, os, struct, sys
a = int(sys.argv[1], 16); page = a & ~0xfff; off = a - page
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=page)
rd = lambda: struct.unpack_from("<I", m, off)[0]
old = rd()
if len(sys.argv) > 2:
    if sys.argv[2] in ("set", "clr"):
        b = int(sys.argv[3], 16); new = old | b if sys.argv[2] == "set" else old & ~b
    else:
        new = int(sys.argv[2], 16)
    struct.pack_into("<I", m, off, new)
    print(f"{a:08x}: {old:08x} -> {rd():08x}")
else:
    print(f"{a:08x}: {old:08x}")
