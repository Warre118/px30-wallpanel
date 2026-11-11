#!/usr/bin/env python3
"""
Log the panel's serial console (UART5 pads or debug UART2).
Usage: python tools/serial-log.py [COMx] [baud] [logfile] [seconds]
Without a COM port: takes the only USB serial port there is.
Writes raw bytes to the log file (also partial lines), stops after N s.
"""
import sys, time
import serial, serial.tools.list_ports

port = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != "-" else None
baud = int(sys.argv[2]) if len(sys.argv) > 2 else 1500000
log = sys.argv[3] if len(sys.argv) > 3 else "serial.log"
dur = float(sys.argv[4]) if len(sys.argv) > 4 else 300

if not port:
    usb = [p for p in serial.tools.list_ports.comports() if p.vid]
    if len(usb) != 1:
        sys.exit("no or several USB serial ports: " + ", ".join(f"{p.device} ({p.description})" for p in usb))
    port = usb[0].device
s = serial.Serial(port, baud, timeout=0.2)
print(f"{port} @ {baud} -> {log} ({dur:.0f} s)", flush=True)
end, n = time.time() + dur, 0
with open(log, "ab") as f:
    f.write(f"\n===== {time.strftime('%H:%M:%S')} {port} @ {baud} =====\n".encode())
    while time.time() < end:
        b = s.read(4096)
        if b:
            f.write(b); f.flush(); n += len(b)
print(f"done: {n} bytes", flush=True)
