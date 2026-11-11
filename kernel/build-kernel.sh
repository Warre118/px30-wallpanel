#!/usr/bin/env bash
# =====================================================================
# Build the kernel for the Smatek PX30 wall panel (in WSL/Linux, arm64 cross).
#
# Usage: bash kernel/build-kernel.sh [prepare|build|all]
#   prepare : put patches + DTS + config into the kernel tree
#   build   : build Image, DTB and modules -> ~/px30-build/out
# Needs: gcc-aarch64-linux-gnu, bc, bison, flex, libssl-dev, libelf-dev
# =====================================================================
set -euo pipefail
PROJ="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${SRC:-$HOME/px30-build/linux}"
OUT="${OUT:-$HOME/px30-build/out}"
export ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-
JOBS="$(nproc)"
DTS_DIR="$SRC/arch/arm64/boot/dts/rockchip"

prepare() {
  cd "$SRC"
  # 1. panel patch (idempotent)
  if ! grep -q smatek_px30_480 drivers/gpu/drm/panel/panel-sitronix-st7701.c; then
    git apply "$PROJ/kernel/0001-drm-panel-st7701-add-smatek-px30-wallpanel.patch"
    echo "patch applied"
  else
    echo "patch was already applied"
  fi
  # 1b. DSI host: power on the PHY before dphy_enable (black screen fix)
  if ! grep -q "Rockchip vendor driver does" drivers/gpu/drm/bridge/synopsys/dw-mipi-dsi.c; then
    git apply "$PROJ/kernel/0002-drm-bridge-dw-mipi-dsi-power-on-phy-before-dphy-enable.patch"
    echo "patch 0002 applied"
  fi
  # 1c. USB gadget: no descriptor DMA on Rockchip (Windows: Device Descriptor Request Failed)
  if ! grep -q rk_gadget_dma drivers/usb/dwc2/params.c; then
    git apply "$PROJ/kernel/0003-usb-dwc2-rockchip-gadget-dma-mode-param.patch"
    echo "patch 0003 applied"
  fi
  # 1d. touch: CST226SE variant of hynitron_cstxxx (no cst3xx bootloader check)
  if ! grep -q cst226se drivers/input/touchscreen/hynitron_cstxxx.c; then
    git apply "$PROJ/kernel/0004-input-hynitron_cstxxx-add-cst226se.patch"
    echo "patch 0004 applied"
  fi
  # (0005-bluetooth-hci_h5-rtl-resync-seq… deliberately NOT applied: tested, the chip
  #  sends nothing after the last fragment -> no seq problem; see docs/development-notes.md)
  # 2. DTS + Makefile line
  cp "$PROJ/dts/px30-smatek-wallpanel.dts" "$DTS_DIR/"
  sed -i '/px30-smatek-wallpanel.dtb/d' "$DTS_DIR/Makefile"
  sed -i '/px30-evb.dtb/a dtb-$(CONFIG_ARCH_ROCKCHIP) += px30-smatek-wallpanel.dtb' "$DTS_DIR/Makefile"
  grep -n 'px30-evb\|px30-smatek' "$DTS_DIR/Makefile"
  # 3. config = defconfig + fragments (features, then trimming)
  local frags="px30-wallpanel.config px30-trim.config"
  for f in $frags; do cp "$PROJ/kernel/$f" "$SRC/$f"; done
  scripts/kconfig/merge_config.sh -m arch/arm64/configs/defconfig $frags >/dev/null
  make olddefconfig >/dev/null
  # 4. check that every requested option really is set that way (also =n)
  local bad=0
  while IFS= read -r line; do
    if [[ "$line" =~ ^#\ (CONFIG_[A-Z0-9_]+)\ is\ not\ set ]]; then
      k="${BASH_REMATCH[1]}"
      if grep -qE "^${k}=[ym]" .config; then echo "MISMATCH: $k must be off, is $(grep -E "^${k}=" .config)"; bad=1; fi
    else
      k="${line%%=*}"
      have="$(grep -E "^${k}=" .config || echo "${k} missing")"
      if [ "$have" != "$line" ]; then echo "MISMATCH: $line -> $have"; bad=1; fi
    fi
  done < <(cat $frags | grep -E '^CONFIG_[A-Z0-9_]+=|^# CONFIG_[A-Z0-9_]+ is not set')
  [ "$bad" = 0 ] && echo "config: all options from the fragments are set correctly"
  echo "config: $(grep -cE '=y$' .config) built in, $(grep -cE '=m$' .config) modules"
}

build() {
  cd "$SRC"
  local t0=$SECONDS
  make -j"$JOBS" Image modules dtbs 2>&1 | tail -n 20
  rm -rf "$OUT"; mkdir -p "$OUT/boot"
  cp arch/arm64/boot/Image "$OUT/boot/"
  cp "$DTS_DIR/px30-smatek-wallpanel.dtb" "$OUT/boot/"
  make -s modules_install INSTALL_MOD_PATH="$OUT" INSTALL_MOD_STRIP=1
  cp .config "$OUT/boot/config-$(make -s kernelrelease)"
  echo "done in $((SECONDS - t0)) s:"
  ls -la "$OUT/boot"; du -sh "$OUT/lib/modules"/*
}

case "${1:-all}" in
  prepare) prepare ;;
  build) build ;;
  all) prepare; build ;;
  *) echo "usage: $0 [prepare|build|all]"; exit 1 ;;
esac
