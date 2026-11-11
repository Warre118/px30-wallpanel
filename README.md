# PX30 Wallpanel: Debian kiosk for the Smatek ST-PX30-T6E

Replaces the Android 8.1 firmware of the Smatek PX30 wall panel (board
`ST-PX30-T6E`, Rockchip PX30, 1 GB RAM, 4" 480×480 touch screen, Tuya TYZS13
Zigbee module) with a minimal **Debian 13 (trixie) arm64** system that does one
thing: show a Home Assistant dashboard full screen.

- Mainline Linux 7.3 with a few small patches. No vendor BSP kernel.
- Cog / WPE WebKit directly on DRM (no X11, no Wayland compositor).
- A/B root file systems, a golden factory copy, automatic fallback, and a rescue
  mode reachable over USB.
- Set up from your phone: scan a QR code, log in to Home Assistant, done. The
  panel never stores your HA password.
- Home Assistant device over MQTT discovery: screen, brightness, presence,
  ambient light, reboot/rescue/reinstall buttons.
- The Zigbee module can be reflashed as an OpenThread RCP: the panel then runs
  its own Thread border router (OTBR).
- Hidden on-screen rescue menu: tap twice in each corner.

> **Warning.** This replaces the firmware on the panel's eMMC. Make the backup
> described below first and keep it safe: it is your only way back to Android.
> You do all of this at your own risk.

## Status

Tested on one panel (ST-PX30-T6E, 2021 board). Other Smatek PX30 variants
(relay, RS485 or other displays) will need device tree changes.

| Works | |
|---|---|
| Display | ST7701 480×480 MIPI-DSI, Mali-G31 (Panfrost) GPU rendering |
| Touch | Hynitron CST226SE (small driver patch) |
| Backlight | PWM, auto-off after inactivity, wake on proximity or touch |
| Sensors | Sensortek STK3x1x proximity + ambient light (mainline `stk3310`) |
| Wi-Fi | RTL8723DS (rtw88); the only network interface |
| Audio | speaker via the RK809 codec |
| Thread | TYZS13 module as OpenThread RCP + otbr-agent on the panel |
| USB | CDC-NCM gadget on the micro-USB OTG port: `ssh admin@10.55.0.1` |
| System | A/B updates over Wi-Fi, fallback after 3 failed boots, rescue mode, reinstall, factory reset |
| Setup | phone setup page (HA login incl. 2FA, MQTT, SSH key or password, services) |

