#!/usr/bin/env bash
# =====================================================================
# Online update of a running PX30 panel over the network (Git Bash):
# writes a release into the INACTIVE rootfs slot, refreshes boot, recovery
# and factory, and switches the slot with fallback to the old one. The
# initramfs returns to the old slot by itself if the new one does not reach
# "boot ok" within 3 boots (px30-boot-ok.timer).
#
#   bash tools/px30-ota.sh <release-dir> <host> [--reboot]
#
# Only uploads (pc -> panel). Needs admin with sudo -n on the panel.
# =====================================================================
set -euo pipefail
D="${1:?release dir}"; H="${2:?panel host or IP}"; RB="${3:-}"
# known_hosts file and host-key alias: the panel keeps the same host key on every slot
KH="${PX30_KNOWN_HOSTS:-$HOME/.ssh/known_hosts_px30}"; ALIAS="${PX30_HOSTKEY_ALIAS:-$H}"
SSH=(ssh -o UserKnownHostsFile="$KH" -o HostKeyAlias="$ALIAS"
     -o ServerAliveInterval=10 -o ServerAliveCountMax=6 "admin@$H")
L='. /usr/local/lib/px30/px30-state.sh'
rs() { "${SSH[@]}" "sudo -n sh -c '$L; $1'"; }

(cd "$D" && sha256sum -c --quiet SHA256SUMS) || { echo "FOUT: release-bestanden kloppen niet"; exit 1; }
echo "release $(cat "$D/VERSION") -> $H"

read -r CUR NEW < <(rs 'state_load; r=$(findmnt -no SOURCE /); [ "$r" = "$(px30_part rootfs_$slot)" ] || { echo "root $r is niet rootfs_$slot" >&2; exit 1; }; [ $slot = a ] && echo "a b" || echo "b a"')
echo "actief: rootfs_$CUR, nieuw: rootfs_$NEW"

t0=$SECONDS
# (the rootfs has no e2fsprogs: the initramfs grows the image to the slot at boot)
rs "r=\$(px30_part rootfs_$NEW); grep -q \"^\$r \" /proc/mounts && exit 1; zstd -dc | dd of=\$r bs=1M 2>/dev/null && sync && blkid \$r | grep -q px30-root && echo \"rootfs_$NEW geschreven\"" < "$D/px30-rootfs.img.zst"
echo "  $((SECONDS - t0)) s"

rs 'dd of=$(px30_part boot) bs=1M 2>/dev/null && sync && echo "boot geschreven"' < "$D/px30-boot.img"
rs 'dd of=$(px30_part recovery) bs=1M 2>/dev/null && sync && echo "recovery geschreven"' < "$D/px30-rescue.img"

rs 'mkdir -p /mnt/factory; grep -q " /mnt/factory " /proc/mounts || mount $(px30_part factory) /mnt/factory'
for f in px30-rootfs.img.zst:rootfs.img.zst px30-boot.img:boot.img px30-rescue.img:recovery.img VERSION:VERSION SHA256SUMS:SHA256SUMS; do
  rs "cat > /mnt/factory/${f#*:}.tmp && mv /mnt/factory/${f#*:}.tmp /mnt/factory/${f#*:}" < "$D/${f%%:*}"
done
rs 'cd /mnt/factory && sed -e "s/px30-rootfs.img.zst/rootfs.img.zst/;s/px30-boot.img/boot.img/;s/px30-rescue.img/recovery.img/" SHA256SUMS | sha256sum -c
  for p in boot recovery; do n=$(wc -c < /mnt/factory/$p.img); head -c $n $(px30_part $p) | cmp -s - /mnt/factory/$p.img && echo "$p: OK" || { echo "$p: VERSCHIL"; exit 1; }; done
  cd /; umount /mnt/factory'

rs "state_load; slot=$NEW; fallback=$CUR; tries=0; action=; state_save; state_show"
if [ "$RB" = --reboot ]; then
  rs 'systemctl reboot' || true
  echo "herstart; na ~40 s: px30-ctl status (tries=0, fallback leeg = ok)"
else
  echo "klaar; nieuwe slot actief bij de volgende herstart"
fi
