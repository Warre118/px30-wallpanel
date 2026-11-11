#!/usr/bin/env bash
# Generate (step 1) the EmberZNet NCP UART HW project (RTS/CTS) for the TYZS13
# (EFR32MG13P732F512GM48), Gecko SDK 4.4.6 / EmberZNet 7.4.x.
# Extra component: bootloader_interface -> EZSP launchStandaloneBootloader werkt
# (needed to go back to Thread with universal-silabs-flasher).
set -euo pipefail
T="$HOME/px30-build/silabs"
export PATH="$T/slc_cli:$T/arm-gnu-toolchain-12.2.rel1-x86_64-arm-none-eabi/bin:$T/commander/commander-cli:$PATH"
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-21-openjdk-amd64}"
export PATH="$JAVA_HOME/bin:$PATH"
SDK="$T/gecko_sdk"
OUT="$HOME/px30-build/zigbee/ncp-uart-hw"
cd "$T"
slc configuration --sdk "$SDK" --gcc-toolchain "$T/arm-gnu-toolchain-12.2.rel1-x86_64-arm-none-eabi" >/dev/null
slc signature trust --sdk "$SDK" >/dev/null || true
rm -rf "$OUT"; mkdir -p "$OUT"
slc generate "$SDK/protocol/zigbee/app/ncp/sample-app/ncp-uart-hw/ncp-uart-hw.slcp" \
  -np -d "$OUT" -name ncp-uart-hw-tyzs13 -o makefile \
  --with "EFR32MG13P732F512GM48,bootloader_interface" 2>&1 | tail -20
ls "$OUT" "$OUT/config"
