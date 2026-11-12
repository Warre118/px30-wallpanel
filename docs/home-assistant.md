# Home Assistant integration (px30-agent)

The dashboard itself is just Home Assistant in a browser. On top of that, the
panel can show up in Home Assistant as a **device** with controls and sensors,
through **MQTT discovery**. Nothing needs to be installed in HA besides the
MQTT integration and a broker (e.g. the Mosquitto add-on).

## Why MQTT discovery

| | MQTT discovery (chosen) | Own HA integration + HTTP API on the panel |
|---|---|---|
| Install in HA | nothing extra | custom component, maintained for every HA release |
| Entities | created automatically | config flow, coordinator, platforms to write |
| Update entities | native `update` platform | to build yourself |
| Security | the panel connects **out**; no open port; one broker account per panel; TLS possible | an open port on the panel with its own authentication |
| Availability | MQTT last will: entities become unavailable when the panel drops | polling |

## Setup

1. In HA: install a broker and the MQTT integration. Create a user per panel
   (e.g. `px30-living`).
2. On the panel: enter broker, port, TLS, user and password on the
   [setup page](usage.md#first-setup-with-your-phone). Or run
   `sudo px30-agent-setup` over SSH. It also works non-interactively:
   `--host … --password-file …`, plus `--show`, `--test` and `--disable`.
3. The connection is tested (CONNACK within 10 s), stored in
   `/data/px30-agent/config.json` (mode 0600) and the agent is restarted.
4. The device appears under *Settings → Devices → MQTT*.

Without a broker, the agent exits cleanly and the panel stays a plain kiosk.

## Architecture

```
Home Assistant ── MQTT broker ──(outgoing, optional TLS)── px30-agent (user px30-agent)
                                                            │  sudo -n, exact arguments only
                                                            ├─ px30-ctl status|rescue|reinstall|factory-reset
                                                            ├─ systemctl reboot / ssh on|off
                                                            ├─ px30-panel dashboard|setup|menu
                                                            ├─ px30-radio switch thread|zigbee stable|latest --yes
                                                            └─ ot-ctl rcp version
                     /data/px30-agent/display.json ──► proximity-wake (owns backlight + sensor)
                     /run/px30-display/state.json  ◄── proximity-wake (live: presence, lux, screen)
```

- One Python process, using `python3-paho-mqtt` and the standard library. It takes about
  15 MB of memory (`MemoryMax=64M`).
- It runs as its own user `px30-agent`, not as root. A bug in the MQTT parsing
  can therefore reach at most the fixed command lines in
  `/etc/sudoers.d/30-px30-agent`: no shell, no Wi-Fi keys, no `/data`.
- `proximity-wake` is the only process that writes the backlight. The agent writes settings
  and sends a signal.

## Topics

`<base>` = `px30/<device_id>`; the default `device_id` is
`px30_<hostname>_<last 6 of machine-id>`.

| Topic | Direction | Content |
|---|---|---|
| `homeassistant/<component>/<device_id>/<object>/config` | → HA, retained | discovery config per entity |
| `<base>/availability` | → HA, retained | `online` / `offline` (last will) |
| `<base>/state` | → HA, retained | one JSON object with all values; sent when something changes, checked every 30 s |
| `<base>/update/firmware`, `<base>/update/module` | → HA, retained | update entity state |
| `<base>/cmd/<object>` | HA → panel | command |
| `homeassistant/status` | HA → panel | `online` → the agent publishes everything again |

Command rules:

- **Retained commands are ignored**, so a stale retained `factory_reset` can never fire at
  every start.
- Payloads are cut at 64 bytes and validated strictly:
  - `PRESS` for buttons;
  - `ON`/`OFF` for switches;
  - a finite number, clamped to min/max/step, for numbers;
  - a known option for selects;
  - `install` for updates.
- Everything else is refused.

## Entities

**Buttons:**

- Restart;
- Reload kiosk;
- Set up with phone;
- Show rescue menu on screen;
- Check for updates;
- Restart into rescue mode, Reinstall (keep settings) and Factory reset (erase everything).

**Confirmation for dangerous actions** (rescue, reinstall, factory reset, radio module):

1. Turn on the switch *Unlock dangerous actions*.
2. Press the button within 60 s.
3. The switch turns itself off after the action or after the timeout.

Without unlocking, the action is refused, and the *Last action* sensor says why.

**Switches:**

- Unlock dangerous actions;
- SSH (the choice is stored on `/data`);
- Automatic brightness;
- Screen (on = wake as if touched; off = off now; proximity and touch keep working).

**Numbers:**

| Object | Range | Meaning |
|---|---|---|
| Brightness | 1–100 % | manual brightness; the maximum when automatic |
| Minimum brightness (auto) | 1–100 % | lower limit when automatic |
| Screen off after | 5–3600 s | after the last presence or touch |
| Proximity threshold | 5–1000 | above the floating baseline |
| Proximity release threshold | 1–999 | hysteresis, kept below the threshold |

**Selects:**

- Radio module (`thread` / `zigbee`; switching erases the network!);
- Radio module channel (`stable` / `latest` / `beta`).

**Binary sensor:** Presence (occupancy).

**Sensors:**

- IP address;
- Wi-Fi signal;
- Booted (timestamp);
- CPU temperature;
- Free memory;
- Free space system and `/data`;
- Boot slot;
- System version;
- Module firmware;
- Thread state;
- Last action;
- Ambient light (lx).

Disabled by default: Load, Kernel, Agent version, Proximity (raw).

**Updates:**

- *Firmware* (the system);
- *Radio module firmware*.

The check runs every 6 hours and on "Check for updates".

- *Firmware* is compared with the GitHub releases of `update.repo`. Installing
  the system from GitHub is **not implemented yet** (`px30-update system` is a
  stub). Use `tools/px30-ota.sh` from a PC.
- *Radio module firmware* is compared with the radio firmware catalog: the
  build of the selected channel for the stack on the chip. With
  `update.radio_catalog_url` set, the check first runs `px30-update radio sync`,
  which downloads the published catalog and firmware into `/data/radio`
  (checksums verified; a signature too when `/etc/px30/radio-update.pub`
  exists). Without it, only the firmware in the image is known. Installing
  goes through `px30-radio switch` and **does not work yet**
  ([radio.md](radio.md)). The pipeline that publishes the catalog:
  [updater/README.md](../updater/README.md).

## Security notes

- Use one broker account per panel. With a Mosquitto ACL the panel can be
  limited to `px30/<id>/#`, `homeassistant/+/<id>/#` (write) and
  `homeassistant/status` (read), so a compromised panel cannot control other devices.
- Use TLS (port 8883) when your broker offers it.
- No payload reaches a root command:
  - the command lines are constants;
  - sudoers allows exact arguments only, without wildcards.
- `config.json` holds the broker password in plain text (mode 0600, owner
  `px30-agent`), as Zigbee2MQTT does.
- Anyone with admin rights in HA can wipe the panel. That is intended. The unlock
  switch only protects against misclicks and automation mistakes.
- Future system update downloads must be signed (a public key in the image),
  not only checked against a sha256 from the same release. The radio firmware
  catalog and manifests are signed (ECDSA P-256); the public key is in the image
  as `/etc/px30/radio-update.pub`, and an unsigned or altered catalog is
  refused.

## Development

```sh
python3 agent/px30-agent.py --config agent/px30-agent.conf.example --dry-run \
  --simulate brightness=40 --simulate arm=ON --simulate factory_reset=PRESS
```

`--dry-run` prints what would be published and executed, without a broker and
without running anything.
