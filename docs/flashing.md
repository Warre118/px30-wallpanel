# Backup, flashing and recovery

## Ground rules

- **Never write `uboot`, `trust` or anything before sector `0x4000` (the
  idblock).** As long as those stay intact, the BOOT button gets you into
  Loader mode, and from there every other partition can be rewritten. A broken
  bootloader would need Maskrom access, and that has not been found on this board.
- Make the full backup first and verify it.
- Feed the panel from its own power supply while flashing. If it is powered
  from a PC USB port, it can brown out during long transfers *from* the panel.
  All tools here therefore only upload (PC → panel).

## 1. Backup of the stock firmware

The stock Android is a `userdebug` build (`ro.adb.secure=0`): `adb root`
works without unlocking anything.

```sh
adb connect <panel-ip>:5555           # or use the OTG port with a data cable
bash backup_emmc.sh <panel-ip>:5555 ./PX30-backup/emmc-$(date +%Y%m%d)
python3 verify_backup.py ./PX30-backup/emmc-<date>
```

What `backup_emmc.sh` does:

- Read-only: every `dd` on the panel uses only `if=`. The only state change
  is `adb root`, which ends at the next reboot.
- Copies `/dev/block/mmcblk1` in 30 blocks of 256 MiB. Each block is hashed on
  the panel, transferred, and checked on the PC. A stalled block (a single 7 GiB
  stream over Wi-Fi stalled after 2.4 GB) is retried. Rerunning with the same
  directory resumes.
- Also saves `mmcblk1boot0/1`, every partition separately, the partition table
  and the panel-side hashes.
- `verify_backup.py` compares every partition range in `emmc-full.img` with
  the hashes taken on the panel. `userdata` is expected to differ, because Android keeps
  writing to it.

Notes:

- toybox `dd` on the panel does not accept `bs=1M`. Use bytes (`bs=1048576`).
- The stock GPT has a CRC mismatch (`sgdisk` complains). Rockchip tools write it like this.
  Do not "repair" it.
- Keep the backup on NTFS/exFAT/ext4 (FAT32 cannot hold the 7.3 GiB image) and
  keep two copies.
- `audit_v2.sh` is the read-only hardware audit used during the port (DT,
  dmesg, partition table, kernel config). It is not needed for installing.

Optional: copy `/oem/media/bootanimation.zip` for the boot animation, and
`/vendor/etc/firmware/rtl8723d_fw*` if you want to experiment with Bluetooth.

## 2. Loader mode (the recovery channel)

Two ways in, both handled by the vendor U-Boot:

- **BOOT button**: hold it while plugging in power. After about 3 s Windows
  shows `Rockusb Device` (2207:330D).
- `adb reboot loader` (Android only), or the same reboot flag from Linux.

Use **RKDevTool** (v3.37 tested) on Windows. It must show "LOADER".

- *Export Image* reads raw eMMC sectors. Reading `0x4000` + `0x2000`
  sectors (the `uboot` partition) gave the same sha256 as the backup.
- *Download Image* with *Write by Address* writes raw sectors. Add your own row,
  untick all default rows, and set the address in 512-byte sectors.
- `rkdeveloptool` from WSL through usbipd did **not** work reliably (`ld`
  works, reading/writing hangs). Use RKDevTool on Windows.
- `reboot loader` is ignored while the misc partition holds a
  `boot-recovery` command. The BOOT button always works.

## 3. First install

You need a release directory (see [building.md](building.md)), preferably
built with Wi-Fi (`WIFI_SSID`/`WIFI_PSK`) or set up afterwards with the Wi-Fi
hotspot. Rescue mode lets `root` in over USB without a password, so no key is
needed for the install itself.

1. **Write the rescue image to the Android `recovery` partition.** On the stock
   layout `recovery` starts at sector `0x32000` and is 64 MiB. Check
   `partitions.txt` in your backup. Pick one of two ways:
   - RKDevTool, Loader mode, *Write by Address* `0x00032000` ←
     `px30-rescue.img`;
   - or adb:

     ```sh
     adb root
     adb push px30-rescue.img /data/local/tmp/
     adb shell 'dd if=/data/local/tmp/px30-rescue.img of=/dev/block/by-name/recovery bs=1048576 && sync'
     ```

