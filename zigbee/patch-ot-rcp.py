#!/usr/bin/env python3
"""Put the TYZS13 pinout (USART0, HW flow) into the generated ot-rcp config.

TYZS13 / EFR32MG13P732F512GM48 -> PX30 UART5:
  TX  PA0 LOC0   RX  PA1 LOC0   CTS PF4 LOC24   RTS PA2 LOC29
LF clock: no LFXO on the module; slc already generates LFRCO for LFA/LFB/LFE.
"""
import re, sys, pathlib

cfg = pathlib.Path(sys.argv[1]) / "config" / "sl_uartdrv_usart_vcom_config.h"
src = cfg.read_text()
pins = {
    "PERIPHERAL": "USART0", "PERIPHERAL_NO": "0",
    "TX_PORT": "gpioPortA", "TX_PIN": "0", "TX_LOC": "0",
    "RX_PORT": "gpioPortA", "RX_PIN": "1", "RX_LOC": "0",
    "CTS_PORT": "gpioPortF", "CTS_PIN": "4", "CTS_LOC": "24",
    "RTS_PORT": "gpioPortA", "RTS_PIN": "2", "RTS_LOC": "29",
}
for k, v in pins.items():
    pat = re.compile(r"^(?://\s*)?#define SL_UARTDRV_USART_VCOM_%s\s+.*$" % k, re.M)
    src, n = pat.subn("#define SL_UARTDRV_USART_VCOM_%-16s %s" % (k, v), src)
    if n != 1:
        sys.exit("macro %s: %d treffers" % (k, n))
src = src.replace('#warning "UARTDRV USART peripheral not configured"\n', "")
baud = sys.argv[2] if len(sys.argv) > 2 else "115200"
src = re.sub(r"(#define SL_UARTDRV_USART_VCOM_BAUDRATE\s+)\d+", r"\g<1>" + baud, src)
cfg.write_text(src)
print("ok", cfg, "baud", baud)

# BELANGRIJK: openthread-core-efr32-config.h test SL_CATALOG_GECKO_BOOTLOADER_INTERFACE_PRESENT
# without including sl_component_catalog.h -> BOOTLOADER_MODE_ENABLE becomes 0 and the RCP
# ignores Spinel RESET_BOOTLOADER (it just restarts). Without this, universal-silabs-flasher
# can NOT get the module into the bootloader any more (only SWD can).
feat = pathlib.Path(sys.argv[1]) / "config" / "sl_openthread_features_config.h"
f = feat.read_text()
marker = "#define OPENTHREAD_CONFIG_PLATFORM_BOOTLOADER_MODE_ENABLE 1"
if marker not in f:
    f = f.rstrip() + "\n\n/* TYZS13: Spinel RESET_BOOTLOADER -> Gecko-bootloader */\n" + marker + "\n"
    feat.write_text(f)
print("ok", feat, "bootloader mode on")
