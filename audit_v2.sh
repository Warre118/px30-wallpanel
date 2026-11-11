#!/usr/bin/env bash
# =====================================================================
# PX30 wallpanel – read-only hardware-audit v2
#
# Draait vanuit Termux (of Git Bash / Linux) met een werkende
# 'adb connect <ip>:5555' naar het panel.
#
# DIT SCRIPT SCHRIJFT NIETS NAAR HET PANEL.
#  - Enige statuswijziging: 'adb root' (herstart adbd als root; enkel
#    mogelijk omdat de build 'userdebug' is). Verdwijnt na reboot.
#  - Alle 'dd'-aanroepen gebruiken uitsluitend if=<panel>, nooit of=<panel>.
#
# Gebruik:  bash audit_v2.sh [serial]     bv. bash audit_v2.sh 192.168.1.50:5555
# Output:   map px30-audit-<datum>/ met audit.log + binaire dumps
# =====================================================================
set -u

SERIAL="${1:-}"
ADB="adb"
[ -n "$SERIAL" ] && ADB="adb -s $SERIAL"

OUT="px30-audit-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"
LOG="$OUT/audit.log"

sec() { printf '\n===== %s =====\n' "$1" | tee -a "$LOG"; }
run() { # run "<shell-commando op panel>"
  printf -- '--- $ %s\n' "$1" >>"$LOG"
  $ADB shell "$1" >>"$LOG" 2>&1
}

sec "ADB / ROOT"
$ADB devices -l | tee -a "$LOG"
run 'id; getprop ro.build.type; getprop ro.debuggable; getprop ro.adb.secure'
echo "Probeer adb root (alleen mogelijk op userdebug/eng builds)..." | tee -a "$LOG"
$ADB root 2>&1 | tee -a "$LOG"
sleep 3
# na 'adb root' kan een netwerk-ADB-verbinding wegvallen -> opnieuw verbinden
if [ -n "$SERIAL" ]; then adb connect "$SERIAL" >/dev/null 2>&1; sleep 2; fi
$ADB wait-for-device
ROOTID="$($ADB shell id -u 2>/dev/null | tr -d '\r')"
echo "uid na adb root: $ROOTID" | tee -a "$LOG"
IS_ROOT=0; [ "$ROOTID" = "0" ] && IS_ROOT=1

sec "KERNEL / BOOT"
run 'cat /proc/version; cat /proc/cmdline'
run 'getprop | grep -iE "ro\.boot|bootloader|secure|verity|flash|rk\." '
run 'ls /proc/config.gz 2>&1'
[ "$IS_ROOT" = 1 ] && $ADB exec-out 'cat /proc/config.gz' > "$OUT/kernel-config.gz" 2>/dev/null

sec "DEVICE TREE"
run 'cat /proc/device-tree/model; echo; cat /proc/device-tree/compatible | tr "\0" " "; echo'
run 'ls -la /sys/firmware/ 2>&1'
# Live DTB (bevat paneel-timings, DSI-init-sequentie, touch-GPIO's, regulators)
$ADB exec-out 'cat /sys/firmware/fdt' > "$OUT/live.dtb" 2>/dev/null
echo "live.dtb: $(wc -c < "$OUT/live.dtb") bytes (0 = niet leesbaar)" | tee -a "$LOG"
# Snelle tekstweergave van de belangrijkste nodes, ook zonder dtc op de PC
run 'for d in /proc/device-tree/display-subsystem /proc/device-tree/dsi@* /proc/device-tree/panel* /proc/device-tree/*/panel* /proc/device-tree/rgb /proc/device-tree/lvds /proc/device-tree/*/rk618* /proc/device-tree/i2c@*/*@5a; do [ -e "$d" ] && { echo "### $d"; ls "$d"; for p in status compatible; do [ -f "$d/$p" ] && { printf "%s=" $p; tr "\0" " " < "$d/$p"; echo; }; done; }; done 2>/dev/null'
run 'find /proc/device-tree -name "panel-init-sequence" -o -name "display-timings" -o -name "rockchip,data-mapping" 2>/dev/null'

sec "DISPLAY / DRM"
run 'ls -la /sys/class/drm'
run 'for f in /sys/class/drm/*/status /sys/class/drm/*/modes /sys/class/drm/*/enabled; do echo "--- $f"; cat "$f" 2>/dev/null; done'
run 'cat /sys/class/graphics/fb0/modes /sys/class/graphics/fb0/virtual_size /sys/class/graphics/fb0/bits_per_pixel 2>&1'
run 'cat /sys/kernel/debug/dri/0/summary 2>&1 | head -60'
run 'ls /sys/class/backlight; for b in /sys/class/backlight/*; do echo "$b: $(cat $b/brightness)/$(cat $b/max_brightness)"; done'

