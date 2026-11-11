#!/usr/bin/env python3
# TEST HELPER: probe /dev/ttyS1 at several baud/parity/RTS settings with an
# H5 SYNC and an H4 HCI reset, and print whatever the chip answers.
import os, sys, termios, time, fcntl, struct, select

DEV = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyS1"
BAUDS = [115200, 1500000, 921600, 230400, 460800, 1000000, 2000000, 3000000]
H5_SYNC = bytes.fromhex("c0002f00d0017ec0")
H4_RESET = bytes.fromhex("01030c00")
TIOCM_RTS = 0x004

def setup(fd, baud, parity, crtscts):
    a = termios.tcgetattr(fd)
    a[0] = 0; a[1] = 0; a[3] = 0
    c = termios.CS8 | termios.CREAD | termios.CLOCAL
    if parity: c |= termios.PARENB
    if crtscts: c |= termios.CRTSCTS
    a[2] = c
    b = getattr(termios, "B%d" % baud)
    a[4] = b; a[5] = b
    a[6][termios.VMIN] = 0; a[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, a)

def rd(fd, t):
    out = b""; end = time.time() + t
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            out += os.read(fd, 4096)
    return out

fd = os.open(DEV, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
bits = struct.unpack("I", fcntl.ioctl(fd, termios.TIOCMGET, b"\0\0\0\0"))[0]
print("modem bits 0x%x (CTS=%d RTS=%d)" % (bits, bool(bits & 0x20), bool(bits & 4)))
setup(fd, 115200, True, False)
idle = rd(fd, 1.0)
print("idle listen 115200E:", idle.hex() or "-")
for baud in BAUDS:
    for parity in (True, False):
        setup(fd, baud, parity, False)
        termios.tcflush(fd, termios.TCIOFLUSH)
        idle = rd(fd, 0.15)
        os.write(fd, H5_SYNC); a = rd(fd, 0.3)
        os.write(fd, H4_RESET); b = rd(fd, 0.3)
        if idle or a or b:
            print("%7d %s idle=%s sync->%s reset->%s" % (baud, "E" if parity else "N", idle.hex() or "-", a.hex() or "-", b.hex() or "-"))
bits = struct.unpack("I", fcntl.ioctl(fd, termios.TIOCMGET, b"\0\0\0\0"))[0]
print("end modem bits 0x%x (CTS=%d)" % (bits, bool(bits & 0x20)))
print("probe done")
