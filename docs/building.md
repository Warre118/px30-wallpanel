# Building

Everything builds on an x86-64 Linux machine. WSL2 with Debian 13 is what the
project was developed on. All builds are rootless, and no build script writes to
the panel. Output goes to `~/px30-build/` by default.

## Prerequisites

```sh
sudo apt install git build-essential gcc-aarch64-linux-gnu bc bison flex libssl-dev libelf-dev \
  mmdebstrap uidmap qemu-user-static binfmt-support e2fsprogs python3 python3-pil
```

## 1. Kernel

Mainline Linux **7.3.4** plus patches from `kernel/`:

| Patch | Why |
|---|---|
| `0001-drm-panel-st7701-add-smatek-px30-wallpanel` | panel init sequence and timings from the vendor DT; DISPLAY_ON in `prepare()` |
| `0002-drm-bridge-dw-mipi-dsi-power-on-phy-before-dphy-enable` | the DSI host only gives video after a restart once video mode runs (see [development-notes.md](development-notes.md#display)) |
| `0003-usb-dwc2-rockchip-gadget-dma-mode-param` | `dwc2.rk_gadget_dma=` parameter (default buffer DMA) |
| `0004-input-hynitron_cstxxx-add-cst226se` | CST226SE touch controller without the cst3xx bootloader handshake |
| `0005-bluetooth-hci_h5-rtl-resync-seq-after-fw-download` | experiment, **not applied** (did not help) |

```sh
git clone --depth 1 -b v7.3.4 \
  https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git ~/px30-build/linux
bash kernel/build-kernel.sh all       # prepare (patches, DTS, config) + build
```

- Config: `defconfig` + `kernel/px30-wallpanel.config` + `kernel/px30-trim.config`.
  The trim fragment drops other SoCs, PCI, ACPI, virtualisation, media and so on. The
  result is a 22 MB `Image` and 36 MB of modules, built in about 12 minutes.
- Device tree: `dts/px30-smatek-wallpanel.dts`, built in-tree.
  `dts/build-dtb.sh` builds it stand-alone against a kernel tree.
- Output: `~/px30-build/out/{boot/Image, boot/*.dtb, lib/modules}`.

## 2. Initramfs

```sh
SSH_PUBKEY=~/.ssh/id_ed25519.pub bash initramfs/build-initramfs.sh
```

- Boot counter, A/B selection, `/data`, and rescue mode with dropbear. About 5 MB.
- `SSH_PUBKEY` is optional. It adds a key for `root` in rescue mode. Without it,
  rescue mode only accepts admin's keys from `/data`, which are set via the setup
  page. **For a first install you need it**, because there is no `/data` yet.

## 3. Root file system

```sh
SSH_PUBKEY=~/.ssh/id_ed25519.pub WIFI_SSID='…' WIFI_PSK='…' bash rootfs/build-rootfs.sh
```

Builds Debian 13 arm64 with `mmdebstrap --mode=unshare` and the overlay in
`rootfs/overlay/`. It writes `~/px30-build/rootfs/px30-rootfs.ext4` (1.8 GB,
grown on the panel). It takes about 7–10 minutes.

| Variable | Default | |
|---|---|---|
| `SSH_PUBKEY` | — | key for `admin`, also put in `/etc/ssh/failsafe_keys`. Optional: SSH can be set up from the setup page |
| `WIFI_SSID`, `WIFI_PSK` | — | baked-in Wi-Fi profile; copied to `/data` on first boot. **Never** put images with Wi-Fi in public |
| `IWD_DIR` | — | alternative: a directory with ready iwd `.psk` profiles (`tools/android-wifi-to-iwd.py` converts Android's Wi-Fi store) |
| `KERNEL_OUT` | `~/px30-build/out` | kernel build output |
| `RADIO_DIST` | `~/px30-build/zigbee/dist` | radio module firmware ([radio.md](radio.md)) |
| `OTBR_DIST` | `~/px30-build/zigbee/otbr-dist` | `otbr-agent` + `ot-ctl` (`zigbee/build-otbr.sh`); without it the image has no Thread |
| `IMG_SIZE` | `1800M` | image size |

What is in the image:

- systemd, iwd, systemd-resolved/timesyncd, zram (half the RAM, zstd), and a
  volatile journal;
- Cog 0.18 + WPE WebKit 2.48 on DRM, plymouth, avahi, openssh;
- `python3-paho-mqtt`, `python3-qrcode`, and universal-silabs-flasher in `/opt/usf`;
- the overlay: kiosk, `px30-panel`, `proximity-wake`, `px30-agent`,
  `px30-radio`, `px30-ctl`, udev rules, and so on.

The script normalises owners and permissions. It stops if any file outside
`/home` belongs to a regular user, and runs `visudo -c` on the sudoers files.
No SSH host keys and no machine-id are baked in.

### Boot animation

The original boot animation is vendor artwork and is not in this repository.
Import it from your own panel before building the rootfs:

```sh
adb pull /oem/media/bootanimation.zip      # while still on Android
python3 tools/import-bootanim.py bootanimation.zip
```

Without it, the panel boots with a black screen until the dashboard appears.

### Bluetooth firmware

Bluetooth does not work yet. For experiments, put `rtl8723ds_fw.bin` and
`rtl8723ds_config.bin` in `rootfs/overlay/usr/lib/firmware/rtl_bt/`. The
firmware is Android's `/vendor/etc/firmware/rtl8723d_fw` or linux-firmware's
`rtl8723d_fw.bin`. The config needs a UART entry, see
[development-notes.md](development-notes.md#bluetooth).

## 4. Release

```sh
PX30_OUT=/mnt/c/PX30 bash tools/make-release.sh 2026.11.11-1
```

Creates `$PX30_OUT/release-<version>/` (default `~/px30-out`) with:

- `px30-boot.img`: kernel + DTB + initramfs, `px30.mode=normal`;
- `px30-rescue.img`: the same, `px30.mode=rescue`;
- `px30-rootfs.img.zst`;
- `VERSION` and `SHA256SUMS`.

The boot images are Android boot images v0 with the DTB in a Rockchip resource
(`boot/make-recovery-img.py`), because that is what the vendor U-Boot boots.

Install with `tools/px30-install.sh` (first time) or `tools/px30-ota.sh`
(updates). See [flashing.md](flashing.md).

## Other tools

| Tool | |
|---|---|
| `tools/serial-log.py` | log a serial console (the pads above the Zigbee module are UART5) |
| `tools/touchlog.py` | print touch coordinates (for orientation) |
| `tools/kmstest.py`, `tools/fbshot.py` | KMS test pattern / framebuffer screenshot |
| `tools/regdump.c`, `tools/regwrite.c`, `tools/memw.py`, `tools/devmem.py` | register access for debugging (static arm64 builds) |
| `tools/dsi*-live.sh`, `tools/dphy-android-timing.sh`, `tools/dispdump.sh`, `tools/usbdump*.sh` | register dumps used while bringing up display and USB |
| `tools/gen-panel-init.py` | turns the vendor DT panel init sequence into C |
| `tools/ezsp-probe.c` | asks a Silicon Labs NCP for its EZSP/EmberZNet version |
| `tools/rtk_hciattach/`, `tools/btuart-free/` | Bluetooth experiments |
| `boot/dtb-uart5-noflow.py` | debug DTB with a kernel console on UART5 |

## Tests

Parts of the panel software can be tested on a PC without the hardware:

```bash
python3 -m unittest discover -s tests -v     # Wi-Fi hotspot logic (fake iwd), Windows or Linux
python3 tests/smoke_panel_hotspot.py         # px30-panel + hotspot dry run (Linux/WSL, needs openssl)
```
