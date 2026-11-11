#!/usr/bin/env bash
# Step 2: set the pinout, build and make the GBL for the TYZS13 EmberZNet NCP (UART, RTS/CTS).
set -euo pipefail
T="$HOME/px30-build/silabs"
export PATH="$T/arm-gnu-toolchain-12.2.rel1-x86_64-arm-none-eabi/bin:$T/commander/commander-cli:$PATH"
OUT="$HOME/px30-build/zigbee/ncp-uart-hw"
HERE="$(cd "$(dirname "$0")" && pwd)"
BAUD="${BAUD:-115200}"
python3 "$HERE/patch-ncp.py" "$OUT" "$BAUD"
cd "$OUT"
make -f ncp-uart-hw.Makefile clean >/dev/null
make -f ncp-uart-hw.Makefile -j"$(nproc)" ARM_GCC_DIR="$T/arm-gnu-toolchain-12.2.rel1-x86_64-arm-none-eabi" 2>&1 | grep -iE "warning|error|Linking|Done" | tail -15
ELF=build/debug/ncp-uart-hw.out
grep -nE "FLASH|ORIGIN" autogen/linkerfile.ld | head -2
arm-none-eabi-size "$ELF"

# Check the return to the bootloader. In EmberZNet 7.x the EZSP handler is in libzigbee-ncp-uart.a:
#  - processEzspCommand(LAUNCH_STANDALONE_BOOTLOADER): if getBootloaderVersion() != 0xFFFF
#    (via bootloader_getInfo, so the bootloader_interface component is needed) -> set a flag;
#  - sli_zigbee_ncp_tick_callback: flag + serialOkToBootload() -> halInternalSysReset(0x0202 =
#    RESET_BOOTLOADER_BOOTLOAD): writes 0x0202/0xF00F to 0x20000000 -> the Gecko bootloader stays
#    in its XMODEM menu. halLaunchStandaloneBootloader (legacy_bootloader_interface) is not needed.
NM=$(arm-none-eabi-nm "$ELF")
grep -qE " T bootloader_getInfo$" <<<"$NM" || { echo "ERROR: bootloader_getInfo missing"; exit 1; }
grep -qE " B sli_zigbee_launch_standalone_bootloader$" <<<"$NM" || { echo "ERROR: EZSP bootloader flag missing"; exit 1; }
arm-none-eabi-objdump -d "$ELF" | awk '/<sli_zigbee_ncp_tick_callback>:/,/^$/' \
  | grep -A1 -E "movw\s+r0, #514" | grep -q "<halInternalSysReset>" \
  && echo "bootloader return (EZSP launchStandaloneBootloader): OK" \
  || { echo "ERROR: no return to the bootloader in the image"; exit 1; }

DIST="$HOME/px30-build/zigbee/dist"; mkdir -p "$DIST"
# GSDK 4.4.6 reports itself as EmberZNet 7.5.0.0 (stack/config/config.h), EZSP protocol v13.
NAME="ncp-uart-hw-tyzs13-ezsp13-gsdk4.4.6-${BAUD}-hwflow"
commander-cli gbl create "$DIST/$NAME.gbl" --app build/debug/ncp-uart-hw.s37
cp build/debug/ncp-uart-hw.s37 "$DIST/$NAME.s37"
cp build/debug/ncp-uart-hw.hex "$DIST/$NAME.hex"
mkdir -p "${PX30_OUT:-$HOME/px30-out}/zigbee"
cd "$DIST" && sha256sum "$NAME".gbl "$NAME".s37 "$NAME".hex | tee "$NAME.sha256"
cp "$NAME".gbl "$NAME".s37 "$NAME".hex "$NAME.sha256" "${PX30_OUT:-$HOME/px30-out}/zigbee"/
ls -la "$NAME".*