sec "DMESG (filter)"
run 'dmesg 2>&1 | grep -iE "panel|dsi|mipi|lvds|rgb|vop|rk618|lcd|backlight|cst|touch|hyn|i2c|rtl|8723|wlan|sdio|mmc|gmac|stmmac|phy|mali|gpu|usb|otg|boot|dtb|fdt" | head -400'
[ "$IS_ROOT" = 1 ] && $ADB exec-out 'dmesg' > "$OUT/dmesg-full.txt" 2>/dev/null

sec "TOUCHSCREEN"
run 'ls -la /sys/bus/i2c/devices/1-005a/ /sys/bus/i2c/devices/1-005a/driver 2>&1'
run 'cat /sys/bus/i2c/devices/1-005a/modalias /sys/bus/i2c/devices/1-005a/name 2>&1'
run 'cat /proc/bus/input/devices'
run 'getevent -p 2>&1 | head -80'

sec "I2C-BUSSEN"
run 'for d in /sys/bus/i2c/devices/*; do echo "$(basename $d): $(cat $d/name 2>/dev/null) -> $(basename $(readlink $d/driver) 2>/dev/null)"; done'

sec "NETWERK"
run 'ip link; ip addr'
run 'for p in /sys/bus/mdio_bus/devices/*; do echo "$p phy_id=$(cat $p/phy_id 2>/dev/null)"; done; cat /sys/class/net/eth0/phydev/phy_id 2>&1'
run 'ls /sys/bus/sdio/devices/ 2>&1; for d in /sys/bus/sdio/devices/*; do echo "$d vendor=$(cat $d/vendor) device=$(cat $d/device)"; done'
run 'ls -la /vendor/etc/firmware/ 2>&1 | grep -i 8723'

sec "OPSLAG / BOOTMEDIA (SD-slot aanwezig?)"
run 'ls -la /sys/class/mmc_host/'
run 'for h in /sys/class/mmc_host/*; do echo "### $h"; ls $h; for c in $h/mmc*; do [ -d "$c" ] && echo "card $c type=$(cat $c/type 2>/dev/null) name=$(cat $c/name 2>/dev/null) cid=$(cat $c/cid 2>/dev/null)"; done; done'
run 'cat /proc/partitions'
run 'ls -la /dev/block/ /dev/block/platform/*/*/by-name/ /dev/block/by-name/ 2>/dev/null'
run 'ls -la /dev/block/mmcblk1boot* 2>&1; cat /sys/block/mmcblk1/size'
# Rockchip 'parameter'-blok (partitie-layout in tekst), read-only, eerste 4 MiB
if [ "$IS_ROOT" = 1 ]; then
  $ADB exec-out 'dd if=/dev/block/mmcblk1 bs=512 skip=0 count=8192 2>/dev/null' > "$OUT/emmc-first-4MiB.bin"
  run 'sgdisk --print /dev/block/mmcblk1 2>&1 || echo "sgdisk niet aanwezig"'
  # resource-partitie bevat de DTB('s) en boot-logo's (klein, read-only)
  $ADB exec-out 'dd if=/dev/block/mmcblk1p4 bs=1048576 2>/dev/null' > "$OUT/resource.img"
fi

sec "GPIO / REGULATORS / CLOCKS (debugfs, alleen root)"
run 'mount | grep debugfs || echo geen-debugfs'
run 'cat /sys/kernel/debug/gpio 2>&1 | head -150'
run 'cat /sys/kernel/debug/regulator/regulator_summary 2>&1 | head -120'
run 'cat /sys/kernel/debug/pinctrl/*/pinmux-pins 2>&1 | grep -v "UNCLAIMED" | head -150'

sec "MODULES / GPU"
run 'lsmod 2>&1; ls /vendor/lib/modules /system/lib/modules 2>&1'
run 'ls -la /dev/mali* /dev/dri 2>&1; cat /sys/class/misc/mali0/device/gpuinfo 2>&1'

sec "USB (OTG / host – nodig voor Loader/Maskrom-herstel)"
run 'ls /sys/bus/platform/drivers/ | grep -iE "dwc|usb|otg"; cat /sys/class/udc/*/state 2>&1'

sec "KLAAR"
echo "Root-sessie: $IS_ROOT" | tee -a "$LOG"
ls -la "$OUT" | tee -a "$LOG"
echo
echo "Stuur de map $OUT (vooral audit.log, live.dtb, resource.img) door voor analyse."
echo "adbd terug naar normaal: 'adb unroot' of herstart het panel."
