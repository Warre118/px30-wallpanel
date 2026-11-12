# Zigbee / Thread radio module

## The module

- **Tuya TYZS13** = Silicon Labs **EFR32MG13P732F512GM48** (512 KB flash,
  64 KB RAM), with a 38.4 MHz HFXO, no LFXO (LFRCO is used), and DC-DC on.
- Connected to PX30 **UART5** (`ff178000.serial`) with RTS/CTS. A udev rule
  makes it `/dev/ttyThread`.
- Pinout on the module: USART0 TX PA0 (LOC0), RX PA1 (LOC0), CTS PF4 (LOC24),
  RTS PA2 (LOC29).
- Stock firmware: EmberZNet 6.5.0.0 (EZSP v7), with Gecko Bootloader 1.8.0 in the
  separate bootloader flash.
- The pads above the module on the PCB are UART5. They are handy as a serial
  console for debugging (`boot/dtb-uart5-noflow.py`).

## Firmware builds

Built in WSL with Gecko SDK 4.4.6, slc-cli, Arm GCC 12.2 and Simplicity
Commander (`zigbee/fetch-toolchain.sh`):

| Script | Result |
|---|---|
| `zigbee/gen-ot-rcp.sh` + `zigbee/build-ot-rcp.sh` | OpenThread RCP, 115200 baud, hardware flow control, **with return to the bootloader** (`-btl`) |
| `zigbee/gen-ncp.sh` + `zigbee/build-ncp.sh` | EmberZNet 7.5.0.0 NCP (EZSP v13), same pinout |
| `zigbee/gen-router.sh` + `zigbee/build-router.sh` | stand-alone Zigbee router (experimental), same pinout, **with `bootloader reboot` on its CLI** |
| `zigbee/build-otbr.sh` | `otbr-agent` + `ot-ctl` for arm64, from the same OpenThread commit as the RCP |

Details:

- `patch-ot-rcp.py` and `patch-ncp.py` set the pinout and clocks. They enable
  `OPENTHREAD_CONFIG_PLATFORM_BOOTLOADER_MODE_ENABLE`, which is 0 by default in
  GSDK 4.4.6. Without it, the RCP ignores the Spinel "reset to bootloader" command.
- The build scripts check in the disassembly that the bootloader return is there.
- The router (`zigbee/router/`) is the SDK's ZigbeeMinimal sample with a CLI on
  the UART (prompt `tyzs13_router>`) and Nabu Casa's router bootloader commands
  ported to GSDK 4.x (`router_bootloader_cli.c`): `bootloader info` and
  `bootloader reboot`. `universal-silabs-flasher` recognises it with the CLI
  `version` command and uses `bootloader reboot`, so a router can be updated or
  replaced without SWD. `app.c` joins an open network by itself (network
  steering at start and every 30 s while it has none). `build-router.sh` checks
  in the disassembly and the CLI table that `bootloader reboot` calls
  `bootloader_rebootAndInstall`. Built and checked; not yet tested on the panel.
- NVM3 sits at the same place (0x77000–0x7FFFF) in both images, so switching
  stacks keeps both stacks' keys.
- The firmware lands in `${PX30_OUT:-~/px30-out}/zigbee/` and, for the image,
  in `RADIO_DIST`. `rootfs/build-rootfs.sh` puts it in
  `/usr/share/px30-radio/firmware/` with a `catalog.json`.
- The scripts take their paths from the environment (`T`, `SDK`, `GCC_DIR`,
  `OUT`, `DIST`, `GSDK_LABEL`, `SLC_TOOL_PATH`, `MKGBL`; the defaults are the
  WSL setup above) and work with slc-cli 5 and 6. That is how the update
  pipeline builds them in a container for other Gecko SDK versions:
  [updater/README.md](../updater/README.md). Gecko SDK 4.5.1 (EmberZNet
  7.5.2.0) still supports the MG13.

## First flash: SWD (once)

The stock EmberZNet firmware cannot be replaced over UART reliably, and a
first RCP build without bootloader return can't either. The first flash of the
`-btl` RCP is done with a debug probe over SWD. After that, every switch goes
over UART.

