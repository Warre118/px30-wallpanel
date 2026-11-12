#!/usr/bin/env bash
# Step 2: pinout, build and GBL for the TYZS13 Zigbee router. Same variables as
# build-ncp.sh. Checks that "bootloader reboot" reaches the Gecko bootloader.
set -euo pipefail
T="${T:-$HOME/px30-build/silabs}"
GCC_DIR="${GCC_DIR:-$T/arm-gnu-toolchain-12.2.rel1-x86_64-arm-none-eabi}"
GSDK_LABEL="${GSDK_LABEL:-gsdk4.4.6}"
export PATH="$GCC_DIR/bin:$T/commander/commander-cli:$PATH"
OUT="${OUT:-$HOME/px30-build/zigbee/router}"
HERE="$(cd "$(dirname "$0")" && pwd)"
BAUD="${BAUD:-115200}"
python3 "$HERE/patch-ncp.py" "$OUT" "$BAUD"      # same USART pinout/clock checks as the NCP
cd "$OUT"
MK="$(ls *.Makefile | head -1)"; P="${MK%.Makefile}"
[ -n "$P" ] || { echo "ERROR: no generated makefile in $OUT"; exit 1; }
make -f "$MK" clean >/dev/null
make -f "$MK" -j"$(nproc)" ARM_GCC_DIR="$GCC_DIR" 2>&1 | grep -iE "warning|error|Linking|Done" | tail -15
ELF=build/debug/$P.out
arm-none-eabi-size "$ELF"

# The way back to the bootloader: the CLI command must be linked in and call
# bootloader_rebootAndInstall (which resets into the Gecko bootloader).
NM=$(arm-none-eabi-nm "$ELF")
grep -qE " T router_bootloader_cli_reboot$" <<<"$NM" || { echo "ERROR: bootloader CLI command missing"; exit 1; }
grep -qE " T bootloader_rebootAndInstall$" <<<"$NM" || { echo "ERROR: bootloader_rebootAndInstall missing"; exit 1; }
DIS="$(arm-none-eabi-objdump -d "$ELF" | awk '/<router_bootloader_cli_reboot>:/,/^$/')"
if grep -q "<bootloader_rebootAndInstall" <<<"$DIS"; then
  echo "return to bootloader (CLI: bootloader reboot): OK"
else
  echo "$DIS"; echo "ERROR: 'bootloader reboot' does not call bootloader_rebootAndInstall"; exit 1
fi
# and the command is registered in the CLI table under "bootloader" -> "reboot"
grep -q "router_bootloader_cli_reboot" autogen/sl_cli_command_table.c \
  || { echo "ERROR: 'bootloader reboot' is not in the CLI command table"; exit 1; }

DIST="${DIST:-$HOME/px30-build/zigbee/dist}"; mkdir -p "$DIST"
NAME="router-tyzs13-${GSDK_LABEL}-${BAUD}-hwflow"
if [ -n "${MKGBL:-}" ]; then $MKGBL build/debug/$P.out "$DIST/$NAME.gbl"
else commander-cli gbl create "$DIST/$NAME.gbl" --app build/debug/$P.s37; fi
cp build/debug/$P.s37 "$DIST/$NAME.s37"
cp build/debug/$P.hex "$DIST/$NAME.hex"
mkdir -p "${PX30_OUT:-$HOME/px30-out}/zigbee"
cd "$DIST" && sha256sum "$NAME".gbl "$NAME".s37 "$NAME".hex | tee "$NAME.sha256"
cp "$NAME".gbl "$NAME".s37 "$NAME".hex "$NAME.sha256" "${PX30_OUT:-$HOME/px30-out}/zigbee"/
ls -la "$NAME".*