| Does not work / untested | |
|---|---|
| Bluetooth | **does not work.** The firmware download completes, but the chip's HCI layer hangs afterwards. See [docs/development-notes.md](docs/development-notes.md#bluetooth). |
| Microphone | not set up (PDM microphone, probably present) |
| Zigbee | the EmberZNet NCP firmware builds, but the switch between Thread and Zigbee (`px30-radio`) is **untested on hardware** |
| Radio module | first flash of the bootloader-capable RCP needs **SWD** (a cheap debug probe); see [docs/radio.md](docs/radio.md) |
| Updates from GitHub | `px30-update` is a stub; updates are pushed from a PC (`tools/px30-ota.sh`) |
| Wi-Fi setup hotspot | **built, untested on hardware**: without a working Wi-Fi network the panel opens `PX30-xxxx` and a QR flow for the phone ([docs/usage.md](docs/usage.md#wi-fi-setup-hotspot)); until tested, set Wi-Fi at build time or over USB |
| Fresh install | the full install path from stock Android is described below, but was done step by step on the development panel, not end to end with this exact script set |
| Other | buttons (adc-keys), the MBI5041 LED driver, the T&H connector and the second UART are not used |

## Quick start

You need a Linux build machine (WSL2 Debian works), Windows for RKDevTool, a
micro-USB **data** cable and a separate power supply for the panel.

### 1. Back up the stock firmware (do not skip)

The stock firmware is an Android `userdebug` build, so `adb root` works. Use
adb over the network (port 5555, enabled on the tested panel) or over the OTG
port:

```sh
adb connect <panel-ip>:5555
bash backup_emmc.sh <panel-ip>:5555 ./PX30-backup/emmc-$(date +%Y%m%d)
python3 verify_backup.py ./PX30-backup/emmc-<date>
```

- `backup_emmc.sh` only **reads** from the panel. It copies the whole eMMC in
  256 MiB blocks, each hashed on the panel and on the PC, and resumes when
  restarted. That takes about 40 minutes over Wi-Fi and gives a 7.3 GiB `emmc-full.img`.
- It also saves every partition separately. Keep two copies on different
  disks. The vendor partitions `smatek`, `keypart`, `STSN` and `stback` are
  device specific.
- Also copy `/oem/media/bootanimation.zip` if you want the original boot
  animation (see [Building](docs/building.md#boot-animation)).

Then check that you can reach the **Loader** mode: hold the **BOOT** button
next to the shield while plugging in power. RKDevTool (Windows) must show
"LOADER". This is the recovery channel if anything goes wrong; see
[docs/flashing.md](docs/flashing.md).

### 2. Build

See [docs/building.md](docs/building.md). In short (WSL):

```sh
bash kernel/build-kernel.sh all
SSH_PUBKEY=~/.ssh/id_ed25519.pub bash initramfs/build-initramfs.sh
SSH_PUBKEY=~/.ssh/id_ed25519.pub WIFI_SSID='...' WIFI_PSK='...' bash rootfs/build-rootfs.sh
bash tools/make-release.sh 2026.11.11-1
```

For the very first install, bake in your SSH key and your Wi-Fi. The panel
has no data partition yet, so the setup page can't provide them. Keep these images private.

### 3. Flash

1. Write `px30-rescue.img` to the Android `recovery` partition, with RKDevTool
   in Loader mode or with `adb root` + `dd`. See
   [docs/flashing.md](docs/flashing.md#first-install). `uboot`, `trust` and
   the idblock are **never** touched.
2. `adb reboot recovery`: the panel starts the PX30 rescue mode and shows
   "rescue mode" on screen. Connect the OTG port to the PC; the panel is now
   `10.55.0.1`.
3. Install and repartition (Git Bash or Linux):

   ```sh
   bash tools/px30-install.sh <release-dir> --repartition
   ```

   This replaces the Android partitions with the PX30 layout (boot, recovery,
   factory, data, rootfs_a, rootfs_b). The vendor partitions are copied over
   unchanged, and the bootloader stays where it is. Then reboot with
   `ssh root@10.55.0.1 px30-rescue reboot`.
4. The panel boots Debian. If you didn't give it a Home Assistant URL,
   it shows a QR code: scan it with your phone and follow
   [docs/usage.md](docs/usage.md).

### Later updates

```sh
bash tools/px30-ota.sh <release-dir> <panel-ip> --reboot
```

This writes the new release to the inactive slot. If it doesn't boot 3 times,
the panel returns to the previous slot by itself.

### Back to Android

Restore the backup with RKDevTool in Loader mode. See
[docs/flashing.md](docs/flashing.md#restoring-android).

## Documentation

| | |
|---|---|
| [docs/usage.md](docs/usage.md) | first setup with the phone, rescue menu, SSH, services |
| [docs/home-assistant.md](docs/home-assistant.md) | MQTT agent, entities, security model |
| [docs/building.md](docs/building.md) | building kernel, initramfs, rootfs and releases |
| [docs/flashing.md](docs/flashing.md) | backup, Loader mode, first install, OTA, restoring Android |
| [docs/system.md](docs/system.md) | eMMC layout, boot flow, A/B, rescue mode, recovery paths |
| [docs/radio.md](docs/radio.md) | Zigbee/Thread module: firmware, SWD, OTBR, switching stacks |
| [docs/hardware.md](docs/hardware.md) | hardware details found during the port |
| [docs/efficiency.md](docs/efficiency.md) | resource use measured on the panel, tuning done and still to try |
| [docs/development-notes.md](docs/development-notes.md) | how the hard problems were solved, open issues |

## License

Not chosen yet. The kernel patches and the device tree are derived from
Linux and fall under the GPL-2.0. Vendor artwork and firmware are **not**
included: you take them from your own panel.

---

*Developed with the help of [Claude](https://claude.ai) (Anthropic), an AI
assistant. All code was reviewed and tested on a real panel.*
