#!/usr/bin/env bash
# Genereer (stap 1) het OpenThread-RCP-project voor de TYZS13 (EFR32MG13P732F512GM48).
set -euo pipefail
T="$HOME/px30-build/silabs"
export PATH="$T/slc_cli:$T/arm-gnu-toolchain-12.2.rel1-x86_64-arm-none-eabi/bin:$T/commander/commander-cli:$PATH"
# slc-cli 6.x vraagt Java 21 (class file 65)
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-21-openjdk-amd64}"
export PATH="$JAVA_HOME/bin:$PATH"
SDK="$T/gecko_sdk"
OUT="$HOME/px30-build/zigbee/ot-rcp"
cd "$T"
slc configuration --sdk "$SDK" --gcc-toolchain "$T/arm-gnu-toolchain-12.2.rel1-x86_64-arm-none-eabi" >/dev/null
slc signature trust --sdk "$SDK" >/dev/null || true
rm -rf "$OUT"; mkdir -p "$OUT"
slc generate "$SDK/protocol/openthread/sample-apps/ot-ncp/ot-rcp.slcp" \
  -np -d "$OUT" -name ot-rcp-tyzs13 -o makefile \
  --with EFR32MG13P732F512GM48 2>&1 | tail -20
ls "$OUT" "$OUT/config" | head -60
