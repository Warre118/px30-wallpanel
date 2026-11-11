#!/usr/bin/env bash
# =====================================================================
# Development tool (pre-A/B layout): write a rootfs image to the panel's
# userdata partition over the USB cable (NCM, ssh admin@10.55.0.1) or Wi-Fi.
# Git Bash (Windows) or WSL.
#
#   bash tools/flash-rootfs.sh <px30-rootfs.ext4> [host]
#
# - The panel must run from ANOTHER root (e.g. stback), because userdata is
#   overwritten completely. The script refuses if userdata is mounted.
# - Stream: gzip -1 (free space = zeros, compresses well) -> ssh -> dd.
# - Check: sha256 of the partition (after drop_caches) == sha256 of the image.
# - Then, ON the panel itself (secrets never leave the device): copy the
#   Wi-Fi profiles, ssh host keys, machine-id and Thread network data from
#   the running root to the new root.
# =====================================================================
set -euo pipefail
IMG="${1:?usage: $0 <px30-rootfs.ext4> [host]}"
HOST="${2:-10.55.0.1}"
# works in WSL (/mnt/c) and in Git Bash on Windows (/c)
C=/c; [ -d /mnt/c/Users ] && C=/mnt/c
KH="${PX30_KNOWN_HOSTS:-$HOME/.ssh/known_hosts_px30}"
SSH=(ssh -o UserKnownHostsFile="$KH" -o HostKeyAlias="${PX30_HOSTKEY_ALIAS:-$HOST}" -c aes128-gcm@openssh.com "admin@$HOST")
PART=/dev/disk/by-partlabel/userdata

if [[ "$IMG" == *.gz ]]; then
  # already compressed; "<sha256> <bytes>" of the raw image is in $IMG.sha256
  read -r want size < "$IMG.sha256"
  SRC=(cat "$IMG")
else
  size=$(stat -c%s "$IMG")
  want=$(sha256sum "$IMG" | cut -d' ' -f1)
  SRC=(gzip -1 -c "$IMG")
fi
psize=$("${SSH[@]}" "sudo blockdev --getsize64 $PART")
echo "image $size bytes, partition $psize bytes, sha256 ${want:0:16}"
[ "$size" -le "$psize" ] || { echo "ERROR: image larger than the partition"; exit 1; }
"${SSH[@]}" "! findmnt -S $PART >/dev/null && ! findmnt -S \$(readlink -f $PART) >/dev/null" \
  || { echo "ERROR: userdata is mounted (is the panel running from it?)"; exit 1; }

t0=$SECONDS
"${SRC[@]}" | "${SSH[@]}" "gunzip -c | sudo dd of=$PART bs=4M iflag=fullblock conv=fsync status=none"
echo "written in $((SECONDS - t0)) s; checking ..."
got=$("${SSH[@]}" "sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null; sudo head -c $size $PART | sha256sum | cut -d' ' -f1")
[ "$got" = "$want" ] || { echo "ERROR: sha256 partition ${got:0:16} != image ${want:0:16}"; exit 1; }
echo "sha256 OK"

# carry over the identity of the device (on the panel; nothing passes the PC)
"${SSH[@]}" 'sudo sh -euc "
  M=\$(mktemp -d); mount '"$PART"' \$M
  [ -d /var/lib/iwd ] && { mkdir -p \$M/var/lib/iwd; cp -a /var/lib/iwd/. \$M/var/lib/iwd/; chmod 700 \$M/var/lib/iwd; }
  cp -a /etc/ssh/ssh_host_* \$M/etc/ssh/ 2>/dev/null || true
  cp /etc/machine-id \$M/etc/machine-id
  [ -d /var/lib/thread ] && { mkdir -p \$M/var/lib/thread; cp -a /var/lib/thread/. \$M/var/lib/thread/; }
  echo \"  iwd-profielen: \$(ls \$M/var/lib/iwd | wc -l)\"
  umount \$M; rmdir \$M
"'
echo "done: identity (Wi-Fi, ssh host keys, machine-id, Thread) carried over"
