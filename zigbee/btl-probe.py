#!/usr/bin/env python3
"""Zoek een ingang naar de Gecko-bootloader van de TYZS13 zonder SWD.

De enige EFR32-pinnen die het panel kan sturen zijn RX (PA1, via onze TX)
en CTS (PF4, via onze RTS). Mocht Tuya een daarvan als GPIO-activatie van
de bootloader ingesteld hebben, dan start de bootloader als die pin bij de
reset in de juiste stand staat.

Het script loopt 4 standen af (TX laag/hoog x RTS aan/uit). De gebruiker
tikt per stand kort de RST-pad tegen GND. Na elke reset:
  - komt er een Spinel-resetmelding (7e 80 06 00 7x) -> app gestart, volgende stand;
  - antwoordt er op "\\r\\n" een menu met "BL >" -> bootloader gevonden, stop.
Schrijft niets naar de module; de bootloader blijft in zijn menu staan.
Gebruik: sudo python3 btl-probe.py [/dev/ttyThread] [timeout_s]
"""
import fcntl, os, select, struct, sys, termios, time

DEV = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyThread"
TIMEOUT = float(sys.argv[2]) if len(sys.argv) > 2 else 240
TIOCM_RTS = 0x004
WINDOW = float(os.environ.get("WINDOW", 25))

fd = os.open(DEV, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd)
a[0] = 0; a[1] = 0; a[3] = 0                       # raw
a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL   # geen CRTSCTS
a[4] = a[5] = termios.B115200
termios.tcsetattr(fd, termios.TCSANOW, a)

def rts(on):
    fcntl.ioctl(fd, termios.TIOCMBIS if on else termios.TIOCMBIC, struct.pack("I", TIOCM_RTS))

def brk(on):
    fcntl.ioctl(fd, 0x5427 if on else 0x5428)

def read(t):
    buf = b""; end = time.time() + t
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try: buf += os.read(fd, 4096)
            except BlockingIOError: pass
    return buf

STATES = [(False, True), (False, False), (True, True), (True, False)]
t_end = time.time() + TIMEOUT
for low_tx, rts_on in STATES:
    name = f"TX {'LAAG' if low_tx else 'hoog'}, RTS {'aan' if rts_on else 'uit'}"
    print(f"\n>>> stand: {name} -- tik nu RST kort tegen GND", flush=True)
    rts(rts_on); brk(low_tx); read(0.3)
    st_end = time.time() + WINDOW  # vaste tijd: met RTS uit kan de app zijn reset niet melden
    while time.time() < min(t_end, st_end):
        data = read(1.5)
        if b"\x7e\x80\x06\x00" in data:
            print(f"    app gestart (Spinel-reset) -> geen bootloader in deze stand", flush=True)
            break
        # kort loslaten en de bootloader om zijn menu vragen
        brk(False); os.write(fd, b"\r\n"); data += read(0.6); brk(low_tx)
        if b"BL >" in data or b"ootloader" in data:
            brk(False)
            print("    *** BOOTLOADER GEVONDEN ***", flush=True)
            print(data.decode("latin1"), flush=True)
            sys.exit(0)
        if b"\x7e\x80\x06\x00" in data:
            print(f"    app gestart (Spinel-reset) -> geen bootloader in deze stand", flush=True)
            break
    else:
        print("    venster voorbij, geen bootloader", flush=True)
brk(False); rts(False)
print("\ngeen bootloader-ingang gevonden via RX/CTS", flush=True)
sys.exit(1)
