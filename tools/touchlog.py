#!/usr/bin/env python3
"""Log touch-events (ABS_MT_POSITION_X/Y, BTN_TOUCH) van een input-device.
Gebruik: sudo python3 touchlog.py /dev/input/eventN <seconden>"""
import struct, sys, time, select
dev, dur = sys.argv[1], float(sys.argv[2])
f = open(dev, 'rb', buffering=0); t_end = time.time() + dur
x = y = None; down = False
while time.time() < t_end:
    r, _, _ = select.select([f], [], [], 0.5)
    if not r: continue
    ev = f.read(24)
    _, _, typ, code, val = struct.unpack('qqHHi', ev)
    if typ == 3 and code == 0x35: x = val
    elif typ == 3 and code == 0x36: y = val
    elif typ == 1 and code == 0x14a:
        down = bool(val); print(('DOWN' if down else 'UP  '), x, y, flush=True)
