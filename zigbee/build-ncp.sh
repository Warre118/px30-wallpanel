#!/usr/bin/env bash
# Step 2: set the pinout, build and make the GBL for the TYZS13 EmberZNet NCP (UART, RTS/CTS).
set -euo pipefail
# Overridable (updater/gsdk/build.sh sets these in the build container):
#   T, GCC_DIR, OUT as in the gen script; DIST output folder; GSDK_LABEL the
#   SDK part of the file name; MKGBL a program "MKGBL <elf> <out.gbl>" used
#   instead of Simplicity Commander to make the GBL
T="${T:-$HOME/px30-build/silabs}"
GCC_DIR="${GCC_DIR:-$T/arm-gnu-toolchain-12.2.rel1-x86_64-arm-none-eabi}"
GSDK_LABEL="${GSDK_LABEL:-gsdk4.4.6}"
export PATH="$GCC_DIR/bin:$T/commander/commander-cli:$PATH"
OUT="${OUT:-$HOME/px30-build/zigbee/ncp-uart-hw}"
HERE="$(cd "$(dirname "$0")" && pwd)"
BAUD="${BAUD:-115200}"
python3 "$HERE/patch-ncp.py" "$OUT" "$BAUD"
cd "$OUT"
# the project name depends on the slc-cli version: <sample>.Makefile (slc 5)
# or <-name>.Makefile (slc 6)
MK="$(ls *.Makefile | head -1)"; P="${MK%.Makefile}"
[ -n "$P" ] || { echo "ERROR: no generated makefile in $OUT"; exit 1; }
make -f "$MK" clean >/dev/null
make -f "$MK" -j"$(nproc)" ARM_GCC_DIR="$GCC_DIR" 2>&1 | grep -iE "warning|error|Linking|Done" | tail -15
ELF=build/debug/$P.out
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

DIST="${DIST:-$HOME/px30-build/zigbee/dist}"; mkdir -p "$DIST"
# GSDK 4.4.6 reports itself as EmberZNet 7.5.0.0 (stack/config/config.h), EZSP protocol v13.
NAME="ncp-uart-hw-tyzs13-${EZSP_LABEL:-ezsp13}-${GSDK_LABEL}-${BAUD}-hwflow"
if [ -n "${MKGBL:-}" ]; then $MKGBL build/debug/$P.out "$DIST/$NAME.gbl"
else commander-cli gbl create "$DIST/$NAME.gbl" --app build/debug/$P.s37; fi
cp build/debug/$P.s37 "$DIST/$NAME.s37"
cp build/debug/$P.hex "$DIST/$NAME.hex"
mkdir -p "${PX30_OUT:-$HOME/px30-out}/zigbee"
cd "$DIST" && sha256sum "$NAME".gbl "$NAME".s37 "$NAME".hex | tee "$NAME.sha256"
cp "$NAME".gbl "$NAME".s37 "$NAME".hex "$NAME.sha256" "${PX30_OUT:-$HOME/px30-out}/zigbee"/
ls -la "$NAME".*