2. **Boot it:** `adb reboot recovery` (or write the `boot-recovery` command to
   misc). The screen shows the rescue status, and the OTG port becomes a USB
   network adapter. The panel is `10.55.0.1` and hands out an address by DHCP.
   Windows recognises it as "UsbNcm Host Device".
3. **Install:**

   ```sh
   bash tools/px30-install.sh <release-dir> --repartition
   ```

   - `--repartition` writes the PX30 partition table
     ([system.md](system.md#emmc-layout)). `uboot`, `trust`, `misc` and
     `resource` stay at the same sectors. `security`, `smatek`, `keypart` and
     `STSN` are copied to their new place. All Android partitions
     (`boot`, `system`, `vendor`, `oem`, `userdata`, `stback`, …) are gone afterwards.
   - It then writes boot, recovery and rootfs_a, fills the factory partition
     and verifies everything on the panel. Reboot with
     `ssh root@10.55.0.1 px30-rescue reboot` (this also clears a
     `boot-recovery` command in misc).
   - Rescue-mode host keys: the script uses its own known_hosts file
     (`~/.ssh/known_hosts_px30_rescue`, alias `px30-rescue`).
4. The panel boots Debian (about 12 s) and opens the setup screen. See
   [usage.md](usage.md).

## 4. Updates (A/B over Wi-Fi)

```sh
bash tools/px30-ota.sh <release-dir> <panel-ip> --reboot
```

- Writes the rootfs to the **inactive** slot, refreshes `boot`, `recovery` and
  the factory copy, and sets `slot=<new> fallback=<old>`.
- If the new slot doesn't reach "boot ok" (30 s after start) within 3 boots,
  the initramfs returns to the old slot.
- Needs `admin` with passwordless sudo on the panel, which is the default.
- Over USB (`10.55.0.1`) it is about 5× faster than over Wi-Fi.

`tools/flash-rootfs.sh` is an older tool that writes a raw rootfs image to a
partition over USB. It is kept for development.

## 5. When things go wrong

| Situation | Way out |
|---|---|
| Dashboard hangs, touch still works | rescue menu: tap twice in every corner ([usage.md](usage.md)) |
| New release does not boot | automatic: back to the previous slot after 3 tries |
| No slot boots | automatic: after 3 failed boots, rescue mode → wait 2 min → reinstall from factory (settings kept) |
| Panel unreachable, but it boots | power-cycle 3× during the boot animation → same automatic path |
| You want a shell anyway | `px30-ctl rescue` (or from the menu/HA) → `ssh root@10.55.0.1` over USB |
| Boot partitions broken | Loader mode (BOOT button) → RKDevTool → write `px30-boot.img` to `boot`, or `px30-rescue.img` to `recovery` |

Sector addresses in the PX30 layout: boot `0x20000`, recovery `0x40000`.

To force the rescue image from Loader mode, write a `boot-recovery`
bootloader command into misc (misc + 16 KiB = sector `0x8020`). U-Boot then
starts `recovery` on every boot until rescue mode clears it (`bcb_clear`):

```sh
printf 'boot-recovery' | dd of=bcb-sector.bin bs=512 conv=sync   # 512 bytes, zero padded
```

## 6. Restoring Android

**Not tested end to end.** Two ways:

- **From rescue mode** (the system runs from RAM, so the eMMC is free):

  ```sh
  ssh root@10.55.0.1 'px30-rescue hold'
  cat emmc-full.img | ssh root@10.55.0.1 'dd of=/dev/mmcblk0 bs=4M conv=fsync'
  ```

  The first 8 MiB (idblock, GPT) are written back identically, so the
  bootloader does not change.
- **From Loader mode:** RKDevTool, *Write by Address* `0x0` ←
  `emmc-full.img` (7.3 GiB). Or write the partition images from the backup at
  their original addresses, after the GPT (sector 0, 34 sectors).

Either way, Android then boots with exactly the state of the backup.
