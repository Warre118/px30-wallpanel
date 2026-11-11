#!/bin/sh
# Dump de display-registers (DSI-host, Inno-DSI-PHY, VOPB, VOPL, GRF, GPIO3, PWM, CRU).
# Werkt onder Android (toybox sh, root) en Debian. Gebruik: dispdump.sh <regdump-pad>
R=${1:-./regdump}
for spec in "dsi ff450000 100" "dphy ff2e0000 400" "vopb ff460000 200" "vopl ff470000 200" \
            "grf ff140000 600" "gpio3 ff270000 80" "pwm ff200000 40" "cru ff2b0000 300"; do
  set -- $spec
  echo "## $1"
  $R $2 $3
done
