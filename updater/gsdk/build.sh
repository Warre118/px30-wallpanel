#!/usr/bin/env bash
# Build one radio firmware for the PX30 panel's TYZS13 (EFR32MG13, series 1)
# with the repo's zigbee/ scripts, and describe the result in build-info.json.
#
#   build.sh <ot-rcp|ncp|router> <outdir>
#
# Run by fwpipe (GsdkBackend) in the fwpipe container; also runs
# outside a container when the environment points at a toolchain:
#   T        folder with slc_cli/ (and, without MKGBL, commander/)   [/opt/silabs]
#   SDK      Gecko SDK                                               [/opt/gecko_sdk]
#   GCC_DIR  Arm GNU toolchain                                       [/opt/gcc-arm-none-eabi]
#   SLC_TOOL_PATH  python adapter pack for slc-cli 6                 [unset]
#   MKGBL    GBL maker; default: mkgbl.py next to this script (needs pygbl)
#   BAUD     UART speed                                              [115200]
set -euo pipefail
TARGET="${1:?usage: build.sh <ot-rcp|ncp|router> <outdir>}"
OUTDIR="${2:?usage: build.sh <ot-rcp|ncp|router> <outdir>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ZIG="${ZIGBEE_DIR:-$HERE/../../zigbee}"
export T="${T:-/opt/silabs}" SDK="${SDK:-/opt/gecko_sdk}" GCC_DIR="${GCC_DIR:-/opt/gcc-arm-none-eabi}"
export BAUD="${BAUD:-115200}"
PYTHON="${PYTHON:-python3}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export DIST="$WORK/dist" PX30_OUT="$WORK/px30-out"
mkdir -p "$OUTDIR"

GSDK_VERSION="$(sed -n 's/^sdk_version: *"\(.*\)"/\1/p' "$SDK/gecko_sdk.slcs" | head -1)"
[ -n "$GSDK_VERSION" ] || { echo "ERROR: no sdk_version in $SDK/gecko_sdk.slcs"; exit 1; }
export GSDK_LABEL="gsdk$GSDK_VERSION"
define() { sed -n "s/^#define $2  *\"\{0,1\}\([^\"]*\)\"\{0,1\} *\$/\1/p" "$1" | head -1; }

case "$TARGET" in
ot-rcp)
  export OUT="$WORK/ot-rcp"
  OT="$(define "$SDK/protocol/openthread/include/sl_openthread_package_info.h" PACKAGE_VERSION)"
  VERSION="gsdk-$GSDK_VERSION"
  META="{\"metadata_version\": 2, \"sdk_version\": \"$GSDK_VERSION\", \"fw_type\": \"openthread_rcp\", \"ot_rcp_version\": \"SL-OPENTHREAD/${OT:-unknown}\", \"baudrate\": $BAUD}"
  DESC="OpenThread RCP ${OT:+$OT }(Gecko SDK $GSDK_VERSION; Spinel, $BAUD hw-flow, can return to the Gecko bootloader)"
  GEN=gen-ot-rcp.sh BUILD=build-ot-rcp.sh
  ;;
ncp|router)
  CFG="$SDK/protocol/zigbee/stack/config/config.h"
  v() { local x; x="$(define "$CFG" "EMBER_$1_VERSION")"; [ -n "$x" ] || x="$(define "$CFG" "SL_ZIGBEE_$1_VERSION")"; echo "$x"; }
  VERSION="$(v MAJOR).$(v MINOR).$(v PATCH).$(v SPECIAL)"
  case "$VERSION" in *..*|.*|*.) echo "ERROR: cannot read the EmberZNet version from $CFG ($VERSION)"; exit 1;; esac
  ;;&
router)
  export OUT="$WORK/router"
  META="{\"metadata_version\": 2, \"sdk_version\": \"$GSDK_VERSION\", \"fw_type\": \"zigbee_router\", \"baudrate\": $BAUD}"
  DESC="Zigbee router $VERSION (Gecko SDK $GSDK_VERSION; stand-alone, joins an open network by itself; CLI on the UART, $BAUD hw-flow, 'bootloader reboot' for updates)"
  GEN=gen-router.sh BUILD=build-router.sh
  ;;
ncp)
  export OUT="$WORK/ncp-uart-hw"
  META="{\"metadata_version\": 2, \"sdk_version\": \"$GSDK_VERSION\", \"fw_type\": \"zigbee_ncp\", \"ezsp_version\": \"$VERSION\", \"baudrate\": $BAUD}"
  DESC="EmberZNet NCP $VERSION (Gecko SDK $GSDK_VERSION; EZSP, $BAUD hw-flow) for ZHA / Zigbee2MQTT (ember)"
  GEN=gen-ncp.sh BUILD=build-ncp.sh
  ;;
*) echo "unknown target $TARGET (ot-rcp, ncp or router)"; exit 1;;
esac

export MKGBL_METADATA="$META"
export MKGBL="${MKGBL:-$PYTHON $HERE/mkgbl.py}"
bash "$ZIG/$GEN"
bash "$ZIG/$BUILD"        # also checks that the image can return to the bootloader

GBL="$(ls "$DIST"/*.gbl)"
[ "$(echo "$GBL" | wc -l)" = 1 ] || { echo "ERROR: expected one .gbl, got: $GBL"; exit 1; }
cp "$GBL" "$OUTDIR/"
cat > "$OUTDIR/build-info.json" <<EOF
{"target": "$TARGET", "version": "$VERSION", "gsdk": "$GSDK_VERSION", "baudrate": $BAUD,
 "flow_control": "hardware", "file": "$(basename "$GBL")", "description": "$DESC"}
EOF
echo "== done: $OUTDIR/$(basename "$GBL") ($VERSION)"
