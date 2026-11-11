#!/usr/bin/env bash
# =====================================================================
# Rootfs-image naar de userdata-partitie van het panel schrijven, over de
# USB-kabel (NCM, ssh admin@10.55.0.1) of Wi-Fi. Git Bash (Windows) of WSL.
#
#   bash tools/flash-rootfs.sh <px30-rootfs.ext4> [host]
#
# - Het panel moet draaien vanaf een ANDERE root (bv. stback), want userdata
#   wordt volledig overschreven. Het script weigert als userdata gemount is.
# - Stroom: gzip -1 (lege ruimte = nullen, comprimeert sterk) -> ssh -> dd.
# - Controle: sha256 van de partitie (na drop_caches) == sha256 van het image.
# - Daarna, OP het panel zelf (geheimen verlaten het toestel niet):
#   Wi-Fi-profielen, ssh-hostkeys, machine-id en Thread-netwerkdata van de
#   draaiende root naar de nieuwe root kopiëren.
# =====================================================================
set -euo pipefail
IMG="${1:?gebruik: $0 <px30-rootfs.ext4> [host]}"
HOST="${2:-10.55.0.1}"
# werkt in WSL (/mnt/c) en in Git Bash op Windows (/c); die laatste heeft de ssh-sleutel
C=/c; [ -d /mnt/c/Users ] && C=/mnt/c
KH="${PX30_KNOWN_HOSTS:-$HOME/.ssh/known_hosts_px30}"
SSH=(ssh -o UserKnownHostsFile="$KH" -o HostKeyAlias="${PX30_HOSTKEY_ALIAS:-$HOST}" -c aes128-gcm@openssh.com "admin@$HOST")
PART=/dev/disk/by-partlabel/userdata

if [[ "$IMG" == *.gz ]]; then
  # al gecomprimeerd; "<sha256> <bytes>" van het ruwe image staat in $IMG.sha256
  read -r want size < "$IMG.sha256"
  SRC=(cat "$IMG")
else
  size=$(stat -c%s "$IMG")
  want=$(sha256sum "$IMG" | cut -d' ' -f1)
  SRC=(gzip -1 -c "$IMG")
fi
psize=$("${SSH[@]}" "sudo blockdev --getsize64 $PART")
echo "image $size bytes, partitie $psize bytes, sha256 ${want:0:16}"
[ "$size" -le "$psize" ] || { echo "FOUT: image groter dan partitie"; exit 1; }
"${SSH[@]}" "! findmnt -S $PART >/dev/null && ! findmnt -S \$(readlink -f $PART) >/dev/null" \
  || { echo "FOUT: userdata is gemount (panel draait ervan?)"; exit 1; }

t0=$SECONDS
"${SRC[@]}" | "${SSH[@]}" "gunzip -c | sudo dd of=$PART bs=4M iflag=fullblock conv=fsync status=none"
echo "geschreven in $((SECONDS - t0)) s; controleren ..."
got=$("${SSH[@]}" "sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null; sudo head -c $size $PART | sha256sum | cut -d' ' -f1")
[ "$got" = "$want" ] || { echo "FOUT: sha256 partitie ${got:0:16} != image ${want:0:16}"; exit 1; }
echo "sha256 OK"

# identiteit van het toestel meenemen (op het panel; niets passeert de PC)
"${SSH[@]}" 'sudo sh -euc "
  M=\$(mktemp -d); mount '"$PART"' \$M
  [ -d /var/lib/iwd ] && { mkdir -p \$M/var/lib/iwd; cp -a /var/lib/iwd/. \$M/var/lib/iwd/; chmod 700 \$M/var/lib/iwd; }
  cp -a /etc/ssh/ssh_host_* \$M/etc/ssh/ 2>/dev/null || true
  cp /etc/machine-id \$M/etc/machine-id
  [ -d /var/lib/thread ] && { mkdir -p \$M/var/lib/thread; cp -a /var/lib/thread/. \$M/var/lib/thread/; }
  echo \"  iwd-profielen: \$(ls \$M/var/lib/iwd | wc -l)\"
  umount \$M; rmdir \$M
"'
echo "klaar: identiteit (Wi-Fi, ssh-hostkeys, machine-id, Thread) overgenomen"