**Wiring.** The TYZS13 top row of pads, as mounted with the I-PEX connector at
the bottom right, from left to right: 19 SWDIO, 18 SWCLK, 17 RX, 16 TX, 15 nRST.

| Module pin | Signal | Pico debugprobe | J-Link |
|---|---|---|---|
| 19 | SWDIO | GP3 | SWDIO (pin 7) |
| 18 | SWCLK | GP2 | SWCLK (pin 9) |
| 15 | nRST | optional | RESET (pin 15) |
| 28 | GND | GND | GND |
| 29/30 | 3V3 | **do not connect** | VTref (pin 1) only |

- The panel stays powered and feeds the module.
- Stop `otbr-agent` first.
- Start at 1 MHz.

**Memory map (xG13):**

| Region | Address | Notes |
|---|---|---|
| main flash | 0x0–0x7FFFF | the application starts at 0x0; NVM3 at 0x77000 |
| user data | 0x0FE00000 | |
| lock bits | 0x0FE04000 | |
| bootloader flash | 0x0FE10000–0x0FE13FFF | Gecko Bootloader. **Never erase it**: without it there is no UART switching. Do not use mass erase or a page erase of `@bootloader` |

**OpenOCD with CMSIS-DAP.** OpenOCD 0.12 knows the chip ("EFR32MG13P Mighty"):

```sh
ocd() { openocd -f interface/cmsis-dap.cfg -c 'transport select swd' -c 'adapter speed 1000' -f target/efm32.cfg "$@"; }
ocd -c init -c 'reset halt' -c 'flash info 0' -c shutdown                 # identify
ocd -c init -c 'reset halt' \
  -c 'dump_image tyzs13-main-512k.bin 0x00000000 0x80000' \
  -c 'dump_image tyzs13-bootloader-16k.bin 0x0FE10000 0x4000' -c shutdown  # backup first
ocd -c "program ot-rcp-tyzs13-gsdk4.4.6-115200-hwflow-btl.hex verify reset exit"
ocd -c init -c 'reset halt' -c 'dump_image bl-after.bin 0x0FE10000 0x4000' -c shutdown
cmp bl-after.bin tyzs13-bootloader-16k.bin && echo bootloader intact
```

**Simplicity Commander with J-Link:**

```sh
D="--device EFR32MG13P732F512GM48 --speed 1000"
commander readmem $D --range 0x00000000:0x00080000 --outfile tyzs13-main-512k.bin
commander readmem $D --range 0x0FE10000:0x0FE14000 --outfile tyzs13-bootloader-16k.bin
commander flash  $D ot-rcp-tyzs13-gsdk4.4.6-115200-hwflow-btl.s37
commander verify $D ot-rcp-tyzs13-gsdk4.4.6-115200-hwflow-btl.s37
commander device reset $D
```

If reading fails (all 0xFF), the chip may be debug-locked. Unlocking erases
main flash and tokens. Check afterwards that the bootloader is still there.

## Thread: OpenThread Border Router on the panel

- `otbr-agent` runs on the panel itself. Spinel over Wi-Fi to a remote host
  is unreliable.
- Config in `/etc/default/otbr-agent`: `-I wpan0 -B wlan0`, radio
  `spinel+hdlc+uart:///dev/ttyThread?uart-baudrate=115200&uart-flow-control`.
- Built with avahi, border routing, SRP advertising proxy, DNS-SD discovery
  proxy and REST. Without D-Bus, web UI, TREL and NAT64.
- State lives in `/data/thread` and survives updates.
- **Home Assistant:** add the "OpenThread Border Router" integration with URL
  `http://<panel-ip>:8081`. HA then forms or joins the Thread network.
- If HA (or the Matter server) runs in Docker, its host must accept route
  information from router advertisements: `net.ipv6.conf.<if>.accept_ra=1` and
  `accept_ra_rt_info_max_plen=64`. The Matter container needs `network_mode: host`.
- **The OTBR REST API has no authentication** and is reachable on the LAN.

## Switching stacks: `px30-radio`

Tested on the hardware: `status`, `probe`, `use thread` and `use off`.
Flashing (`switch`, `use --flash`) failed with the old flasher arguments; it now
uses the `universal-silabs-flasher` 1.3 command line (`--probe-methods`,
`flash --firmware`) and has to be tested on the hardware again.

