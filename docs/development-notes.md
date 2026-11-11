# Development notes

The problems that took the most time, how they were solved, and what is still
open. This is useful if you port this to a similar board or continue on Bluetooth.

## Display

A black screen with the backlight on had four separate causes:

1. **Reset polarity.** `panel-sitronix-st7701` inverts the reset GPIO itself.
   With `GPIO_ACTIVE_LOW`, copied from Android's simple-panel, the panel stayed in
   reset. The DT now has `GPIO_ACTIVE_HIGH`.
2. **No GLES.** WPEWebProcess crashed on a missing `libGLESv2.so.2` (package
   `libgles2`).
3. **No HS video after a modeset.** The panel answered LP commands (DCS 0x0A =
   0x9C), but showed nothing, even with a CPU test pattern or the DSI host's own
   pattern generator. All DSI/D-PHY registers were identical between the black
   and a working state. One restart of the DSI host (PWR_UP 0 → 1) while the VOP
   was already sending video always gave a picture. Patch 0002:
   - powers the PHY on before `dphy_enable` (BSP order);
   - restarts the host two frames after switching to video mode;
   - restarts it once more 300 ms after `atomic_enable`, so after the panel enable
     and the first frames.

   Patch 0001 sends DISPLAY_ON in `prepare()` (as the Android init does) and skips
   the extra DISPLAY_ON that mainline sends in `enable()`. That LP command
   landed in the middle of the fresh video stream and caused a race: about one
   boot in three was black.
4. **Noise in Cog.** Cog's default "modeset" renderer scans out WebKit's tiled
   GPU buffers without a modifier. `--platform-params=renderer=gles` fixes it.

Smaller ones:

- `COG_PLATFORM_DRM_CURSOR=0` *enables* the cursor (Cog only checks whether
  the variable exists). The variable is unset now.
- A non-root Cog only gets DRM master if nobody holds it when it opens the
  device. `kiosk-start` waits until the previous Cog and its WPE processes are
  gone.
- Stop Cog with SIGKILL: on SIGTERM it aborts in its EGL teardown (a core dump
  each time) and keeps DRM master for seconds.
- Handover from the boot splash: `plymouth quit --retain-splash` removes
  plymouth's framebuffer, and the kernel then disables the plane (black). Now
  `plymouth deactivate` releases DRM master, the last frame stays, and Cog shows
  a local loader page with the same animation until Home Assistant answers.
- The boot hung now and then on tty1: systemd 257 asks the terminal size with an
  ANSI query, and plymouthd sometimes ate the answer. Fixed size
  `TTYRows=30`/`TTYColumns=60` in the getty drop-in.

## USB gadget

Windows reported "Device Descriptor Request Failed". The vendor U-Boot
leaves `aclk_peri_src` on CPLL, which mainline only knows as `dummy_cpll`. The
peri clock branch became an orphan, and `assigned-clock-rates` then picked the
largest divider: the peri bus HCLK ran at **3 MHz**, while USB 2.0 needs ≥ 30 MHz AHB.
The DT now re-parents `ACLK_PERI_SRC` to GPLL first (`&cru`), which gives the same
`CLKSEL_CON14` as Android. That also speeds up everything else on the peri bus.

Also:

- The EHCI/OHCI host controllers hang the bus at probe (Android gives them two
  clocks, mainline one). There is no host port, so they are disabled.
- When the panel is powered from a PC USB port, long transfers *from* the panel
  (hundreds of MB) make it brown out. Uploads are fine. All tools only upload.

## Touch

- The CST226SE speaks the cst3xx protocol but does not answer the cst3xx
  bootloader handshake (0xA002). Patch 0004 adds a variant with a plain reset.
- The axes are swapped: `touchscreen-swapped-x-y`.
- The IRQ must be edge-triggered (`IRQ_TYPE_EDGE_FALLING`). With level-low the
  chip keeps INT low briefly after a report, and the driver logged ~30 read
  failures per tap.

