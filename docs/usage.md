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
   - **Radio module:** Thread, Zigbee or Off. After a factory reset the panel
     asks the chip which firmware it has (about 15 s) and preselects it.
     Choosing the other stack needs a tick at "Flash the chip": that takes
     about 2 minutes and erases the network on it ([radio.md](radio.md)).
   - **Sensors:** turn the proximity sensor or the light sensor off (see
     "Screen and sensors").
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

### Wi-Fi setup (hotspot)

> **Not yet tested on the hardware.** The code is in the image from the next
> release on. It was built and tested on a PC only (`tests/`). Until it has
> been tried on the panel, keep a USB cable or a baked-in Wi-Fi profile
> (`WIFI_SSID`/`WIFI_PSK`) as a fallback.

When the panel has no Wi-Fi network, it opens its own network so you can set
one up with your phone:

1. The panel screen shows **two QR codes**, the network name `PX30-xxxx` and
   its password (new for every session).
2. Scan the **first** QR code: the phone joins `PX30-xxxx`. That network has
   no internet, but the panel does not act as a captive portal, so no
   "sign in to network" window pops up.
3. Scan the **second** QR code (`http://192.168.4.1/setup#PIN`). It opens the
   Wi-Fi page in your normal browser. Pick your home network (or type a hidden
   network's name) and enter its password. Any other `http://` address you
   open while on `PX30-xxxx` also leads to this page.
4. The panel leaves its own network and joins yours. Your phone falls back to
   your usual network by itself.
   - **It worked:** the page waits until it finds the panel at
     `http://<hostname>.local`. It then opens the normal setup
     (`https://<hostname>.local:8443/setup`, same PIN). If your phone cannot
     resolve `.local` names, scan the QR code that the panel screen now shows:
     it holds the panel's new IP address.
   - **Wrong password, or network not found:** the panel opens `PX30-xxxx`
     again with the same name and password. Your phone rejoins it, and the
     page shows the error so you can try again.

When the hotspot starts:

- at boot, when no Wi-Fi network is known (first boot, after a factory reset)
  and none has connected within 45 seconds;
- when the known network has been unreachable for **5 minutes** (wrong
  network, router replaced). While no one is using the setup page, the panel
  tries the known network again every 5 minutes, so a router that was only
  off for a while is picked up again by itself;
- from the rescue menu ("Wi-Fi setup"), or over SSH with
  `sudo px30-panel hotspot`. This leaves the current network.

If a known network exists, the hotspot screen has a **Cancel** button that
returns to normal Wi-Fi.

Notes:

- Android: if the browser cannot open `http://192.168.4.1/setup`, turn off
  mobile data for a moment. Android may say "no internet" or "limited
  connection" for `PX30-xxxx`; that is expected, stay connected.
- Security: the hotspot uses WPA2 with a random password that is shown only
  on the panel's screen, and the setup page needs the PIN on top of that. Wi-Fi
  names and passwords are never written to the log.
- How it works: iwd's own access-point mode and DHCP server, a small DNS
  server that answers every name with `192.168.4.1`, and a web server on port
  80 that answers the phones' internet checks with "online" (no captive
  portal window). All of it lives in px30-panel
  (`/usr/local/lib/px30/px30_hotspot.py`), so there are no extra packages or
  services.

Other ways to set up Wi-Fi:

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
| Wi-Fi setup | start the Wi-Fi hotspot (see above); leaves the current network |
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

The radio stack (Thread border router or Zigbee bridge, or off) is chosen on
the setup page, in Home Assistant or with `px30-radio` ([radio.md](radio.md)).
Only the service of the chosen stack runs; with "Off" neither does.

Choices are stored in `/data/px30-panel/services.json` and applied again at
every start, also after an update.

## Screen and sensors

- The screen turns off after a period without presence (default 60 s) and
  turns back on when someone comes close (proximity sensor) or touches it.
- Brightness can follow the ambient light. Settings are in Home Assistant.
- Both sensors can be turned off on the setup page or in Home Assistant
  (*Proximity sensor*, *Light sensor*). Without the proximity sensor only a
  touch wakes the screen; without the light sensor there is no ambient-light
  value and the brightness stays fixed.
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
