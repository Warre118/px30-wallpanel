#!/usr/bin/env bash
# =====================================================================
# Builds the PX30 release files (in WSL) from the kernel, initramfs and
# rootfs builds:
#   px30-boot.img      kernel + DTB + initramfs, px30.mode=normal  -> boot
#   px30-rescue.img    same, px30.mode=rescue                      -> recovery
#   px30-rootfs.img.zst  ext4 rootfs (zstd, with checksums)        -> rootfs_a / factory
#   VERSION, SHA256SUMS
# Output: ${PX30_OUT:-~/px30-out}/release-<version>/
#
#   bash tools/make-release.sh [version]
# Needs: kernel/build-kernel.sh, initramfs/build-initramfs.sh and
# rootfs/build-rootfs.sh to have run. zstd = the arm64 binary from the
# initramfs package tree under qemu (WSL has no zstd).
# =====================================================================
set -euo pipefail
PROJ="$(cd "$(dirname "$0")/.." && pwd)"
VER="${1:-$(date +%Y.%m.%d-%H%M)}"
O="$HOME/px30-build/out/boot"
I="$HOME/px30-build/initramfs/initramfs.cpio.gz"
R="$HOME/px30-build/rootfs/px30-rootfs.ext4"
BASE="$HOME/px30-build/initramfs/base"
D="${PX30_OUT:-$HOME/px30-out}/release-$VER"
mkdir -p "$D"
for f in "$O/Image" "$O/px30-smatek-wallpanel.dtb" "$I" "$R"; do [ -f "$f" ] || { echo "ontbreekt: $f"; exit 1; }; done

python3 "$PROJ/boot/make-recovery-img.py" "$O/Image" "$O/px30-smatek-wallpanel.dtb" "$PROJ/kernel/cmdline-boot.txt" "$D/px30-boot.img" "$I" | head -1
python3 "$PROJ/boot/make-recovery-img.py" "$O/Image" "$O/px30-smatek-wallpanel.dtb" "$PROJ/kernel/cmdline-rescue.txt" "$D/px30-rescue.img" "$I" | head -1

# the image must be clean before it becomes the golden copy
/sbin/e2fsck -fn "$R" >/dev/null
echo "rootfs comprimeren ..."
qemu-aarch64-static -L "$BASE" "$BASE/usr/bin/zstd" -q -T0 -12 --check -f -c "$R" > "$D/px30-rootfs.img.zst"
qemu-aarch64-static -L "$BASE" "$BASE/usr/bin/zstd" -q -t "$D/px30-rootfs.img.zst"

echo "$VER" > "$D/VERSION"
(cd "$D" && sha256sum px30-boot.img px30-rescue.img px30-rootfs.img.zst VERSION > SHA256SUMS)
ls -la "$D"
