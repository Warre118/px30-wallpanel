#!/usr/bin/env bash
# Toolchain voor de OpenThread-RCP-build (EFR32MG13, serie 1) in WSL, zonder root:
# Gecko SDK 4.4.x (laatste met serie-1-ondersteuning), slc-cli, Arm GCC 12.2,
# Simplicity Commander en een portable Java 17 (Temurin) voor slc.
set -euo pipefail
T="${T:-$HOME/px30-build/silabs}"
mkdir -p "$T" && cd "$T"
GSDK_VER="${GSDK_VER:-v4.4.6}"
get() {
  if [ ! -s "$2" ]; then wget -q --tries=3 -O "$2.part" "$1"; mv "$2.part" "$2"; fi
  ls -la "$2"
}
unz() { python3 -c 'import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$1" "$2"; }
get "https://github.com/SiliconLabs/gecko_sdk/releases/download/$GSDK_VER/gecko-sdk.zip" gecko-sdk.zip


get "https://developer.arm.com/-/media/Files/downloads/gnu/12.2.rel1/binrel/arm-gnu-toolchain-12.2.rel1-x86_64-arm-none-eabi.tar.xz" gcc.tar.xz
get "https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jre/hotspot/normal/eclipse" jre17.tar.gz
[ -d gecko_sdk ] || { unz gecko-sdk.zip gecko_sdk; }
[ -d slc_cli ] || { unz slc_cli_linux.zip .; chmod +x slc_cli/slc slc_cli/bin/slc-cli/slc-cli 2>/dev/null || true; }
[ -d commander/commander-cli ] || { mkdir -p commander; unz SimplicityCommander-Linux.zip commander; tar xjf commander/SimplicityCommander-Linux/Commander-cli_linux_x86_64_*.tar.bz -C commander; }
[ -d arm-gnu-toolchain-12.2.rel1-x86_64-arm-none-eabi ] || tar xJf gcc.tar.xz
[ -d jre17 ] || { mkdir jre17 && tar xzf jre17.tar.gz -C jre17 --strip-components=1; }
du -sh "$T"/* | sort -h | tail
