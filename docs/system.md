# System design

## eMMC layout

Written once by `px30-rescue repartition` (sectors of 512 bytes; eMMC is
7.3 GiB):

| Partition | Start | Size | Contents |
|---|---|---|---|
| (idblock) | 64 | — | Rockchip loader, **never touched** |
| uboot, trust, misc, resource | 16384 … 40960 | 4/4/4/16 MiB | vendor bootloader, **unchanged and at the same place** |
| security, smatek, keypart, STSN | 73728 … 115200 | 4 MiB / 16 MiB / 256 KiB / 256 KiB | vendor data, copied from the stock layout |
| boot | 131072 | 64 MiB | `px30-boot.img`: kernel + DTB + initramfs, `px30.mode=normal` |
| recovery | 262144 | 64 MiB | `px30-rescue.img`: same kernel, `px30.mode=rescue` |
| factory | 393216 | 1 GiB | ext4, golden copy: `rootfs.img.zst`, `boot.img`, `recovery.img`, `VERSION`, `SHA256SUMS` |
| data | 2490368 | 512 MiB | ext4, everything that must survive a reinstall (below) |
| rootfs_a, rootfs_b | 3538944, 9402368 | 2863 MiB each | Debian, A/B |

What lives on `/data`:

- `iwd/`: Wi-Fi profiles (bind mount on `/var/lib/iwd`);
- `thread/`: OTBR settings (bind mount on `/var/lib/thread`);
- `ssh/`: host keys (also used by rescue mode), `authorized_keys/admin`, and
  the optional password hash;
- `kiosk/`: `kiosk.conf` (dashboard URL) and `share/`, the WebKit profile with
  the HA login;
- `px30-agent/`: MQTT configuration and display settings;
- `px30-panel/`: setup certificate and `services.json`;
- `radio/`: the chosen radio stack and the Thread dataset backup;
- `machine-id`, `hostname`, `timezone`, `wifi-country` (the last three from the
  setup page; the initramfs writes `/etc/hostname`, `/etc/hosts`,
  `/etc/localtime` and the `cfg80211` country option from them at every boot).

## Images

All images are built from the same kernel:

- `px30-boot.img` and `px30-rescue.img` are Android boot images v0, because the
  vendor U-Boot only boots those from `boot`/`recovery`. The kernel goes in
  the kernel slot, the initramfs as the ramdisk, and the DTB in "second" as a
  Rockchip RSCE resource (`boot/make-recovery-img.py`). The command line comes
  from `kernel/cmdline-boot.txt` / `cmdline-rescue.txt`. U-Boot prepends its
  own `androidboot.*` arguments, which is harmless.
- `px30-rootfs.img.zst` is an ext4 image (1.8 GB). It is grown to the slot size
  offline by the initramfs on first boot (`e2fsck` + `resize2fs`), because the
  rootfs itself has no e2fsprogs.

## Boot flow

1. The vendor U-Boot starts `boot`. It would start `recovery` instead with a
   `boot-recovery` command in misc or the "recovery" reboot flag, and enter
   Loader mode with the BOOT button.
2. The initramfs (`initramfs/init`, about 1.3 s) reads the state record in misc
   at 1 MiB (`PX30S1 slot=a tries=… fallback=… action=…`, see
   `initramfs/lib/px30-state.sh`):
   - it increments `tries`;
   - after 3 failed boots it switches to the `fallback` slot if there is one, else
     to rescue mode ("boot-failed");
   - a pending `action` (rescue, reinstall, factory-reset) runs once.
3. It mounts `rootfs_<slot>` and `/data`, sets up the bind mounts, and runs `switch_root`.
4. `px30-boot-ok.timer` runs `px30-ctl boot-ok` 30 s after boot: `tries=0`,
   fallback cleared, admin's SSH keys copied to `/data`.
5. Plymouth shows the boot animation. `kiosk-start` takes over the screen
   without a black gap: `plymouth deactivate` → Cog opens a local loader page
   with the same animation → the dashboard as soon as Home Assistant responds.

## Rescue mode

Entered after 3 failed boots, with `px30-ctl rescue`, from the on-screen
menu, or from Home Assistant.

- Runs entirely from the initramfs: busybox, e2fsprogs, sfdisk, zstd and dropbear.
- USB-NCM gadget on the OTG port: the panel is `10.55.0.1` and serves DHCP. Log in
  with `ssh root@10.55.0.1`, **without a password** (USB only: dropbear listens
  on `10.55.0.1`, and there is no Wi-Fi in rescue mode). Keys work too: the one
  baked in at build time plus admin's keys from `/data`. The host key is the
  normal system's ed25519 key from `/data`, so the panel has one fingerprint in
  both modes.
- Status on screen (fbcon).
- "boot-failed": waits 2 minutes for `px30-rescue hold`, then reinstalls from the
  factory partition (data kept) and reboots, at most twice in a row.
  **Power-cycling 3 times during the boot animation** therefore also leads to
  automatic recovery.
- Commands: `px30-rescue status | hold | reboot | reinstall [--wipe-data] |
  write-rootfs a|b | write-part boot|recovery | put-factory NAME | repartition --yes`.

`px30-ctl` on the running system:

```
px30-ctl status          slot, boot counter, pending request
px30-ctl rescue          reboot into rescue mode
px30-ctl reinstall       reinstall from factory, keep settings
px30-ctl factory-reset   reinstall and wipe /data
```

## Services

| Unit | Role |
|---|---|
| `getty@tty1` → `kiosk-start` | autologin of user `kiosk`, runs Cog in a loop |
| `px30-panel` | root daemon: rescue-menu gesture, setup page, service choices |
| `proximity-wake` | owns the backlight: proximity/touch wake, auto brightness, presence |
| `px30-agent` | Home Assistant over MQTT (only starts with a configuration) |
| `otbr-agent` or `px30-zigbee-bridge` | the radio stack chosen with `px30-radio` (one at a time) |
| `px30-audio` | keeps the codec open with silence (prevents a pop at every new sound) |
| `ssh` | can be switched off from the setup page, HA or the menu |
| `avahi-daemon` | `<hostname>.local` and the Thread border router advertisement |
| `iwd`, `systemd-resolved`, `systemd-timesyncd`, zram | base system |

The systemd enable state lives on the rootfs and is replaced by every
update. The user's choices (`/data/px30-panel/services.json`,
`/data/radio/state.json`) are therefore applied again at every start by `px30-panel`.

## Users and privileges

- `root` is locked.
- `admin` logs in over SSH with a key (or a password, if one is chosen on the
  setup page) and has sudo. The keys live in three places: `~admin/.ssh`,
  `/data/ssh/authorized_keys/admin` (survives a reinstall) and
  `/etc/ssh/failsafe_keys/admin` (in the image, only with `SSH_PUBKEY` at build time).
- `kiosk` runs Cog. Its only sudo rule is for `plymouth`.
- `px30-agent` runs the MQTT agent. Its sudo rules allow exact command lines
  only (`agent/30-px30-agent.sudoers`), so no MQTT payload reaches a root command.
- The local API of `px30-panel` (`127.0.0.1:8088`) needs a per-session token
  and checks Host/Origin. A web page in the kiosk can't call it.
