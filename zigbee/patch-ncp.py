#!/usr/bin/env python3
"""Put the TYZS13 pinout (USART0, HW flow) into the generated ncp-uart-hw config.

Same pinout as patch-ot-rcp.py, but the NCP uses iostream_usart
(SL_IOSTREAM_USART_VCOM_*) i.p.v. uartdrv.
TYZS13 / EFR32MG13P732F512GM48 -> PX30 UART5:
  TX  PA0 LOC0   RX  PA1 LOC0   CTS PF4 LOC24   RTS PA2 LOC29
HFXO 38.4 MHz, DC-DC and LFRCO (no LFXO) are the slc defaults for this device.
"""
import re, sys, pathlib

proj = pathlib.Path(sys.argv[1])
baud = sys.argv[2] if len(sys.argv) > 2 else "115200"
cfg = proj / "config" / "sl_iostream_usart_vcom_config.h"
src = cfg.read_text()
pins = {
    "PERIPHERAL": "USART0", "PERIPHERAL_NO": "0",
    "TX_PORT": "gpioPortA", "TX_PIN": "0", "TX_LOC": "0",
    "RX_PORT": "gpioPortA", "RX_PIN": "1", "RX_LOC": "0",
    "CTS_PORT": "gpioPortF", "CTS_PIN": "4", "CTS_LOC": "24",
    "RTS_PORT": "gpioPortA", "RTS_PIN": "2", "RTS_LOC": "29",
}
for k, v in pins.items():
    pat = re.compile(r"^(?://\s*)?#define SL_IOSTREAM_USART_VCOM_%s\s+.*$" % k, re.M)
    src, n = pat.subn("#define SL_IOSTREAM_USART_VCOM_%-16s %s" % (k, v), src)
    if n != 1:
        sys.exit("macro %s: %d treffers" % (k, n))
src = re.sub(r'#warning "IO Stream USART peripheral not configured"\n', "", src)
src, n = re.subn(r"(#define SL_IOSTREAM_USART_VCOM_BAUDRATE\s+)\d+", r"\g<1>" + baud, src)
assert n == 1, "baudrate"
src, n = re.subn(r"(#define SL_IOSTREAM_USART_VCOM_FLOW_CONTROL_TYPE\s+)\w+",
                 r"\g<1>usartHwFlowControlCtsAndRts", src)
assert n == 1, "flow control"
cfg.write_text(src)
print("ok", cfg, "baud", baud, "RTS/CTS")

# Check the clocks: no LFXO on the module.
clk = (proj / "autogen" / "sl_device_init_clocks.c").read_text()
if "LFXO" in clk:
    sys.exit("ERROR: autogen picks LFXO, but the TYZS13 has none")
hfxo = (proj / "config" / "sl_device_init_hfxo_config.h").read_text()
if not re.search(r"#define SL_DEVICE_INIT_HFXO_FREQ\s+38400000", hfxo):
    sys.exit("ERROR: HFXO is not 38.4 MHz")
print("ok klokken: HFXO 38.4 MHz, LF = LFRCO")
