#!/usr/bin/env bash
# otbr-agent (ot-br-posix uit Gecko SDK 4.4.6, zelfde OpenThread als de RCP) cross-bouwen voor arm64.
# Vereist (WSL): g++-aarch64-linux-gnu cmake ninja-build pkg-config + :arm64 dev-pakketten
#   libavahi-client-dev libavahi-common-dev libsystemd-dev
set -euo pipefail
GSDK_TP="$HOME/px30-build/silabs/gecko_sdk/util/third_party"
SRC="$HOME/px30-build/zigbee/ot-br-posix"
B="$HOME/px30-build/zigbee/otbr-build"
OUT="$HOME/px30-build/zigbee/otbr-dist"
# Werkkopie (SDK zelf niet wijzigen); Silabs levert third_party/openthread/repo niet mee:
# dat is de OpenThread van de SDK zelf (zelfde commit als de RCP-firmware).
if [ ! -d "$SRC" ]; then
  cp -a "$GSDK_TP/ot-br-posix" "$SRC"
  ln -s "$GSDK_TP/openthread" "$SRC/third_party/openthread/repo"
fi
# GCC 14 geeft vals-positieve -Warray-bounds in mbedtls; niet als fout behandelen.
sed -i 's/ -Werror -Wfatal-errors/ -Wfatal-errors/' "$SRC/CMakeLists.txt"
mkdir -p "$B"
cat >"$B/toolchain.cmake" <<'EOF'
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)
set(CMAKE_C_COMPILER aarch64-linux-gnu-gcc)
set(CMAKE_CXX_COMPILER aarch64-linux-gnu-g++)
set(CMAKE_FIND_ROOT_PATH /usr/aarch64-linux-gnu /usr/lib/aarch64-linux-gnu)
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE BOTH)
EOF
export PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig
cmake -S "$SRC" -B "$B" -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE="$B/toolchain.cmake" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX=/usr \
  -DBUILD_TESTING=OFF \
  -DOTBR_MDNS=avahi \
  -DOTBR_INFRA_IF_NAME=wlan0 \
  -DOTBR_RADIO_URL="spinel+hdlc+uart:///dev/ttyS0?uart-baudrate=115200&uart-flow-control" \
  -DOTBR_BORDER_ROUTING=ON \
  -DOTBR_SRP_ADVERTISING_PROXY=ON \
  -DOTBR_DNSSD_DISCOVERY_PROXY=ON \
  -DOTBR_REST=ON \
  -DOTBR_DBUS=OFF \
  -DOTBR_WEB=OFF \
  -DOTBR_BACKBONE_ROUTER=OFF \
  -DOTBR_TREL=OFF \
  -DOTBR_NAT64=OFF \
  -DOTBR_VENDOR_NAME=Smatek -DOTBR_PRODUCT_NAME=PX30-Panel \
  -DOT_FIREWALL=OFF \
  -DOT_POSIX_SETTINGS_PATH='"/var/lib/thread"' \
  "$@" > "$B/cmake.log" 2>&1 || { tail -30 "$B/cmake.log"; exit 1; }
ninja -C "$B" otbr-agent ot-ctl 2>&1 | tail -15
rm -rf "$OUT"; mkdir -p "$OUT/usr/sbin"
find "$B" -name otbr-agent -type f -perm -u+x -exec cp {} "$OUT/usr/sbin/" \;
find "$B" -name ot-ctl -type f -perm -u+x -exec cp {} "$OUT/usr/sbin/" \;
aarch64-linux-gnu-strip "$OUT"/usr/sbin/*
ls -la "$OUT/usr/sbin"; file "$OUT/usr/sbin/otbr-agent"
aarch64-linux-gnu-readelf -d "$OUT/usr/sbin/otbr-agent" | grep NEEDED
