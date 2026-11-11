#!/bin/sh
# Live test: set the Inno DSI D-PHY lane timings to the Android values (logs/dphy-android.txt).
# Mainline computes T_LPX=0 at 240 Mbps; Android uses 2.
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
