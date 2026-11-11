#!/usr/bin/env bash
# =====================================================================
# PX30 wallpanel – READ-ONLY volledige eMMC-back-up via adb root
# v2: in blokken van 256 MiB, hervatbaar, met sha256 per blok.
#
# Schrijft NIETS naar het panel: alle dd-aanroepen op het panel
# gebruiken enkel if=<panel>. Enkel op de PC wordt geschreven.
#
# Gebruik:  bash backup_emmc.sh <serial> <run-map>
#   bv.     bash backup_emmc.sh <panel-ip>:5555 ./PX30-backup/emmc-$(date +%Y%m%d)
# Opnieuw starten met dezelfde run-map gaat verder waar het stopte.
# Daarna:   python verify_backup.py <run-map>
# =====================================================================
set -u
SERIAL="$1"; RUN="$2"
ADB="adb -s $SERIAL"
BS=4194304            # 4 MiB
PER_CHUNK=64          # 64 x 4 MiB = 256 MiB per blok
TIMEOUT=600           # s per blok; daarna geldt de overdracht als vastgelopen
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

ensure_root || { log "FOUT: geen root/verbinding"; exit 1; }
log "run-map: $RUN"

# metadata (opnieuw vastleggen kan geen kwaad)
$ADB shell 'for p in /sys/block/mmcblk1/mmcblk1p*; do n=$(basename $p); echo "$n $(cat $p/start) $(cat $p/size) $(grep PARTNAME $p/uevent | cut -d= -f2)"; done' | tr -d '\r' | sort -V > "$RUN/partitions.txt"
$ADB shell 'cat /sys/block/mmcblk1/size; cat /sys/block/mmcblk1/device/cid; cat /sys/block/mmcblk1/device/name' | tr -d '\r' > "$RUN/emmc-info.txt"
$ADB shell 'getprop ro.build.fingerprint; getprop ro.serialno; cat /proc/version' | tr -d '\r' > "$RUN/device-info.txt"

SECTORS=$(head -1 "$RUN/emmc-info.txt")
TOTAL_BYTES=$((SECTORS * 512))
BLOCKS=$(( (TOTAL_BYTES + BS - 1) / BS ))
CHUNKS=$(( (BLOCKS + PER_CHUNK - 1) / PER_CHUNK ))
log "eMMC: $TOTAL_BYTES bytes = $CHUNKS blokken van 256 MiB"

# Eén blok ophalen en controleren. $1 = bronapparaat, $2 = blok-index,
# $3 = doelbestand op PC, $4 = offset in 4MiB-blokken in het doelbestand
fetch_chunk() {
  local dev=$1 i=$2 out=$3 seek=$4 skip=$(( $2 * PER_CHUNK )) tmp="$RUN/.chunk.tmp"
  local want got attempt
  for attempt in $(seq 1 $RETRIES); do
    ensure_root || { log "  geen verbinding, opnieuw over 20 s"; sleep 20; continue; }
    want=$(timeout 300 $ADB shell "dd if=$dev bs=$BS skip=$skip count=$PER_CHUNK 2>/dev/null | sha256sum" | cut -d' ' -f1)
    timeout $TIMEOUT $ADB exec-out "dd if=$dev bs=$BS skip=$skip count=$PER_CHUNK 2>/dev/null" > "$tmp"
    got=$(sha256sum "$tmp" | cut -d' ' -f1)
    if [ -n "$want" ] && [ "$want" = "$got" ]; then
      dd if="$tmp" of="$out" bs=$BS seek=$seek conv=notrunc status=none
      echo "$i $got" >> "$MAN"; rm -f "$tmp"; return 0
    fi
    log "  blok $i poging $attempt: panel=${want:0:12} pc=${got:0:12} ($(stat -c %s "$tmp") bytes)"
    # pas na meerdere pogingen: aanvaarden als het blok op het panel zelf verandert (live /data)
    if [ "$attempt" -ge 2 ] && [ "$(stat -c %s "$tmp")" -gt 0 ]; then
      local again
      again=$(timeout 300 $ADB shell "dd if=$dev bs=$BS skip=$skip count=$PER_CHUNK 2>/dev/null | sha256sum" | cut -d' ' -f1)
      if [ "$again" != "$want" ] && [ "$attempt" -ge 3 ]; then
        log "  blok $i verandert op het panel zelf (live data) -> aanvaard als LIVE"
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
  log "blok $((i+1))/$CHUNKS"
  fetch_chunk /dev/block/mmcblk1 "$i" "$IMG" $(( i * PER_CHUNK )) || { log "  blok $i MISLUKT"; FAILED=1; }
done
# afkappen op exacte schijfgrootte (eerdere, langere pogingen)
truncate -s "$TOTAL_BYTES" "$IMG"
[ "$FAILED" = 0 ] || { log "Niet alle blokken gelukt; start opnieuw met dezelfde run-map om te hervatten."; exit 2; }
log "volledige image compleet: $(stat -c %s "$IMG") bytes"

# losse kritieke partities (klein) – kopie uit de image, geen extra overdracht
while read -r dev start size name; do
  case "$name" in cache|system|vendor|userdata) continue;; esac
  dd if="$IMG" of="$RUN/partitions/$name.img" bs=512 skip="$start" count="$size" status=none
done < "$RUN/partitions.txt"
log "losse partitie-images uit de image geknipt"

# referentie-hashes op het panel
log "sha256 per partitie op het panel berekenen"
$ADB shell 'cd /dev/block; sha256sum mmcblk1boot0 mmcblk1boot1 mmcblk1p*' | tr -d '\r' > "$RUN/sha256-panel.txt"
log "KLAAR. Controleer met: python verify_backup.py \"$RUN\""
