#!/usr/bin/env python3
# TEST HELPER: minimal H5 (three-wire) host on /dev/ttyS1: link establishment,
# then a few HCI commands; prints every frame the chip sends.
import os, sys, termios, time, select

DEV = "/dev/ttyS1"
BAUD = int(sys.argv[1]) if len(sys.argv) > 1 else 115200
CMDS = [bytes.fromhex(x) for x in (sys.argv[2:] or ["011000", "030c00", "011000"])]

fd = os.open(DEV, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd)
a[0] = 0; a[1] = 0; a[3] = 0
a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL | termios.PARENB
b = getattr(termios, "B%d" % BAUD); a[4] = b; a[5] = b
a[6][termios.VMIN] = 0; a[6][termios.VTIME] = 0
termios.tcsetattr(fd, termios.TCSANOW, a)
termios.tcflush(fd, termios.TCIOFLUSH)
t0 = time.time()
buf = b""; txseq = 0; txack = 0

def slip(p):
    o = bytearray([0xc0])
    for c in p:
        if c == 0xc0: o += b"\xdb\xdc"
        elif c == 0xdb: o += b"\xdb\xdd"
        else: o.append(c)
    o.append(0xc0); return bytes(o)

USE_CRC = os.environ.get("H5CRC", "1") == "1"

def crc16(data):
    c = 0xffff
    for x in data:
        c ^= x
        for _ in range(8):
            c = (c >> 1) ^ 0x8408 if c & 1 else c >> 1
    c = int("{:016b}".format(c)[::-1], 2)
    return bytes([c >> 8, c & 0xff])

def frame(ptype, payload, rel):
    global txseq
    crc = USE_CRC and ptype != 15
    h0 = (txack << 3) | (0x80 | txseq if rel else 0) | (0x40 if crc else 0)
    if rel: txseq = (txseq + 1) & 7
    h1 = ptype | ((len(payload) & 0xf) << 4); h2 = len(payload) >> 4
    h3 = (~(h0 + h1 + h2)) & 0xff
    body = bytes([h0, h1, h2, h3]) + payload
    if crc: body += crc16(body)
    return slip(body)

def tx(ptype, payload, rel=False, note=""):
    os.write(fd, frame(ptype, payload, rel))
    print("%6.3f TX type=%d rel=%d %s %s" % (time.time() - t0, ptype, rel, payload.hex(), note))

def frames(timeout):
    global buf, txack
    out = []; end = time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.02)
        if r: buf += os.read(fd, 4096)
        while True:
            i = buf.find(b"\xc0")
            if i < 0: buf = b""; break
            j = buf.find(b"\xc0", i + 1)
            if j < 0: buf = buf[i:]; break
            raw = buf[i + 1:j]; buf = buf[j:]
            if not raw: continue
            raw = raw.replace(b"\xdb\xdc", b"\xc0").replace(b"\xdb\xdd", b"\xdb")
            if len(raw) < 4: print("       short", raw.hex()); continue
            h = raw[:4]; p = raw[4:]
            ok = (sum(h) & 0xff) == 0xff
            seq = h[0] & 7; ack = (h[0] >> 3) & 7; rel = h[0] >> 7; typ = h[1] & 0xf
            if h[0] & 0x40 and len(p) >= 2:
                if crc16(raw[:-2]) != raw[-2:]: print("       BADCRC")
                p = p[:-2]
            print("%6.3f RX type=%d rel=%d seq=%d ack=%d %s%s" % (time.time() - t0, typ, rel, seq, ack, p.hex(), "" if ok else " BADHDR"))
            if rel and ok:
                txack = (seq + 1) & 7
                tx(0, b"", note="(ack)")
            out.append((typ, p))
        if out and timeout < 0.5: break
    return out

print("== listen"); frames(1.0)
print("== link establishment")
active = False; got_sync_rsp = False
for n in range(40):
    if not got_sync_rsp: tx(15, b"\x01\x7e", note="SYNC")
    else: tx(15, b"\x03\xfc\x14", note="CONFIG")
    for typ, p in frames(0.25):
        if typ != 15: continue
        if p[:2] == b"\x01\x7e": tx(15, b"\x02\x7d", note="SYNC_RSP")
        elif p[:2] == b"\x02\x7d": got_sync_rsp = True
        elif p[:2] == b"\x03\xfc": tx(15, b"\x04\x7b", note="CONFIG_RSP")
        elif p[:2] == b"\x04\x7b": active = True
    if active: break
print("link active:", active)
frames(0.3)
for c in CMDS:
    print("== HCI cmd", c.hex())
    tx(1, c, rel=True)
    frames(1.5)
print("== final listen"); frames(1.0)
