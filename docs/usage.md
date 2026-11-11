# Using the panel

## First setup with your phone

A new panel, or one after a factory reset, starts in setup mode. The screen
shows:

- a QR code;
- a 6-digit PIN;
- the address `https://<panel-ip>:8443/setup`;
- the start of the certificate fingerprint.

1. Scan the QR code. The PIN is already filled in through the link.
2. Your browser warns about the certificate. The panel makes its own
   self-signed certificate (`/data/px30-panel/setup.crt`). Compare the
   fingerprint with the screen, then continue.
3. Fill in what you need. Every section is independent: change one thing
   without retyping the rest.
   - **Home Assistant:** the dashboard URL (e.g.
     `https://ha.example.com/dashboard-panel/home`), your HA user name and
     password, and the 2FA code if HA asks for one.
     - The panel runs the HA login flow itself and hands the resulting
       authorization code to the kiosk browser. HA's own frontend then stores
       its tokens there, like a normal browser login.
     - **The password is never stored.**
     - If the panel is already logged in, changing only the dashboard on the
       same HA server needs no new login. A URL on another HA server opens
       the login fields by itself. Tick "Log in again" to change user.
   - **MQTT** (optional, for the [Home Assistant device](home-assistant.md)):
     the stored settings are filled in. Change what you need; the section is
     only applied when something in it differs.
     - The port defaults to 1883, and switches to 8883 when you tick TLS (unless
       you typed your own port).
     - Leave the password empty to keep the stored one.
     - An empty host disables the agent.
   - **SSH access** for user `admin`:
     - *keep*: no change;
     - *my public key*: paste an `ssh-ed25519 …` or `ssh-rsa …` line;
     - *generate a key pair*: the panel makes an ed25519 key and shows the
       private key **once**, with a download button. Store it, then connect with
       `ssh -i px30-panel-key admin@<panel-ip>`;
     - *password*: set a login password (stored as a SHA-512 crypt hash on `/data`);
     - *no password*: switch password login off again;
     - "Remove the other keys" replaces all keys instead of adding one.
   - **Services:** switch optional services on or off (see below).
4. Submit. The panel confirms each section and returns to the dashboard.

Security of the setup page:

- it only runs while the setup screen is shown, for 20 minutes at most;
- it locks after 10 wrong PINs;
- every setup session has a new PIN.

Open setup again at any time:

- from the rescue menu ("Set up with phone");
- from Home Assistant (button "Set up with phone");
- over SSH with `sudo px30-panel setup`.

### Wi-Fi

There is no hotspot setup yet. Choose one of these:

- build the image with `WIFI_SSID`/`WIFI_PSK` ([building.md](building.md));
- connect over USB (`ssh admin@10.55.0.1`) and run `iwctl station wlan0
  connect <ssid>`.

Profiles are stored in `/data/iwd` and survive updates and reinstalls (not a
factory reset).

## The rescue menu

**Tap twice in each of the four corners**, in any order, within 12 seconds.

- The touch screen is read directly from the kernel, so this works even when
  the dashboard hangs.
- The menu closes after 3 minutes without use.
- Also available as `sudo px30-panel menu` and as a button in Home Assistant.

| Button | What it does |
|---|---|
| Dashboard | back to the dashboard |
| Status | IP address, Wi-Fi, MQTT, SSH, boot slot, version, ambient light |
| Set up with phone | the QR setup described above |
| Services | switch optional services on/off |
| Restart kiosk | restarts the browser |
| Restart panel | reboot |
| Rescue mode | reboot into rescue mode (`ssh root@10.55.0.1` over USB) |
| Reinstall | reinstall the system from the factory copy; settings are kept |
| Factory reset | reinstall and erase all settings (asks twice) |

## Services

Optional services, switchable from the setup page and the rescue menu (SSH
also from Home Assistant):

| Service | Off saves |
|---|---|
| Audio (`px30-audio`) | keeps the speaker amplifier open to avoid a pop at every sound; about 3–4 % CPU |
| SSH (`ssh`) | no network login (rescue mode over USB still works) |

The radio stack (Thread border router or Zigbee bridge) is chosen with
`px30-radio` ([radio.md](radio.md)), and only the chosen one runs.

Choices are stored in `/data/px30-panel/services.json` and applied again at
every start, also after an update.

## Screen and sensors

- The screen turns off after a period without presence (default 60 s) and
  turns back on when someone comes close (proximity sensor) or touches it.
- Brightness can follow the ambient light. Settings are in Home Assistant.
- `px30-panel` signals: `sudo px30-panel dashboard` returns to the dashboard
  from setup or menu.

## SSH

```sh
ssh admin@<panel-ip>          # over Wi-Fi
ssh admin@10.55.0.1           # over the micro-USB OTG cable (USB network)
```

`admin` has sudo. Useful commands:

```sh
px30-ctl status               # slot, boot counter
sudo px30-ctl rescue          # rescue mode
px30-radio status             # radio module
journalctl -t kiosk-cog       # browser log
sudo px30-agent-setup         # MQTT setup on the command line
```
