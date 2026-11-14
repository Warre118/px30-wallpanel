#!/usr/bin/env bash
# =====================================================================
# PX30 wall panel – READ-ONLY full eMMC backup via adb root
# v2: in chunks of 256 MiB, resumable, with a sha256 per chunk.
#
# Writes NOTHING to the panel: every dd call on the panel only uses
# if=<panel>. Only the PC is written to.
#
# Usage:    bash backup_emmc.sh <serial> <run-dir>
#   e.g.    bash backup_emmc.sh <panel-ip>:5555 ./PX30-backup/emmc-$(date +%Y%m%d)
# Starting again with the same run-dir resumes where it stopped.
# Then:     python verify_backup.py <run-dir>
# =====================================================================
set -u
SERIAL="$1"; RUN="$2"
ADB="adb -s $SERIAL"
BS=4194304            # 4 MiB
PER_CHUNK=64          # 64 x 4 MiB = 256 MiB per chunk
TIMEOUT=600           # s per chunk; after that the transfer counts as stuck
RETRIES=4
mkdir -p "$RUN/partitions"
LOG="$RUN/backup.log"; MAN="$RUN/chunks.sha256"; touch "$MAN"
IMG="$RUN/emmc-full.img"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

ensure_root() {
  adb connect "$SERIAL" >/dev/null 2>&1
  timeout 30 $ADB wait-for-device
  if [ "$(timeout 20 $ADB shell id -u | tr -d '\r')" != 0 ]; then
    timeout 30 $ADB root >>"$LOG" 2>&1; sleep 3; adb connect "$SERIAL" >/dev/null 2>&1
    timeout 30 $ADB wait-for-device
  fi
  [ "$(timeout 20 $ADB shell id -u | tr -d '\r')" = 0 ]
}

ensure_root || { log "ERROR: no root/connection"; exit 1; }
log "run-dir: $RUN"

# metadata (recording it again does no harm)
$ADB shell 'for p in /sys/block/mmcblk1/mmcblk1p*; do n=$(basename $p); echo "$n $(cat $p/start) $(cat $p/size) $(grep PARTNAME $p/uevent | cut -d= -f2)"; done' | tr -d '\r' | sort -V > "$RUN/partitions.txt"
$ADB shell 'cat /sys/block/mmcblk1/size; cat /sys/block/mmcblk1/device/cid; cat /sys/block/mmcblk1/device/name' | tr -d '\r' > "$RUN/emmc-info.txt"
$ADB shell 'getprop ro.build.fingerprint; getprop ro.serialno; cat /proc/version' | tr -d '\r' > "$RUN/device-info.txt"

SECTORS=$(head -1 "$RUN/emmc-info.txt")
TOTAL_BYTES=$((SECTORS * 512))
BLOCKS=$(( (TOTAL_BYTES + BS - 1) / BS ))
CHUNKS=$(( (BLOCKS + PER_CHUNK - 1) / PER_CHUNK ))
log "eMMC: $TOTAL_BYTES bytes = $CHUNKS chunks of 256 MiB"

# Fetch and check one chunk. $1 = source device, $2 = chunk index,
# $3 = target file on the PC, $4 = offset in 4 MiB blocks in the target file
fetch_chunk() {
  local dev=$1 i=$2 out=$3 seek=$4 skip=$(( $2 * PER_CHUNK )) tmp="$RUN/.chunk.tmp"
  local want got attempt
  for attempt in $(seq 1 $RETRIES); do
    ensure_root || { log "  no connection, retrying in 20 s"; sleep 20; continue; }
    want=$(timeout 300 $ADB shell "dd if=$dev bs=$BS skip=$skip count=$PER_CHUNK 2>/dev/null | sha256sum" | cut -d' ' -f1)
    timeout $TIMEOUT $ADB exec-out "dd if=$dev bs=$BS skip=$skip count=$PER_CHUNK 2>/dev/null" > "$tmp"
    got=$(sha256sum "$tmp" | cut -d' ' -f1)
    if [ -n "$want" ] && [ "$want" = "$got" ]; then
      dd if="$tmp" of="$out" bs=$BS seek=$seek conv=notrunc status=none
      echo "$i $got" >> "$MAN"; rm -f "$tmp"; return 0
    fi
    log "  chunk $i attempt $attempt: panel=${want:0:12} pc=${got:0:12} ($(stat -c %s "$tmp") bytes)"
    # only after several attempts: accept it if the chunk changes on the panel itself (live /data)
    if [ "$attempt" -ge 2 ] && [ "$(stat -c %s "$tmp")" -gt 0 ]; then
      local again
      again=$(timeout 300 $ADB shell "dd if=$dev bs=$BS skip=$skip count=$PER_CHUNK 2>/dev/null | sha256sum" | cut -d' ' -f1)
      if [ "$again" != "$want" ] && [ "$attempt" -ge 3 ]; then
        log "  chunk $i changes on the panel itself (live data) -> accepted as LIVE"
        dd if="$tmp" of="$out" bs=$BS seek=$seek conv=notrunc status=none
        echo "$i $got LIVE" >> "$MAN"; rm -f "$tmp"; return 0
      fi
    fi
  done
  return 1
}

FAILED=0
for i in $(seq 0 $((CHUNKS - 1))); do
  if grep -q "^$i " "$MAN"; then continue; fi
  log "chunk $((i+1))/$CHUNKS"
  fetch_chunk /dev/block/mmcblk1 "$i" "$IMG" $(( i * PER_CHUNK )) || { log "  chunk $i FAILED"; FAILED=1; }
done
# truncate to the exact disk size (earlier, longer attempts)
truncate -s "$TOTAL_BYTES" "$IMG"
[ "$FAILED" = 0 ] || { log "Not all chunks succeeded; start again with the same run-dir to resume."; exit 2; }
log "full image complete: $(stat -c %s "$IMG") bytes"

# separate critical partitions (small) – copied from the image, no extra transfer
while read -r dev start size name; do
  case "$name" in cache|system|vendor|userdata) continue;; esac
  dd if="$IMG" of="$RUN/partitions/$name.img" bs=512 skip="$start" count="$size" status=none
done < "$RUN/partitions.txt"
log "separate partition images cut from the image"

# eMMC boot areas (4 MiB each, not part of mmcblk1): small, fetched directly
for b in mmcblk1boot0 mmcblk1boot1; do
  ensure_root || { log "ERROR: no connection for $b"; exit 1; }
  timeout 120 $ADB exec-out "dd if=/dev/block/$b bs=1048576 2>/dev/null" > "$RUN/$b.img"
done
log "boot areas saved: mmcblk1boot0.img, mmcblk1boot1.img"

# reference hashes on the panel
log "computing sha256 per partition on the panel"
$ADB shell 'cd /dev/block; sha256sum mmcblk1boot0 mmcblk1boot1 mmcblk1p*' | tr -d '\r' > "$RUN/sha256-panel.txt"
log "DONE. Check with: python verify_backup.py \"$RUN\""