```
px30-radio status                         stack, channel, firmware, service
px30-radio probe                          ask the chip which firmware it runs (~11 s)
px30-radio use thread|zigbee|router|off [--flash] [--yes]
                                          choose the stack (flashes only with --flash)
px30-radio list                           available firmware (catalog)
px30-radio switch thread|zigbee|router [stable|latest|beta] [--yes] [--force]
px30-radio backup                         Thread: save the active dataset to /data/radio/backup
px30-radio apply                          (at boot) run only the chosen stack's service
```

- **Stacks:**
  - `thread` = OpenThread RCP + `otbr-agent`;
  - `zigbee` = EmberZNet NCP + `px30-zigbee-bridge`;
  - `router` = the stand-alone Zigbee router (no service on the panel: the
    chip routes by itself and joins an open network; experimental).
- **Channels:** `stable` / `latest` / `beta`, from
  `/usr/share/px30-radio/catalog.json`. A downloaded `/data/radio/catalog.json`
  takes precedence. `px30-update radio sync` downloads it, with the firmware it
  lists, from the URL in `update.radio_catalog_url` of
  `/data/px30-agent/config.json`; the agent does that on every update check.
  The image only ships `stable` and `latest`; `beta` exists when one is
  published. The catalog and its firmware come from the `firmware` branch of
  this repository and are signed (ECDSA P-256, public key
  `/etc/px30/radio-update.pub`); each channel has `RELEASE_NOTES.md`. See
  [updater/README.md](../updater/README.md).
- **Warnings before acting** (type the word to confirm, or pass `--yes`):
  - a stack switch loses the network, and all devices must be paired again;
  - an update or downgrade within a stack risks it.
  - For Thread, the active dataset is saved first. For Zigbee, you are told to
    make a ZHA/Z2M backup.
- **Flashing** uses `universal-silabs-flasher` (`/opt/usf`) over the bootloader.
  If it fails, the old stack is restarted and the state is unchanged.
- **Only one stack runs at a time.** The choice is stored in
  `/data/radio/state.json` and applied at every start. Without a state file
  (first boot, after a factory reset) nothing is started; choose on the setup
  page or with `px30-radio use`.
- **Detecting the chip:** `px30-radio probe` stops both services, asks the chip
  with `universal-silabs-flasher probe` (Spinel = Thread, EZSP = Zigbee, only
  the Gecko bootloader = no firmware) and starts them again. Tested on the
  hardware: about 11 s. The setup page does this by itself when nothing is
  recorded and preselects the stack it finds.
- **`use`:** if the chip already runs the chosen stack, only its service is
  turned on (no flashing, network kept). Another stack needs `--flash`
  (= `switch`, network lost). `use off` keeps the chip's stack on record but
  runs neither service; `use <that stack>` turns it on again.
- **Zigbee bridge:** the bridge exposes the UART on TCP port 6638. ZHA uses
  `socket://<panel-ip>:6638`, Zigbee2MQTT uses `adapter: ember`. It listens on
  `127.0.0.1` only by default. Set `ZIGBEE_LISTEN=0.0.0.0` in
  `/data/radio/radio.conf` to open it to the LAN, which is **a port without
  authentication**.
- **From Home Assistant:** the selects *Radio module* and *Radio module
  channel*, plus the update entity *Radio module firmware*. All of them need the
  unlock switch.

Manual switch with the flasher:

```sh
systemctl stop otbr-agent
# Thread -> Zigbee
universal-silabs-flasher --device /dev/ttyThread \
  --probe-methods spinel:115200,bootloader:115200,ezsp:115200 \
  flash --firmware ncp-uart-hw-tyzs13-ezsp13-gsdk4.4.6-115200-hwflow.gbl
# Zigbee -> Thread
universal-silabs-flasher --device /dev/ttyThread \
  --probe-methods ezsp:115200,bootloader:115200,spinel:115200 \
  flash --firmware ot-rcp-tyzs13-gsdk4.4.6-115200-hwflow-btl.gbl
```

The Gecko bootloader uses no flow control; the applications do.
