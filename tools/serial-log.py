#!/usr/bin/env python3
"""
Seriële console van het panel loggen (debug-UART2, 1 500 000 8N1).
Gebruik: python tools/serial-log.py [COMx] [baud] [logbestand] [seconden]
Zonder COM-poort: neemt de enige USB-seriële poort die er is.
Schrijft ruwe bytes naar het logbestand (ook halve regels), stopt na N s.
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
        sys.exit("geen of meerdere USB-seriële poorten: " + ", ".join(f"{p.device} ({p.description})" for p in usb))
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
print(f"klaar: {n} bytes", flush=True)
