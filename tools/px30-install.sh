#!/usr/bin/env bash
# =====================================================================
# Installs a PX30 release on the panel over the USB cable (Git Bash).
# The panel must be in RESCUE MODE (px30-ctl rescue, or a release-less
# panel: power-cycle 3x during the boot animation).
#
#   bash tools/px30-install.sh <release-dir> [--repartition]
#
# --repartition  one-time: old Android GPT -> PX30 layout (keeps
#                uboot/trust/misc/resource in place, copies the Smatek
#                partitions, takes Wi-Fi/ssh keys/Thread/machine-id along
#                into the data partition).
# Then: boot <- px30-boot.img, recovery <- px30-rescue.img,
# rootfs_a <- px30-rootfs.img.zst, factory <- all release files, reboot.
# Only PC -> panel transfers (the panel may brown out on long downloads
# while it is powered from the PC's USB port).
# =====================================================================
set -euo pipefail
D="${1:?usage: $0 <release-dir> [--repartition]}"
REPART="${2:-}"
KH="${PX30_KNOWN_HOSTS_RESCUE:-$HOME/.ssh/known_hosts_px30_rescue}"
RS=(ssh -o UserKnownHostsFile="$KH" -o StrictHostKeyChecking=accept-new -o HostKeyAlias=px30-rescue
    -o ServerAliveInterval=10 -o ServerAliveCountMax=6 root@10.55.0.1)
rs() { "${RS[@]}" "$@"; }

(cd "$D" && sha256sum -c --quiet SHA256SUMS) || { echo "ERROR: release files do not match SHA256SUMS"; exit 1; }
echo "release $(cat "$D/VERSION")"
rs 'px30-rescue hold; px30-rescue status' || { echo "ERROR: no rescue mode on 10.55.0.1"; exit 1; }

if [ "$REPART" = --repartition ]; then
  rs 'px30-rescue repartition --yes'
fi
rs 'px30-rescue status' | grep -q rootfs_a || { echo "ERROR: no PX30 layout (use --repartition)"; exit 1; }

echo "boot + recovery ..."
rs 'px30-rescue write-part boot' < "$D/px30-boot.img"
rs 'px30-rescue write-part recovery' < "$D/px30-rescue.img"
echo "rootfs_a ($(du -h "$D/px30-rootfs.img.zst" | cut -f1) compressed) ..."
t0=$SECONDS
rs 'px30-rescue write-rootfs a' < "$D/px30-rootfs.img.zst"
echo "  $((SECONDS - t0)) s"
echo "factory ..."
rs 'px30-rescue put-factory rootfs.img.zst' < "$D/px30-rootfs.img.zst"
rs 'px30-rescue put-factory boot.img' < "$D/px30-boot.img"
rs 'px30-rescue put-factory recovery.img' < "$D/px30-rescue.img"
rs 'px30-rescue put-factory VERSION' < "$D/VERSION"
rs 'px30-rescue put-factory SHA256SUMS' < "$D/SHA256SUMS"

# verify on the panel what was written
rs '. /lib/px30-state.sh
  mkdir -p /mnt/f; mount -o ro $(px30_part factory) /mnt/f
  cd /mnt/f && sed -e "s/px30-rootfs.img.zst/rootfs.img.zst/;s/px30-boot.img/boot.img/;s/px30-rescue.img/recovery.img/" SHA256SUMS | sha256sum -c
  for p in boot recovery; do n=$(wc -c < /mnt/f/$p.img); head -c $n $(px30_part $p) | cmp -s - /mnt/f/$p.img && echo "$p: OK" || echo "$p: DIFFERENT"; done
  cd /; umount /mnt/f
  px30-rescue status'
echo "done; reboot with: ssh ... root@10.55.0.1 px30-rescue reboot"
