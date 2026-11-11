#!/usr/bin/env python3
"""Read 32-bit registers through /dev/mem. Usage: devmem.py <base-hex> <offset-hex>... """
import mmap, os, struct, sys

base = int(sys.argv[1], 16)
fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
m = mmap.mmap(fd, 0x10000, mmap.MAP_SHARED, mmap.PROT_READ, offset=base)
for o in sys.argv[2:]:
    off = int(o, 16)
    print("0x%08x [+0x%03x] = 0x%08x" % (base + off, off, struct.unpack_from("<I", m, off)[0]))
