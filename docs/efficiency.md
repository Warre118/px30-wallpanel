# Efficiency and robustness review

A review of release 2026.11.11-4, measured on the panel on 2026-11-11 after it
had been up for 1 h 40 min. The screen was off (no one near), the Home Assistant
dashboard was loaded and Thread (otbr-agent) was the active radio stack.

## What was measured

| Item | Value |
|---|---|
| RAM | 950 MB total, ~270 MB available, 18 MB of the 474 MB zram swap in use |
| Load average | ~2.1 (4 cores) |
| CPU | ~35 % user, ~12 % system; the cores sit at the top clock (1296 MHz) |
| SoC temperature | ~60 °C |
| Biggest processes | WPEWebProcess 414 MB RSS / ~95 % of one core, cog 109 MB / ~20 %, WPENetworkProcess 81 MB |
| Journal | 6.2 MB in RAM (volatile, capped at 16 MB) |
| Log volume | otbr-agent wrote 4844 of the 5925 journal lines since boot |
| Root file system | 871 MB of 2.8 GB used; mounted `noatime,commit=60` |
| Failed units / core dumps | none |

The running services are what the panel needs: iwd, networkd, resolved,
timesyncd, ssh, avahi, otbr-agent, proximity-wake, px30-agent, px30-audio,
px30-panel and the kiosk session. The only timers are fstrim (weekly; useful
for the eMMC), systemd-tmpfiles-clean, px30-boot-ok (one-shot) and
dpkg-db-backup.

Settings that were already right and stay as they are:

- journald keeps logs in RAM only (`Storage=volatile`, `RuntimeMaxUse=16M`),
  so logging never wears the eMMC.
- Core dumps are kept but capped (`MaxUse=64M`, `KeepFree=512M`,
  `ProcessSizeMax=256M`).
- zram swap is half the RAM, zstd compressed, with `vm.swappiness=100` and
  `vm.page-cluster=0`, which suits compressed swap.
- apt-daily and apt-daily-upgrade are disabled.
- Long-running services restart themselves (`Restart=always` or
  `on-failure`), and px30-agent is capped at `MemoryMax=64M`.
- Cog runs with `--web-mem-limit=400` and its memory-pressure thresholds, and
  restarts the web process after a crash.

## Changes made (in the build; they take effect with the next release)

1. **otbr-agent logs at NOTICE (`-d 5`)** in `/etc/default/otbr-agent`. At
   the default INFO level OpenThread logs about 50 lines a minute (MLE
   advertisements, frame dumps, "already subscribed" notes). That costs CPU in
   journald and pushes every other log out of the 16 MB journal within hours.
   For Thread debugging, set `-d 6` (or 7) and restart otbr-agent.
2. **dpkg-db-backup.timer is disabled.** It saves a copy of the dpkg database
   every day. The image is replaced as a whole (A/B), so the copy has no use
   and only writes to the eMMC.
3. **No Python bytecode caches in the image.** Syntax checks on the PC can
   leave `__pycache__` folders in the overlay. build-rootfs.sh now deletes
   them from `/usr/local` before the image is packed.

None of these were deployed to the running panel: restarting otbr-agent touches
the Wi-Fi backbone interface, and the panel had no second link at the time.

## Recommendations (need a test on the hardware)

1. **Pause the web page while the screen is off.** This is by far the biggest
   win. With the screen off, WebKit still renders the dashboard and uses about
   one core all the time. That keeps the CPU at its top clock and the SoC at
   ~60 °C. Possible approaches, in order of preference:
   - Turn off animated dashboard content (screensaver slideshows, animated
     cards) on the panel's dashboard. This is a Home Assistant setting and
     needs no change to the panel.
   - Let proximity-wake send `SIGSTOP` to the WPEWebProcess when the screen
     turns off and `SIGCONT` when it turns on. On wake, Home Assistant
     reconnects its websocket within a second or two. First check that Cog's
     unresponsive-process handling does not restart the web process while it
     is stopped.
2. **Audio keep-alive.** px30-audio plays silence through dmix all the time
   (about 4 % of one core). The amplifier stays powered and there is no pop
   when a sound starts. This was a deliberate choice. A larger dmix period
   (for example `period_size 4096`) should lower the wake-up rate. Listen for
   pops after changing it.
3. **OOM priorities.** The kernel OOM killer will pick the WPEWebProcess
   first, because it is by far the largest process, and Cog then restarts it.
   This is the right victim, so no `OOMScoreAdjust` tuning is needed for now.
