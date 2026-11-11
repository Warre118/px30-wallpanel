#!/usr/bin/env python3
"""Minimale TCP<->UART-brug zonder pyserial (alleen stdlib).

Gebruik op het paneel:  sudo python3 tcp-serial-bridge.py /dev/ttyS0 115200 6638 [flow|noflow] [luisteradres]
Tunnel vanaf de pc:     ssh -N -L 6638:127.0.0.1:6638 admin@<panel-ip>
Daarna:                 universal-silabs-flasher --device socket://127.0.0.1:6638 probe
Eén client tegelijk; 8N1, raw, hardware-flowcontrol (RTS/CTS).
"""
import os, socket, selectors, sys, termios, tty

dev, baud, port = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
B = {115200: termios.B115200, 230400: termios.B230400, 460800: termios.B460800}[baud]
# 4e argument "noflow": zonder RTS/CTS (de Gecko-bootloader van de TYZS13 gebruikt geen flowcontrol)
FLOW = 0 if (len(sys.argv) > 4 and sys.argv[4] == "noflow") else termios.CRTSCTS

fd = os.open(dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
tty.setraw(fd)
a = termios.tcgetattr(fd)
a[2] = (a[2] & ~(termios.CSIZE | termios.PARENB | termios.CSTOPB | termios.CRTSCTS)) | termios.CS8 | termios.CREAD | termios.CLOCAL | FLOW
a[4] = a[5] = B
termios.tcsetattr(fd, termios.TCSANOW, a)
termios.tcflush(fd, termios.TCIOFLUSH)

srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
# 5e argument: luisteradres (standaard enkel lokaal; px30-zigbee-bridge leest het uit radio.conf)
BIND = sys.argv[5] if len(sys.argv) > 5 else "127.0.0.1"
srv.bind((BIND, port))
srv.listen(1)
print(f"bridge {dev}@{baud} <-> tcp/{port}", flush=True)
while True:
    c, addr = srv.accept(); c.setblocking(False)
    c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    print("client", addr, flush=True)
    sel = selectors.DefaultSelector()
    sel.register(fd, selectors.EVENT_READ, "uart"); sel.register(c, selectors.EVENT_READ, "net")
    try:
        while True:
            for k, _ in sel.select():
                if k.data == "uart":
                    d = os.read(fd, 4096)
                    if d: c.sendall(d)
                else:
                    d = c.recv(4096)
                    if not d: raise ConnectionError
                    while d:
                        try: n = os.write(fd, d); d = d[n:]
                        except BlockingIOError: sel.select(0.01)
    except (ConnectionError, OSError) as e:
        print("client weg:", e, flush=True)
    finally:
        sel.close(); c.close()
