#!/usr/bin/env bash
# =====================================================================
# First install WITHOUT a USB cable: from the stock Android over adb (Wi-Fi).
#
#   bash tools/px30-android-autoinstall.sh <release-dir> <adb-serial> [hostname] [--reboot]
#   e.g. bash tools/px30-android-autoinstall.sh ~/px30-out/release-<version> <panel-ip>:5555 kitchen --reboot
#
# 1. writes the release as a tar "px30-autoinstall/..." raw into Android's
#    stback partition (a Smatek backup partition Android does not mount;
#    MAKE THE eMMC BACKUP FIRST, it holds the only copy of stback),
# 2. writes px30-rescue.img to Android's recovery partition,
# 3. with --reboot: adb reboot recovery. The rescue mode finds the payload,
#    checks every sha256, waits 30 s (ssh ... px30-rescue hold over USB stops
#    it), repartitions and installs to rootfs_b, then boots Debian.
# Without --reboot nothing changes for Android until the next "reboot recovery".
# The release must have Wi-Fi baked in (WIFI_SSID/WIFI_PSK) or the panel
# opens its setup hotspot. uboot/trust/idblock are never touched.
# =====================================================================
set -euo pipefail
D="${1:?usage: $0 <release-dir> <adb-serial> [hostname] [--reboot]}"
SERIAL="${2:?usage: $0 <release-dir> <adb-serial> [hostname] [--reboot]}"
HOST="" REBOOT=""
for a in "${@:3}"; do case "$a" in --reboot) REBOOT=1 ;; *) HOST="$a" ;; esac; done
[ -z "$HOST" ] || [[ "$HOST" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] || { echo "ERROR: invalid hostname $HOST"; exit 1; }
ADB=(adb -s "$SERIAL")
sh_() { "${ADB[@]}" shell "$@" | tr -d '\r'; }

(cd "$D" && sha256sum -c --quiet SHA256SUMS) || { echo "ERROR: release files do not match SHA256SUMS"; exit 1; }
echo "release $(cat "$D/VERSION") -> $SERIAL${HOST:+ (hostname $HOST)}"

# --- panel: stock Android, root, old layout
case "$SERIAL" in *:*) adb connect "$SERIAL" >/dev/null ;; esac
"${ADB[@]}" root >/dev/null; sleep 3
case "$SERIAL" in *:*) adb connect "$SERIAL" >/dev/null ;; esac
"${ADB[@]}" wait-for-device
[ "$(sh_ id -u)" = 0 ] || { echo "ERROR: no adb root"; exit 1; }
[ "$(sh_ getprop ro.product.device)" = px30_evb ] || { echo "ERROR: not a PX30 panel on stock Android"; exit 1; }
SB=/dev/block/by-name/stback
sh_ "ls $SB >/dev/null && ! grep -q \"\$(readlink -f $SB) \" /proc/mounts" || { echo "ERROR: stback missing or mounted"; exit 1; }
sb_bytes=$(( $(sh_ "cat /sys/class/block/\$(basename \$(readlink -f $SB))/size") * 512 ))

# --- payload
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
P="$T/px30-autoinstall"; mkdir "$P"
for f in px30-boot.img px30-rescue.img px30-rootfs.img.zst VERSION SHA256SUMS; do ln -s "$(readlink -f "$D/$f")" "$P/$f"; done
[ -z "$HOST" ] || echo "$HOST" > "$P/hostname"
(cd "$P" && sha256sum *) > "$T/PAYLOAD.sha256" && mv "$T/PAYLOAD.sha256" "$P/"
tar -C "$T" --format=ustar -h -cf "$T/payload.tar" px30-autoinstall
size=$(stat -c %s "$T/payload.tar")
[ "$size" -lt "$sb_bytes" ] || { echo "ERROR: payload ($size) larger than stback ($sb_bytes)"; exit 1; }
want=$(sha256sum < "$T/payload.tar" | cut -d' ' -f1)
echo "payload: $((size / 1048576)) MiB (stback $((sb_bytes / 1048576)) MiB)"

# --- write payload + rescue image (PC -> panel only), then read back
echo "uploading ..."
"${ADB[@]}" push "$T/payload.tar" /data/local/tmp/px30-payload.tar
"${ADB[@]}" push "$D/px30-rescue.img" /data/local/tmp/px30-rescue.img
sh_ "dd if=/data/local/tmp/px30-payload.tar of=$SB bs=1048576 2>/dev/null && sync"
got=$(sh_ "head -c $size $SB | sha256sum" | cut -d' ' -f1)
[ "$got" = "$want" ] || { echo "ERROR: stback read-back differs"; exit 1; }
echo "stback: payload written and verified"
r=$(sha256sum < "$D/px30-rescue.img" | cut -d' ' -f1); n=$(stat -c %s "$D/px30-rescue.img")
sh_ "dd if=/data/local/tmp/px30-rescue.img of=/dev/block/by-name/recovery bs=1048576 2>/dev/null && sync"
[ "$(sh_ "head -c $n /dev/block/by-name/recovery | sha256sum" | cut -d' ' -f1)" = "$r" ] || { echo "ERROR: recovery read-back differs"; exit 1; }
echo "recovery: rescue image written and verified"
sh_ "rm -f /data/local/tmp/px30-payload.tar /data/local/tmp/px30-rescue.img"

if [ -n "$REBOOT" ]; then
  "${ADB[@]}" reboot recovery || true
  echo "rebooting into the rescue mode: it installs by itself (screen shows the progress)."
  echo "Then: ssh admin@${HOST:-px30-panel}.local  (about 3-5 min)"
else
  echo "ready. Start with: adb -s $SERIAL reboot recovery"
fi
