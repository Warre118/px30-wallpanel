#!/usr/bin/env bash
# =====================================================================
# Builds the PX30 initramfs (boot counter, A/B, /data, rescue mode).
# In WSL, rootless. Output: ~/px30-build/initramfs/initramfs.cpio.gz
#
#   bash initramfs/build-initramfs.sh
#
# Contents: busybox-static, e2fsprogs (e2fsck/mke2fs/resize2fs/dumpe2fs),
# sfdisk, zstd, dropbear (rescue ssh), the USB-NCM gadget modules, and
# the scripts in initramfs/ (init, bin/px30-rescue, lib/px30-state.sh).
# The arm64 binaries come from a small mmdebstrap tree (cached).
# =====================================================================
set -euo pipefail
PROJ="$(cd "$(dirname "$0")/.." && pwd)"
KOUT="${KERNEL_OUT:-$HOME/px30-build/out}"
W="${W:-$HOME/px30-build/initramfs}"
# optional key baked in for root in rescue mode (public images: none; the
# rescue mode also accepts admin's keys from /data, set via the setup page)
PUBKEY="${SSH_PUBKEY:-}"
mkdir -p "$W"
KVER="$(ls "$KOUT/lib/modules" | head -1)"

# 1. arm64 package tree (cached)
if [ ! -f "$W/base.tar" ]; then
  mmdebstrap --mode=unshare --architectures=arm64 --variant=essential \
    --include=busybox-static,dropbear-bin,e2fsprogs,fdisk,zstd \
    --dpkgopt='path-exclude=/usr/share/doc/*' --dpkgopt='path-exclude=/usr/share/man/*' \
    --dpkgopt='path-exclude=/usr/share/locale/*' \
    trixie "$W/base.tar" http://deb.debian.org/debian
fi
B="$W/base"; rm -rf "$B"; mkdir -p "$B"
tar -C "$B" --exclude='./dev/*' -xf "$W/base.tar" 2>/dev/null || true

# 2. staging tree (usrmerge layout)
S="$W/stage"; rm -rf "$S"
mkdir -p "$S"/{usr/bin,usr/sbin,usr/lib/aarch64-linux-gnu,usr/lib/modules,etc/dropbear,root/.ssh,proc,sys,dev,run,tmp,mnt,newroot}
ln -s usr/bin "$S/bin"; ln -s usr/sbin "$S/sbin"; ln -s usr/lib "$S/lib"
cp "$B/usr/bin/busybox" "$S/usr/bin/busybox"
ln -s busybox "$S/usr/bin/sh"

LIBS=()
add_bin() { # path-in-base -> same path in stage, plus libraries
  install -D -m 755 "$B/$1" "$S/$1"
  LIBS+=("$S/$1")
}
for f in usr/sbin/e2fsck usr/sbin/mke2fs usr/sbin/resize2fs usr/sbin/dumpe2fs \
         usr/sbin/sfdisk usr/bin/zstd usr/sbin/dropbear usr/bin/dropbearkey; do
  add_bin "$f"
done
# shared libraries, recursively (NEEDED entries of everything copied)
find_lib() { for d in usr/lib/aarch64-linux-gnu lib/aarch64-linux-gnu usr/lib; do
  [ -e "$B/$d/$1" ] && { echo "$d/$1"; return; }; done; echo "ONTBREEKT: $1" >&2; exit 1; }
declare -A seen
while [ ${#LIBS[@]} -gt 0 ]; do
  f="${LIBS[0]}"; LIBS=("${LIBS[@]:1}")
  for n in $(aarch64-linux-gnu-readelf -d "$f" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p'); do
    [ -n "${seen[$n]:-}" ] && continue; seen[$n]=1
    p="$(find_lib "$n")"
    install -D -m 755 "$(readlink -f "$B/$p")" "$S/usr/lib/aarch64-linux-gnu/$n"
    LIBS+=("$S/usr/lib/aarch64-linux-gnu/$n")
  done
done
cp "$(readlink -f "$B/usr/lib/ld-linux-aarch64.so.1")" "$S/usr/lib/ld-linux-aarch64.so.1"
ln -s mke2fs "$S/usr/sbin/mkfs.ext4"

# 3. USB gadget modules (libcomposite, u_ether, usb_f_ncm)
for m in libcomposite u_ether usb_f_ncm; do
  k="$(find "$KOUT/lib/modules/$KVER" -name "$m.ko*" | head -1)"
  [ -n "$k" ] || { echo "module $m ontbreekt"; exit 1; }
  case "$k" in *.xz) xz -dc "$k" ;; *.zst) zstd -dc "$k" ;; *) cat "$k" ;; esac > "$S/usr/lib/modules/$m.ko"
done

# 4. scripts and config
install -m 755 "$PROJ/initramfs/init" "$S/init"
install -m 755 "$PROJ/initramfs/bin/px30-rescue" "$S/usr/bin/px30-rescue"
install -m 644 "$PROJ/initramfs/lib/px30-state.sh" "$S/usr/lib/px30-state.sh"
printf 'root:x:0:0:root:/root:/bin/sh\n' > "$S/etc/passwd"
printf 'root:x:0:\n' > "$S/etc/group"
printf '/bin/sh\n' > "$S/etc/shells"
printf 'start 10.55.0.10\nend 10.55.0.20\ninterface usb0\nlease_file /tmp/udhcpd.leases\noption subnet 255.255.255.0\n' > "$S/etc/udhcpd.conf"
if [ -n "$PUBKEY" ]; then install -m 600 "$PUBKEY" "$S/root/.ssh/authorized_keys"; else : > "$S/root/.ssh/authorized_keys"; chmod 600 "$S/root/.ssh/authorized_keys"; fi
chmod 700 "$S/root" "$S/root/.ssh"
# no host key in the image: px30-rescue uses /data/ssh/rescue_host_key (made by
# the normal system) or generates one
# busybox applets as symlinks (only where no real binary exists)
for a in $(qemu-aarch64-static "$S/usr/bin/busybox" --list); do
  [ -e "$S/usr/bin/$a" ] || [ -e "$S/usr/sbin/$a" ] || ln -s busybox "$S/usr/bin/$a"
done

# 5. cpio (newc, all root-owned, with /dev/console) + gzip
python3 "$PROJ/initramfs/mkcpio.py" "$S" | gzip -9 > "$W/initramfs.cpio.gz"
echo "initramfs: $(du -h "$W/initramfs.cpio.gz" | cut -f1)  ($(find "$S" | wc -l) entries, $(du -sh "$S" | cut -f1) unpacked)"
qemu-aarch64-static -L "$S" "$S/usr/sbin/sfdisk" --version
qemu-aarch64-static -L "$S" "$S/usr/bin/zstd" --version | head -1
