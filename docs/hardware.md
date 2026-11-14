# Hardware

Everything below was found on one panel: board `ST-PX30-T6E` (2021), stock
firmware Android 8.1 `userdebug`, kernel 4.4.138. The data comes from a
read-only audit (`audit_v2.sh`: live device tree, dmesg, partition table) and
from the PCB.

## Overview

| Part | Details | Mainline status |
|---|---|---|
| SoC | Rockchip PX30, 4× Cortex-A35 | `px30.dtsi` |
| RAM | 1 GB DDR3 (977 MB usable) | |
| eMMC | Samsung 8GTF4R, 7.3 GiB + boot0/boot1 + RPMB | `dw_mmc-rockchip`, HS200 |
| GPU | Mali-G31 | Panfrost (works, DVFS 200–480 MHz) |
| Display | 480×480, 72×70 mm, ST7701(S) on MIPI-DSI (2 lanes, RGB888) | `panel-sitronix-st7701` + patch |
| Backlight | PWM1, 25 µs period | `pwm-backlight` |
| Touch | Hynitron CST226SE, I2C1 0x5a, IRQ GPIO0_B5 (gpio0-13), reset GPIO0_B4 | `hynitron_cstxxx` + patch |
| PMIC | Rockchip RK809 on I2C0 0x20 (regulators, RTC, codec, clkout) | `rk808` / `rk817` codec |
| Audio | RK809 codec, one speaker; codec MCLK on GPIO2_C3 (`i2s1_2ch_mclk`) | works (`Playback Mux` = SPK) |
| Microphone | probably a PDM microphone (Android enables `pdm@ff0a0000`) | not set up |
| Proximity / light | Sensortek STK3x1x, I2C2 0x47, IRQ GPIO3_C0 (polled) | `stk3310` |
| Wi-Fi + BT | RTL8723DS: Wi-Fi on SDIO, BT on UART1 (H5) | Wi-Fi: `rtw88_8723ds`; BT: not working |
| Zigbee / Thread | Tuya TYZS13 (EFR32MG13P732) on UART5 with RTS/CTS | see [radio.md](radio.md) |
| Buttons | `adc-keys` (ESC, HOME, VOL+/−, MENU) in the vendor DT | not wired up / unknown |
| USB | micro-USB OTG port (dwc2): adb/Loader on Android, USB-NCM gadget on Debian | works (needs the clock fix) |
| Not populated | Ethernet PHY, SD slot, camera, USB host port | — |
| Unknown | MBI5041 16-channel LED driver (bit-banged GPIO3), I2C2 0x3c "tp" device, UART4 (RS485 on other variants), "T&H" connector | |

## PCB

- **BOOT button** next to the shield: hold it while powering on → Rockchip
  Loader mode (U-Boot Rockusb). This is the recovery channel.
- **Micro-USB "OTG"** at the bottom right: the PC connection. Power the panel
  through its normal supply, and use a data cable straight to the PC. A
  charge-only cable makes the PHY see a USB charger.
- **TYZS13 module** with pads above it: those are **UART5** (PX30 side). Use
  them as a serial console at up to 921600 baud: GND from the IO header, no 5 V.
  The UART2 debug pads were not found.
- Connectors: "TP" (touch flex), "T&H" (temperature/humidity sensor, disabled in
  the vendor DT), "SPK", and the header `IO1/IO2/NC/GND` (relay variant).

## Display

From the vendor DT:

- 2 lanes, RGB888, continuous HS clock (`dsi,flags` 0x0803);
- pixel clock 16 MHz;
- H 480/12/10/15 and V 480/12/8/15 (active/front porch/sync/back porch), ≈ 60 Hz;
- reset GPIO3_A0, supply `vcc3v3_lcd`.

The init sequence is close to the Winstar WF40ESWAA6MNN0, which is already in
mainline. The differences are E0, EB, gamma, Vcom and the porch/inversion/VGH
registers. Patch 0001 adds the exact sequence as its own compatible.

In the DT the reset must be `GPIO_ACTIVE_HIGH`: the ST7701 driver inverts it
itself.

## Proximity sensor

- Android polls every 200 ms. The IRQ is not used.
- Baseline without a hand: ~190 counts.
- Ambient light: the mainline driver defaults to the shortest integration
  time, which reads 0 indoors. `proximity-wake` sets
  `in_illuminance_integration_time=0.094720` and `in_illuminance_scale=0.1`,
  which gives plausible lux values (≈150 lx in a lit room).

## Stock eMMC layout (Android)

| # | Name | Size | Notes |
|---|---|---|---|
| p1 | uboot | 4 MiB | at sector 0x4000 |
| p2 | trust | 4 MiB | |
| p3 | misc | 4 MiB | |
| p4 | resource | 16 MiB | RSCE: DTB and logos |
| p5 | kernel | 32 MiB | |
| p6 | boot | 32 MiB | |
| p7 | recovery | 64 MiB | sector 0x32000 |
| p8 | backup | 112 MiB | |
| p9 | security | 4 MiB | |
| p10 | cache | 384 MiB | |
| p11 | system | 1.5 GiB | |
| p12 | metadata | 16 MiB | |
| p13 | vendor | 384 MiB | |
| p14 | oem | 256 MiB | boot animation, factory test app |
| p15 | frp | 512 KiB | |
| p16 | smatek | 16 MiB | vendor specific, kept |
| p17 | keypart | 256 KiB | vendor specific, kept |
| p18 | STSN | 256 KiB | vendor specific (serial number?), kept |
| p19 | stback | 1 GiB | factory update package (`update.zip`) |
| p20 | userdata | ~3.5 GiB | |

The vendor U-Boot runs `boot_android` → `bootrkp` → distro boot. It boots
Android boot images v0 from `boot`/`recovery` (page 2048, DTB as RSCE in
"second") and does not verify signatures.
