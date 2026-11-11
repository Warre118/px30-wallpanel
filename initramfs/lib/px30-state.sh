# PX30 boot state (shared by the initramfs and px30-ctl in the rootfs).
#
# One 512-byte text record in the misc partition at 1 MiB (sector 2048),
# well away from the Android bootloader message at 0 and 16 KiB:
#   PX30S1 slot=a tries=0 fallback= action= rescues=0
# slot      a|b      rootfs slot to boot
# tries     n        boots started since the last "boot ok"; the initramfs
#                    increments it, px30-boot-ok resets it to 0
# fallback  a|b|""   slot to return to when the active slot keeps failing
#                    (set by an update, cleared once the new slot is ok)
# action    ""|rescue|reinstall|factory-reset   one-shot request for the
#                    rescue mode (set by px30-ctl, cleared by the rescue)
# rescues   n        automatic repairs in a row (stops endless repair loops)
# Busybox sh and dash compatible.

PX30_STATE_SECTOR=2048
PX30_MAX_TRIES=3

px30_part() { # PARTNAME -> /dev/mmcblk0pN (no udev needed)
	for u in /sys/class/block/*/uevent; do
		grep -qx "PARTNAME=$1" "$u" 2>/dev/null || continue
		echo "/dev/$(sed -n 's/^DEVNAME=//p' "$u")"
		return 0
	done
	return 1
}

state_load() {
	slot=a; tries=0; fallback=; action=; rescues=0
	_m=$(px30_part misc) || return 1
	_rec=$(dd if="$_m" bs=512 skip=$PX30_STATE_SECTOR count=1 2>/dev/null | tr -d '\000' | head -n 1)
	case "$_rec" in "PX30S1 "*) ;; *) return 0 ;; esac
	for _kv in ${_rec#PX30S1 }; do
		_v=${_kv#*=}
		case "$_v" in *[!a-z0-9-]*) continue ;; esac   # only safe values
		case "$_kv" in
		slot=a|slot=b) slot=$_v ;;
		tries=*) tries=${_v:-0} ;;
		fallback=*) fallback=$_v ;;
		action=*) action=$_v ;;
		rescues=*) rescues=${_v:-0} ;;
		esac
	done
	case "$tries" in ''|*[!0-9]*) tries=0 ;; esac
	case "$rescues" in ''|*[!0-9]*) rescues=0 ;; esac
	return 0
}

state_save() {
	_m=$(px30_part misc) || return 1
	printf 'PX30S1 slot=%s tries=%s fallback=%s action=%s rescues=%s\n' \
		"$slot" "$tries" "$fallback" "$action" "$rescues" |
		dd of="$_m" bs=512 seek=$PX30_STATE_SECTOR count=1 conv=sync,notrunc 2>/dev/null
	sync
}

state_show() {
	printf 'slot=%s tries=%s fallback=%s action=%s rescues=%s\n' \
		"$slot" "$tries" "$fallback" "$action" "$rescues"
}

# Android bootloader message (misc @ 16 KiB): the vendor U-Boot boots the
# recovery partition while it says "boot-recovery". The new layout never
# needs it; clear it so the reboot flags (reboot recovery/loader) work.
bcb_clear() {
	_m=$(px30_part misc) || return 1
	for _off in 0 16384; do
		case "$(dd if="$_m" bs=1 skip=$_off count=13 2>/dev/null)" in
		boot-recovery) dd if=/dev/zero of="$_m" bs=1 seek=$_off count=32 conv=notrunc 2>/dev/null ;;
		esac
	done
	sync
}
