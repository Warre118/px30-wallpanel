#!/usr/bin/env python3
"""Screenshot van een DRM-framebuffer (XRGB/ARGB8888) naar PNG, alleen stdlib.

Gebruik (root):  python3 fbshot.py <fb_id> /tmp/shot.png
fb_id: zie /sys/kernel/debug/dri/0/state (plane-0 -> fb=NN).
"""
import fcntl, mmap, os, struct, sys, zlib

GETFB = 0xC01C64AD      # DRM_IOCTL_MODE_GETFB
MAP_DUMB = 0xC01064B3   # DRM_IOCTL_MODE_MAP_DUMB

fb_id, out = int(sys.argv[1]), sys.argv[2]
fd = os.open("/dev/dri/card0", os.O_RDWR)
buf = bytearray(struct.pack("7I", fb_id, 0, 0, 0, 0, 0, 0))
fcntl.ioctl(fd, GETFB, buf)
_, w, h, pitch, bpp, depth, handle = struct.unpack("7I", buf)
try:
    m = bytearray(struct.pack("IIQ", handle, 0, 0))
    fcntl.ioctl(fd, MAP_DUMB, m)
    off = struct.unpack("IIQ", m)[2]
    mm = mmap.mmap(fd, pitch * h, mmap.MAP_SHARED, mmap.PROT_READ, offset=off)
except OSError:
    # Geïmporteerde buffer (GPU/Mesa via dma-buf): exporteer als dma-buf en mmap die.
    p = bytearray(struct.pack("IIi", handle, os.O_CLOEXEC, -1))
    fcntl.ioctl(fd, 0xC00C642D, p)  # DRM_IOCTL_PRIME_HANDLE_TO_FD
    dfd = struct.unpack("IIi", p)[2]
    mm = mmap.mmap(dfd, pitch * h, mmap.MAP_SHARED, mmap.PROT_READ)
data = mm[:]
raw = bytearray()
nonblack = 0
for y in range(h):
    raw.append(0)
    row = data[y * pitch: y * pitch + w * 4]
    for x in range(0, w * 4, 4):
        b, g, r = row[x], row[x + 1], row[x + 2]
        raw += bytes((r, g, b))
        if r | g | b:
            nonblack += 1
def chunk(t, d):
    return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) \
    + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b"")
open(out, "wb").write(png)
print(f"{w}x{h} pitch={pitch} bpp={bpp} niet-zwarte pixels: {nonblack}/{w*h}")
