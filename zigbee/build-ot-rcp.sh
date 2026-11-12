#!/usr/bin/env bash
# Step 2: set the pinout, build and make the GBL for the TYZS13 RCP.
set -euo pipefail
# Overridable (updater/gsdk/build.sh sets these in the build container):
#   T, GCC_DIR, OUT as in the gen script; DIST output folder; GSDK_LABEL the
#   SDK part of the file name; MKGBL a program "MKGBL <elf> <out.gbl>" used
#   instead of Simplicity Commander to make the GBL
T="${T:-$HOME/px30-build/silabs}"
GCC_DIR="${GCC_DIR:-$T/arm-gnu-toolchain-12.2.rel1-x86_64-arm-none-eabi}"
GSDK_LABEL="${GSDK_LABEL:-gsdk4.4.6}"
export PATH="$GCC_DIR/bin:$T/commander/commander-cli:$PATH"
OUT="${OUT:-$HOME/px30-build/zigbee/ot-rcp}"
HERE="$(cd "$(dirname "$0")" && pwd)"
BAUD="${BAUD:-115200}"
python3 "$HERE/patch-ot-rcp.py" "$OUT" "$BAUD"
cd "$OUT"
# the project name depends on the slc-cli version: <sample>.Makefile (slc 5)
# or <-name>.Makefile (slc 6)
MK="$(ls *.Makefile | head -1)"; P="${MK%.Makefile}"
[ -n "$P" ] || { echo "ERROR: no generated makefile in $OUT"; exit 1; }
make -f "$MK" clean >/dev/null
make -f "$MK" -j"$(nproc)" ARM_GCC_DIR="$GCC_DIR" 2>&1 | grep -iE "warning|error|Linking|Done" | tail -15
grep -nE "FLASH|ORIGIN" autogen/linkerfile.ld | head -5
arm-none-eabi-size build/debug/$P.out
DIST="${DIST:-$HOME/px30-build/zigbee/dist}"; mkdir -p "$DIST"
NAME="ot-rcp-tyzs13-${GSDK_LABEL}-${BAUD}-hwflow-btl"
if [ -n "${MKGBL:-}" ]; then $MKGBL build/debug/$P.out "$DIST/$NAME.gbl"
else commander-cli gbl create "$DIST/$NAME.gbl" --app build/debug/$P.s37; fi
cp build/debug/$P.s37 "$DIST/$NAME.s37"
mkdir -p "${PX30_OUT:-$HOME/px30-out}/zigbee" && cp "$DIST/$NAME".* "${PX30_OUT:-$HOME/px30-out}/zigbee"/
sha256sum "$DIST/$NAME".*
# Check: without these symbols the RCP cannot return to the bootloader (see patch-ot-rcp.py)
arm-none-eabi-nm build/debug/$P.out | grep -E " T (otPlatResetToBootloader|bootloader_rebootAndInstall)$" | wc -l | grep -qx 2 \
  && echo "return to bootloader: OK" || { echo "ERROR: no return to the bootloader in the image"; exit 1; }
