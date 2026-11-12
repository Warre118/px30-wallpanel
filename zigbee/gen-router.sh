#!/usr/bin/env bash
# Generate (step 1) the stand-alone Zigbee router for the TYZS13
# (EFR32MG13P732F512GM48), Gecko SDK 4.x: the SDK's ZigbeeMinimal sample made
# into a router that joins by itself, with the "bootloader reboot" CLI command
# (router/) so it can be reflashed over the UART. Same variables as gen-ncp.sh.
set -euo pipefail
T="${T:-$HOME/px30-build/silabs}"
GCC_DIR="${GCC_DIR:-$T/arm-gnu-toolchain-12.2.rel1-x86_64-arm-none-eabi}"
export PATH="$T/slc_cli:$GCC_DIR/bin:$T/commander/commander-cli:$PATH"
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-21-openjdk-amd64}"
export PATH="$JAVA_HOME/bin:$PATH"
SDK="${SDK:-$T/gecko_sdk}"
OUT="${OUT:-$HOME/px30-build/zigbee/router}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$OUT.src"
rm -rf "$SRC" "$OUT"; mkdir -p "$SRC" "$OUT"
cp -r "$SDK/protocol/zigbee/app/framework/scenarios/z3/ZigbeeMinimal/." "$SRC/"
mv "$SRC/ZigbeeMinimal.slcp" "$SRC/tyzs13_router.slcp"
cp "$HERE/router/app.c" "$HERE/router/router_bootloader_cli.c" "$SRC/"
python3 "$HERE/router/patch-slcp.py" "$SRC/tyzs13_router.slcp"
# SLC_TOOL_PATH may list several adapter packs (python:zap); a router needs ZAP
# for its ZCL configuration (zap-config.h)
TP=(); IFS=: read -ra _tp <<< "${SLC_TOOL_PATH:-}"
for p in "${_tp[@]}"; do [ -n "$p" ] && TP+=(--tool-path="$p"); done
cd "$T"
slc configuration --sdk "$SDK" --gcc-toolchain "$GCC_DIR" >/dev/null
slc signature trust --sdk "$SDK" >/dev/null || true
slc generate "$SRC/tyzs13_router.slcp" \
  -np -d "$OUT" -name router-tyzs13 -o makefile \
  --with "EFR32MG13P732F512GM48" "${TP[@]}" 2>&1 | tail -20
[ -f "$OUT/autogen/zap-config.h" ] || { echo "ERROR: ZAP did not generate autogen/zap-config.h (is the ZAP adapter pack in SLC_TOOL_PATH?)"; exit 1; }
ls "$OUT" "$OUT/config"
