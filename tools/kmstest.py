#!/usr/bin/env python3
"""KMS test pattern without libdrm: red/green/blue/white bars, then colour fields.

Usage (root, without another DRM master, so stop the kiosk first):
    python3 kmstest.py [seconds]
Runs entirely from a dumb buffer on the CPU -> tests VOP/DSI/panel apart from GPU/Cog.
"""
import array, fcntl, mmap, os, struct, sys, time

def IOWR(nr, size): return 0xC0000000 | (size << 16) | (0x64 << 8) | nr
GETRES, GETCONN = IOWR(0xA0, 64), IOWR(0xA7, 80)
CREATE_DUMB, MAP_DUMB, ADDFB, SETCRTC = IOWR(0xB2, 32), IOWR(0xB3, 16), IOWR(0xAE, 28), IOWR(0xA2, 104)
secs = float(sys.argv[1]) if len(sys.argv) > 1 else 30

fd = os.open("/dev/dri/card0", os.O_RDWR)
def ptr(a): return a.buffer_info()[0]

r = bytearray(64); fcntl.ioctl(fd, GETRES, r)
nfb, ncrtc, ncon, nenc = struct.unpack_from("4I", r, 32)
crtcs, cons = array.array("I", [0] * ncrtc), array.array("I", [0] * ncon)
encs, fbs = array.array("I", [0] * nenc), array.array("I", [0] * max(nfb, 1))
r = bytearray(struct.pack("4Q4I4I", ptr(fbs), ptr(crtcs), ptr(cons), ptr(encs), nfb, ncrtc, ncon, nenc, 0, 0, 0, 0))
fcntl.ioctl(fd, GETRES, r)

for cid in cons:
    c = bytearray(80); struct.pack_into("I", c, 48, cid); fcntl.ioctl(fd, GETCONN, c)
    nmodes = struct.unpack_from("I", c, 32)[0]
    if not nmodes:
        continue
    modes = bytearray(68 * nmodes)
    mbuf = array.array("B", modes)
    c = bytearray(80); struct.pack_into("Q", c, 8, ptr(mbuf)); struct.pack_into("I", c, 32, nmodes)
    struct.pack_into("I", c, 48, cid); fcntl.ioctl(fd, GETCONN, c)
    mode = mbuf.tobytes()[:68]
    conn = cid
    break
else:
    sys.exit("no connector with modes")
w, h = struct.unpack_from("HH", mode, 4)[0], struct.unpack_from("H", mode, 14)[0]
print(f"connector {conn} crtc {crtcs[0]} mode {w}x{h} '{mode[36:68].rstrip(bytes(1)).decode()}'")

d = bytearray(struct.pack("4I2IQ", h, w, 32, 0, 0, 0, 0)); fcntl.ioctl(fd, CREATE_DUMB, d)
_, _, _, _, handle, pitch, size = struct.unpack("4I2IQ", d)
f = bytearray(struct.pack("7I", 0, w, h, pitch, 32, 24, handle)); fcntl.ioctl(fd, ADDFB, f)
fb = struct.unpack("7I", f)[0]
m = bytearray(struct.pack("IIQ", handle, 0, 0)); fcntl.ioctl(fd, MAP_DUMB, m)
mm = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=struct.unpack("IIQ", m)[2])

def fill(colors):
    band = h // len(colors)
    for i, col in enumerate(colors):
        px = struct.pack("<I", col) * w
        for y in range(i * band, h if i == len(colors) - 1 else (i + 1) * band):
            mm[y * pitch: y * pitch + w * 4] = px

conns = array.array("I", [conn])
if "reset" in sys.argv[2:]:
    # Force a full modeset: CRTC off first (no fb, no mode, no connectors).
    fcntl.ioctl(fd, SETCRTC, bytearray(struct.pack("QIIIIIII", 0, 0, crtcs[0], 0, 0, 0, 0, 0) + bytes(68)))
    print("CRTC off", flush=True)
    time.sleep(2)
s = bytearray(struct.pack("QIIIIIII", ptr(conns), 1, crtcs[0], fb, 0, 0, 0, 1) + mode)
fill([0xFF0000, 0x00FF00, 0x0000FF, 0xFFFFFF])
fcntl.ioctl(fd, SETCRTC, s)
print("red/green/blue/white bars are on the screen")
t_end = time.time() + secs
seq = [0xFFFFFF, 0xFF0000, 0x00FF00, 0x0000FF]
i = 0
time.sleep(min(secs / 2, 15))
while time.time() < t_end:
    fill([seq[i % 4]]); i += 1
    time.sleep(2)