## Wi-Fi

`rtw88_8723ds` works. iwd needs the kernel crypto options (AF_ALG and
friends), and regulatory needs `wireless-regdb`.

## Audio

The amplifier distorts the start of every new stream (a pop or crack).
`px30-audio.service` keeps the codec open with a silent dmix stream, and
`/etc/asound.conf` routes `default` through plug → mono → dmix. The cost is about
3–4 % CPU, and it can be switched off as a service.

## Rescue/initramfs pitfalls

- busybox ash prefers its own applets over `PATH`. Use `/usr/sbin/mke2fs`
  explicitly; there is no `mountpoint` applet.
- Scripts edited on Windows can get CRLF line endings. The shebang then fails
  with "not found".
- `dwc2.rk_gadget_dma=0` (PIO) sends nothing. 2 (descriptor DMA) is no better
  than 1 (buffer DMA, default).
- `reboot loader` is ignored by U-Boot while misc holds `boot-recovery`.
- The systemd enable state lives on the rootfs, so an update resets it. User
  choices are therefore kept on `/data` and applied at start.
- An image without a baked-in SSH key could lock you out after an update. Admin's
  keys are now copied to `/data` 30 s after each good boot, and `sshd` reads them
  from there.

## Radio module

- The Gecko bootloader uses no flow control; the RCP/NCP do.
- GSDK 4.4.6 has `OPENTHREAD_CONFIG_PLATFORM_BOOTLOADER_MODE_ENABLE` = 0 by
  default. The first RCP build could therefore not return to the bootloader,
  which is why a one-time SWD flash is needed now.
- Bootloader entry through GPIO (PA1/PF4 at reset) was tried in all four
  combinations. The bootloader did not answer, so SWD is the only way in.

## Bluetooth

**Status: not working, low priority.**

Setup: `hci_h5` (serdev) on UART1 (`ff158000`), firmware `rtl_bt/rtl8723ds_fw.bin`
plus a config with a UART entry (0x000c) added.

Found and fixed:

- The chip's **32 kHz LPO clock was missing**. Android feeds it from RK809
  clkout2 (`ext_clock = <&rk809 1>`). Mainline left it off (reg `0xf2` bit 7). The
  DT now has `#clock-cells = <1>` on the RK809 and the clock in `sdio-pwrseq`.
  Wi-Fi still works.

Still failing:

- At 1.5 Mbaud the first download stalls on fragment 0 after the baud change.
- At 115200 the download completes and every fragment is acked, including the
  last. After that there is no command complete. The chip does come back with
  H5 SYNC packets and the link re-syncs, but every HCI command gets a bare ack
  and never an event. In the ROM state, before the download, the same probe works
  (`Read Local Version` → lmp_subver 8723).
- The same happens with the vendor tool `rtk_hciattach` (`tools/rtk_hciattach/`,
  run on a freed `/dev/ttyS1` using `tools/btuart-free/`). So this is not a
  mainline driver problem.
- No effect from: Android's or linux-firmware's firmware file, baud rates 115200
  to 1.5M, with or without flow control, BT-wake high or low, or patch 0005
  (H5 sequence resync, refuted: nothing arrives to resync on).
- Ruled out: the UART1 clock (exactly 24 MHz) and the 32k clock (enabled).

Still to separate:

1. Does the 32 kHz clock actually reach the module? This needs a measurement.
2. Does rtw88 interfere? Test without ever loading it, over USB.
3. Did Bluetooth ever work under Android on this board? The audit only shows the
   BT HAL started.

## Ideas / not done

- Hotspot provisioning: Wi-Fi QR → captive portal → choose a network → the
  phone follows the panel to `<hostname>.local`, with a QR code showing the new IP
  on screen as a fallback.
- Signed system updates from GitHub releases (`px30-update`).
- Microphone (PDM) and a voice satellite.
- Device tree aliases for stable serial names.
