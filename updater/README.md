# Radio firmware update pipeline (fwpipe)

One container for your own server that keeps the radio firmware of two devices
up to date:

| Device | Chip | Built with | Published to |
|---|---|---|---|
| TubesZB EFR32 MGM210 2022 ([Warre Edition](https://github.com/Warre118/TubesZB_MGM210_Warre_Edition)) | MGM210PA32JIA2 (series 2) | [NabuCasa/silabs-firmware-builder](https://github.com/NabuCasa/silabs-firmware-builder) + a board overlay | `firmware` branch of the TubesZB repo |
| PX30 wall panel radio | Tuya TYZS13 = EFR32MG13 (series 1) | Gecko SDK 4.x + this repo's `zigbee/` scripts | `firmware` branch of this repo |

Every `check_interval_h` hours it looks at the upstream sources, builds what
changed, moves the build a channel had to `archive/`, and puts the new one in
its place. The panel and the TubesZB read that branch, verify the signature and
report an update; installing it is one button on each device.

## Firmware types

| Type | What it is | TubesZB | Panel |
|---|---|---|---|
| `zigbee_ncp` | EmberZNet NCP (EZSP): Zigbee coordinator radio for ZHA / Zigbee2MQTT | yes | yes |
| `openthread_rcp` | OpenThread RCP: Thread radio for a border router, i.e. the radio side of **Matter over Thread** | yes | yes |
| `zigbee_router` | stand-alone Zigbee router (repeater), no host | prepared, **off** | yes, **experimental** |

"Matter" here always means the Thread radio. Matter itself runs on the host
(Home Assistant's Matter server + a Thread border router); there is no Matter
firmware for these radios.

About the router:

- **Panel:** [`zigbee/router/`](../zigbee/router) is the Gecko SDK's
  ZigbeeMinimal with a CLI on the UART and Nabu Casa's router bootloader
  commands ported to the Gecko SDK 4.x (`bootloader info`, `bootloader
  reboot`). `universal-silabs-flasher` finds it with the CLI `version` command
  and gets it back into the Gecko bootloader with `bootloader reboot`, so the
  panel can flash another stack over it without SWD. It joins an open Zigbee
  network by itself. The build checks the disassembly for the return to the
  bootloader. See [docs/radio.md](../docs/radio.md).
- **TubesZB:** [`devices/tubeszb-mgm210/zigbee_router.yaml`](devices/tubeszb-mgm210/zigbee_router.yaml)
  derives a router from Nabu Casa's ZBT-2 router by stripping that board's LEDs,
  accelerometer and button. It is `enabled: false` in `config.yaml` until a
  first manual build (`fwpipe run --device tubeszb-mgm210 --firmware
  zigbee_router`). The ESP32 can always put the radio in its bootloader with a
  GPIO, so the way back is safe.
- A router joins an existing Zigbee network as a repeater. The device stops
  being a coordinator; its own network is gone.

## Channels

| Channel | TubesZB (follows Nabu Casa's builder) | Panel (follows the Gecko SDK) |
|---|---|---|
| `stable` | newest full release of the builder: what Home Assistant ships for its own adapters | pinned in `config.yaml` (4.4.6, the build tested on the panel); promote by editing the pin |
| `latest` | head of `main`, with the SDK its manifests name | highest Gecko SDK 4.x release |
| `beta` | newest pre-release, only while it is newer than the release | a Gecko SDK pre-release, when one exists |

As of 2026-11-12 that gives:

| | stable | beta | latest |
|---|---|---|---|
| TubesZB Zigbee | builder v2026.02.23, Gecko SDK 4.5.0 (EmberZNet 7.5.1.0) | v2026.09.17-beta2, Simplicity SDK 2026.6.1 (9.1.1.1) | main |
| TubesZB Thread | v2026.02.23, Simplicity SDK 2025.6.2 (2.7.2.0) | as Zigbee | as Zigbee |
| Panel | Gecko SDK 4.4.6 (EmberZNet 7.5.0.0) | none | Gecko SDK 4.5.1 (EmberZNet 7.5.2.0) |

The TubesZB runs 9.1.1.1 and follows **`beta`** (the default of its channel
select); `stable` would be a downgrade.

A channel only gets a new build when the **firmware version** changes (or when
the overlay / recipe in this repo changes: the version then gets a `-r2`
suffix). Upstream commits that do not change the version are built once, to
find that out, and not published. A channel whose source disappears (a beta
that became the release) is moved to the archive and removed.

A failed build leaves the channel as it was, is reported, and is retried after
24 h, then 48 h, up to once a week.

## What gets published

Each device has its own repository and the firmware goes to its orphan
**`firmware`** branch, so the code history never fills up with binaries:

```
<firmware>/<channel>/
    <device>_<firmware>_<version>.gbl
    manifest.json (+ .sig)     version, url, sha256, md5, size, baud rate, sdk, source,
                               release_summary (<= 250 chars), release_url
    RELEASE_NOTES.md           what changed: the upstream release notes (or the
                               commit list for a branch channel) and the SDK / stack versions
    efr32_flasher.json         (TubesZB) flat manifest in the format of tube0013's efr32_flasher
catalog.json (+ .sig)          (panel) the px30-radio catalog
archive/<firmware>/<channel>/<version>_<time>/     earlier builds (10 kept per channel)
index.json, README.md          everything in one file / a table of the current builds
```

URLs are `https://raw.githubusercontent.com/<repo>/firmware/...`.

## Signing

Every `manifest.json` and `catalog.json` gets a `.sig` next to it: an ECDSA
P-256 / SHA-256 signature (DER, base64), made with `openssl dgst -sha256
-sign`. The manifests carry the sha256 of the firmware, so the signature covers
the firmware too. Both devices refuse an unsigned or altered manifest:

- the panel with `/etc/px30/radio-update.pub` (in the image);
- the TubesZB with `public_key:` in its ESPHome config (mbedTLS on the ESP32).

The key pair:

```bash
openssl ecparam -name prime256v1 -genkey -noout -out keys/radio-update.pem
```
```bash
openssl ec -in keys/radio-update.pem -pubout -out radio-update.pub
```

Only the server has the private key (`updater/keys/radio-update.pem`, mounted
read-only at `/keys`). The public key goes into
`rootfs/overlay/etc/px30/radio-update.pub` and the TubesZB YAML. A new key
pair means a new panel image and a new TubesZB firmware.

What the signature does not stop: an attacker who controls the download can
replay an *older* signed manifest of the same channel. Nothing is ever flashed
without someone pressing Install.

## Setting it up on the server

Needs Docker with Compose on an **x86-64** Linux host, about 15 GB of disk for
the tool cache (Arm GCC, Simplicity / Gecko SDKs) and outbound internet. No
Docker socket, no host access: everything runs inside the one container as an
unprivileged user. The first pass downloads several GB and takes a long time;
later passes only do something when upstream moved.

1. A fine-grained GitHub token for both device repositories with *Contents:
   read and write*. The `firmware` branch is created on the first push.
2. On the server:

   ```bash
   git clone https://github.com/Warre118/px30-wallpanel.git && cd px30-wallpanel/updater
   ```
   ```bash
   cp .env.example .env
   ```
   Put the token in `.env`, and the private key in `keys/radio-update.pem`
   (readable by uid 1000).
3. See what it would do, without building:

   ```bash
   docker compose run --rm fwpipe check
   ```
4. Do one pass by hand and watch it (start small):

   ```bash
   docker compose run --rm fwpipe run --device tubeszb-mgm210 --firmware zigbee_ncp --channel beta
   ```
5. Leave it running:

   ```bash
   docker compose up -d --build
   ```

Other commands: `fwpipe status`, `fwpipe run --force` (rebuild and republish),
`fwpipe run --no-push` (commit in the volume, do not push).
`FWPIPE_LOCAL_ONLY=1` builds and writes everything under `/data/repos/local_<device>`
without touching GitHub.

**LAN only.** Set `publish.base_url` per device to your own server and run
`fwpipe serve --port 8080`. Both devices only accept `https` URLs, so put a TLS
reverse proxy in front.

**Security notes.**

- The token can write to the two device repositories. The devices check the
  signature, so the token alone cannot offer them firmware; the private key is
  what matters.
- The container listens on no port (unless you use `serve`).

## What is in the container

- `/opt/silabs`: slc-cli 6, Silicon Labs' Python adapter pack, ZAP and a Java
  21 runtime, downloaded from `updates.silabs.com` at image build time with
  pinned checksums. `bin/slc` is a wrapper that adds `--tool-path` for those
  packs (older builder releases do not pass it) and puts the pack's Python
  first on PATH (slc runs the SDK's Jinja template generators with `python3`
  from PATH); `bin/commander` is a small stand-in that makes GBL files with
  `pygbl` (the only Commander call the builder makes), and `bin/slt` answers
  the one `slt where commander` question generated CMake projects ask.
- `/data/cache` (volume): Arm GCC per version, Simplicity SDKs (from Silicon
  Labs' conan server), Gecko SDKs (GitHub), and each builder release's own
  Python environment (uv, from its `uv.lock` / `requirements.txt`), fetched on
  first use and reused. See [`fwpipe/toolset.py`](fwpipe/toolset.py).
- `/data/repos` (volume): checkouts of the device repositories' `firmware`
  branches.

## How the devices see an update

### PX30 wall panel (built in, shows up in Home Assistant)

1. The agent's update check (every 6 h, or the *Check for updates* button) runs
   `px30-update radio sync`. It downloads
   `https://raw.githubusercontent.com/Warre118/px30-wallpanel/firmware/catalog.json`
   (`update.radio_catalog_url` in `/data/px30-agent/config.json` overrides it;
   `""` turns it off), checks its signature, and fetches the firmware files it
   lists into `/data/radio`, checking their sha256. Nothing is flashed.
2. The update entity *Radio module firmware* then compares the version on the
   chip with the build of the selected channel (*Radio module channel*:
   stable / latest / beta) for the stack on the chip, with the release summary
   and a link to the release notes.
3. *Install* runs `px30-radio switch <stack> <channel>`, behind the unlock
   switch. Flashing uses `universal-silabs-flasher` 1.3; it still has to be
   tested on the hardware ([docs/radio.md](../docs/radio.md)).

### TubesZB (ESPHome: on its web page and in Home Assistant)

The `efr32_radio` update platform in the TubesZB repo
(`esphome/components/efr32_radio`, documented in its `docs/radio-updates.md`)
reads `<firmware>/<channel>/manifest.json` and its signature. On the device:
a *Radio firmware channel* select (stable / latest / beta, default beta), a
*Radio firmware type* select (Zigbee / Thread), and the update entity with the
installed and offered version, the release summary and a link to
`RELEASE_NOTES.md`. *Install* downloads the image into the ESP32's spare OTA
partition, checks size and sha256 against the signed manifest, puts the EFR32
in its Gecko bootloader (boot pin + reset) and sends it over XMODEM. It works
next to `uart_tcp`: the bridge is paused while flashing.

## Changing what is built

- [`config.yaml`](config.yaml): devices, firmware types, what each channel
  follows, where each device publishes.
- [`devices/tubeszb-mgm210/*.yaml`](devices/tubeszb-mgm210): board overlays
  (pins, flow control, removed LED). Format: [`fwpipe/overlay.py`](fwpipe/overlay.py).
- Panel recipe: `zigbee/gen-*.sh`, `zigbee/build-*.sh`, `zigbee/patch-*.py`,
  run by [`gsdk/build.sh`](gsdk/build.sh). The build fails, and nothing is
  published, when the image cannot return to the Gecko bootloader.

Tests (no network, no Docker; need PyYAML and pygbl):

```bash
cd updater && python3 -m unittest tests.test_fwpipe
```
```bash
python3 -m unittest tests.test_update
```

## Status

Tested:

- The pipeline logic, publishing, release notes and signing (tests).
- The panel builds (NCP, RCP, router) with the recipe outside a container
  (WSL, Gecko SDK 4.4.6), including the bootloader-return checks.
- The panel's download side with signed catalogs (tests).
- The TubesZB ESPHome firmware with the update platform compiles.
- Native runs of the container's toolset on midgard (same tools, user space,
  `FWPIPE_LOCAL_ONLY=1`): all six TubesZB builds (Zigbee and Thread on
  stable, beta and latest) build, publish, sign and verify. The panel builds
  were not run there (no `make` for that user); they run in WSL and in the
  image.

Not tested yet:

- Building and running the image itself, and a push to GitHub.
- Flashing: the TubesZB from its web page, the panel with `px30-radio switch`,
  and the router on the panel.
- OpenThread RCP on the TubesZB hardware.
- The TubesZB router (never built).
