# px30-agent

Home Assistant integration over MQTT discovery. Design, entities and security
model: [docs/home-assistant.md](../docs/home-assistant.md).

| File | Installed as |
|---|---|
| `px30-agent.py` | `/usr/local/bin/px30-agent.py` |
| `px30-agent.service` | `/etc/systemd/system/px30-agent.service` (starts only with `/data/px30-agent/config.json`) |
| `30-px30-agent.sudoers` | `/etc/sudoers.d/30-px30-agent` (exact command lines only) |
| `px30-agent.sysusers`, `px30-agent.tmpfiles` | system user and `/data/px30-agent` ownership |
| `61-px30-backlight.rules` | backlight write access for group `video` |
| `px30-update` | `/usr/local/sbin/px30-update`: `radio sync` downloads the published radio firmware catalog into `/data/radio`; `system latest` is a **stub** (system updates from GitHub are not implemented) |
| `px30-agent.conf.example` | example for `/data/px30-agent/config.json` |

`rootfs/build-rootfs.sh` installs all of these. Configure the agent with the
setup page or `sudo px30-agent-setup`.

Test without a broker:

```sh
python3 agent/px30-agent.py --config agent/px30-agent.conf.example --dry-run \
  --simulate brightness=40 --simulate arm=ON --simulate factory_reset=PRESS
```
