#!/bin/sh
# Live-test: zet de inno-DSI-DPHY-lanetimings op de Android-waarden (logs/dphy-android.txt).
# Mainline berekent T_LPX=0 bij 240 Mbps; Android gebruikt 2.
W=/usr/local/sbin/regwrite
for base in 100 180 200 280 300; do
	$W $(printf "ff2e%04x" $((0x$base + 0x14))) c2      # T_LPX
	$W $(printf "ff2e%04x" $((0x$base + 0x44))) 02
	if [ $base = 100 ]; then
		$W $(printf "ff2e%04x" $((0x$base + 0x38))) 02  # T_TA_GO klok-lane
	else
		$W $(printf "ff2e%04x" $((0x$base + 0x38))) 06  # T_TA_GO data-lanes
		$W $(printf "ff2e%04x" $((0x$base + 0x28))) 0f  # T_CLK_POST data-lanes
	fi
done
