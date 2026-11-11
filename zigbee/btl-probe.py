#!/usr/bin/env python3
"""Look for a way into the TYZS13 Gecko bootloader without SWD.

The only EFR32 pins the panel can drive are RX (PA1, through our TX) and
CTS (PF4, through our RTS). If Tuya configured one of them for GPIO
activation of the bootloader, the bootloader starts when that pin is in
the right state at reset.

The script walks through 4 states (TX low/high x RTS on/off). For each
state the user briefly taps the RST pad to GND. After every reset:
  - a Spinel reset notification (7e 80 06 00 7x) -> app started, next state;
  - a menu with "BL >" in answer to "\\r\\n" -> bootloader found, stop.
Writes nothing to the module; the bootloader stays in its menu.
Usage: sudo python3 btl-probe.py [/dev/ttyThread] [timeout_s]
"""
import fcntl, os, select, struct, sys, termios, time

DEV = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyThread"
TIMEOUT = float(sys.argv[2]) if len(sys.argv) > 2 else 240
TIOCM_RTS = 0x004
WINDOW = float(os.environ.get("WINDOW", 25))

fd = os.open(DEV, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd)
a[0] = 0; a[1] = 0; a[3] = 0                       # raw
a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL   # no CRTSCTS
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
    name = f"TX {'LOW' if low_tx else 'high'}, RTS {'on' if rts_on else 'off'}"
    print(f"\n>>> stand: {name} -- tik nu RST kort tegen GND", flush=True)
    rts(rts_on); brk(low_tx); read(0.3)
    st_end = time.time() + WINDOW  # fixed time: with RTS off the app cannot report its reset
    while time.time() < min(t_end, st_end):
        data = read(1.5)
        if b"\x7e\x80\x06\x00" in data:
            print(f"    app started (Spinel reset) -> no bootloader in this state", flush=True)
            break
        # release briefly and ask the bootloader for its menu
        brk(False); os.write(fd, b"\r\n"); data += read(0.6); brk(low_tx)
        if b"BL >" in data or b"ootloader" in data:
            brk(False)
            print("    *** BOOTLOADER GEVONDEN ***", flush=True)
            print(data.decode("latin1"), flush=True)
            sys.exit(0)
        if b"\x7e\x80\x06\x00" in data:
            print(f"    app started (Spinel reset) -> no bootloader in this state", flush=True)
            break
    else:
        print("    window over, no bootloader", flush=True)
brk(False); rts(False)
print("\ngeen bootloader-ingang gevonden via RX/CTS", flush=True)
sys.exit(1)
