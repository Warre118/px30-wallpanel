#!/bin/sh
# Compiles the panel DTS against a (sparse) mainline Linux checkout.
# Usage (in WSL/Linux): sh dts/build-dtb.sh <path-to-linux> [dtc]
set -e
L="$1"; DTC="${2:-dtc}"; D="$(dirname "$0")"
cpp -nostdinc -undef -x assembler-with-cpp -D__DTS__ \
    -I "$L/include" -I "$L/arch/arm64/boot/dts/rockchip" \
    "$D/px30-smatek-wallpanel.dts" -o /tmp/px30-wp.pre.dts
"$DTC" -I dts -O dtb -o "$D/px30-smatek-wallpanel.dtb" /tmp/px30-wp.pre.dts
ls -la "$D/px30-smatek-wallpanel.dtb"
