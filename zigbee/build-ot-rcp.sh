#!/usr/bin/env bash
# Step 2: set the pinout, build and make the GBL for the TYZS13 RCP.
set -euo pipefail
T="$HOME/px30-build/silabs"
export PATH="$T/arm-gnu-toolchain-12.2.rel1-x86_64-arm-none-eabi/bin:$T/commander/commander-cli:$PATH"
OUT="$HOME/px30-build/zigbee/ot-rcp"
HERE="$(cd "$(dirname "$0")" && pwd)"
BAUD="${BAUD:-115200}"
python3 "$HERE/patch-ot-rcp.py" "$OUT" "$BAUD"
cd "$OUT"
make -f ot-rcp.Makefile clean >/dev/null
make -f ot-rcp.Makefile -j"$(nproc)" ARM_GCC_DIR="$T/arm-gnu-toolchain-12.2.rel1-x86_64-arm-none-eabi" 2>&1 | grep -iE "warning|error|Linking|Done" | tail -15
grep -nE "FLASH|ORIGIN" autogen/linkerfile.ld | head -5
arm-none-eabi-size build/debug/ot-rcp.out
DIST="$HOME/px30-build/zigbee/dist"; mkdir -p "$DIST"
NAME="ot-rcp-tyzs13-gsdk4.4.6-${BAUD}-hwflow-btl"
commander-cli gbl create "$DIST/$NAME.gbl" --app build/debug/ot-rcp.s37
cp build/debug/ot-rcp.s37 "$DIST/$NAME.s37"
mkdir -p "${PX30_OUT:-$HOME/px30-out}/zigbee" && cp "$DIST/$NAME".* "${PX30_OUT:-$HOME/px30-out}/zigbee"/
sha256sum "$DIST/$NAME".*
# Check: without these symbols the RCP cannot return to the bootloader (see patch-ot-rcp.py)
arm-none-eabi-nm build/debug/ot-rcp.out | grep -E " T (otPlatResetToBootloader|bootloader_rebootAndInstall)$" | wc -l | grep -qx 2 \
  && echo "return to bootloader: OK" || { echo "ERROR: no return to the bootloader in the image"; exit 1; }
