#!/usr/bin/env bash
# =====================================================================
# Kernel voor het Smatek PX30-wallpanel bouwen (in WSL/Linux, arm64-cross).
#
# Gebruik: bash kernel/build-kernel.sh [prepare|build|all]
#   prepare : patch + DTS + config in de kernelboom zetten
#   build   : Image, DTB en modules bouwen -> ~/px30-build/out
# Vereist: gcc-aarch64-linux-gnu, bc, bison, flex, libssl-dev, libelf-dev
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
  # 1. paneelpatch (idempotent)
  if ! grep -q smatek_px30_480 drivers/gpu/drm/panel/panel-sitronix-st7701.c; then
    git apply "$PROJ/kernel/0001-drm-panel-st7701-add-smatek-px30-wallpanel.patch"
    echo "patch toegepast"
  else
    echo "patch was al toegepast"
  fi
  # 1b. DSI-host: PHY aanzetten vóór dphy_enable (zwart-scherm-fix, 11-11-2026)
  if ! grep -q "Rockchip vendor driver does" drivers/gpu/drm/bridge/synopsys/dw-mipi-dsi.c; then
    git apply "$PROJ/kernel/0002-drm-bridge-dw-mipi-dsi-power-on-phy-before-dphy-enable.patch"
    echo "patch 0002 toegepast"
  fi
  # 1c. USB-gadget: geen descriptor-DMA op Rockchip (Windows: Device Descriptor Request Failed)
  if ! grep -q rk_gadget_dma drivers/usb/dwc2/params.c; then
    git apply "$PROJ/kernel/0003-usb-dwc2-rockchip-gadget-dma-mode-param.patch"
    echo "patch 0003 toegepast"
  fi
  # 1d. touch: CST226SE-variant van hynitron_cstxxx (geen cst3xx-bootloadercheck)
  if ! grep -q cst226se drivers/input/touchscreen/hynitron_cstxxx.c; then
    git apply "$PROJ/kernel/0004-input-hynitron_cstxxx-add-cst226se.patch"
    echo "patch 0004 toegepast"
  fi
  # (0005-bluetooth-hci_h5-rtl-resync-seq… bewust NIET toegepast: getest op 11-11-2026,
  #  de chip stuurt na het laatste fragment niets meer -> geen seq-probleem; zie BEVINDINGEN)
  # 2. DTS + Makefile-regel
  cp "$PROJ/dts/px30-smatek-wallpanel.dts" "$DTS_DIR/"
  sed -i '/px30-smatek-wallpanel.dtb/d' "$DTS_DIR/Makefile"
  sed -i '/px30-evb.dtb/a dtb-$(CONFIG_ARCH_ROCKCHIP) += px30-smatek-wallpanel.dtb' "$DTS_DIR/Makefile"
  grep -n 'px30-evb\|px30-smatek' "$DTS_DIR/Makefile"
  # 3. config = defconfig + fragmenten (functies, daarna inkorten)
  local frags="px30-wallpanel.config px30-trim.config"
  for f in $frags; do cp "$PROJ/kernel/$f" "$SRC/$f"; done
  scripts/kconfig/merge_config.sh -m arch/arm64/configs/defconfig $frags >/dev/null
  make olddefconfig >/dev/null
  # 4. controleren dat elke gevraagde optie ook echt zo staat (ook =n)
  local bad=0
  while IFS= read -r line; do
    if [[ "$line" =~ ^#\ (CONFIG_[A-Z0-9_]+)\ is\ not\ set ]]; then
      k="${BASH_REMATCH[1]}"
      if grep -qE "^${k}=[ym]" .config; then echo "AFWIJKING: $k moet uit, staat $(grep -E "^${k}=" .config)"; bad=1; fi
    else
      k="${line%%=*}"
      have="$(grep -E "^${k}=" .config || echo "${k} ontbreekt")"
      if [ "$have" != "$line" ]; then echo "AFWIJKING: $line -> $have"; bad=1; fi
    fi
  done < <(cat $frags | grep -E '^CONFIG_[A-Z0-9_]+=|^# CONFIG_[A-Z0-9_]+ is not set')
  [ "$bad" = 0 ] && echo "config: alle opties uit de fragmenten staan correct"
  echo "config: $(grep -cE '=y$' .config) ingebouwd, $(grep -cE '=m$' .config) modules"
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
  echo "klaar in $((SECONDS - t0)) s:"
  ls -la "$OUT/boot"; du -sh "$OUT/lib/modules"/*
}

case "${1:-all}" in
  prepare) prepare ;;
  build) build ;;
  all) prepare; build ;;
  *) echo "gebruik: $0 [prepare|build|all]"; exit 1 ;;
esac
