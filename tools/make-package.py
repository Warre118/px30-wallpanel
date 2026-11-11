#!/usr/bin/env python3
"""make-package.py - zip a release for a first install with RKDevTool.

  python3 tools/make-package.py <release-dir> [out-dir]

The zip holds the release files (px30-boot.img, px30-rescue.img,
px30-rootfs.img.zst, VERSION, SHA256SUMS), tools/px30-install.sh,
misc-boot-recovery.img and INSTALL.txt.

misc-boot-recovery.img is a 2 KiB Android bootloader message with the command
"boot-recovery". Written to the misc partition at 16 KiB (sector 0x8020 on
both the stock and the PX30 layout), it makes the vendor U-Boot start the
recovery partition once, so no adb is needed to reach the rescue image.
px30-rescue clears it again (bcb_clear) on "px30-rescue reboot".

No bootloader parts (idblock/loader, uboot, trust) are ever part of the package.
"""
import hashlib, os, sys, zipfile

INSTALL = """PX30 wall panel - Debian 13 Home Assistant kiosk
Release {ver} - BETA
Project: https://github.com/Warre118/px30-wallpanel

!! BETA. Read this first !!
- This replaces Android on the panel's eMMC. Make the full backup described in
  docs/flashing.md (section 1) FIRST and keep it safe: it is your only way back.
- Bluetooth (RTL8723DS) does not work, and probably never did.
- Flashing the radio module with another stack or version (px30-radio switch,
  "use --flash", the module update in Home Assistant) does not work yet.
  Choosing Thread / Zigbee / Off without flashing works.
- The Wi-Fi setup hotspot was tested on the hardware once.
- Tested on one panel only (Smatek ST-PX30-T6E, 2021 board).
You do all of this at your own risk.

You need
- a Windows PC with RKDevTool (v3.37 tested) and the Rockchip USB driver,
- Git for Windows (for "Git Bash": bash, ssh, sha256sum),
- a micro-USB DATA cable to the panel's OTG port, and the panel's own power
  supply.

Step 1 - RKDevTool (Loader mode)
  1. Unzip this package. Hold the panel's BOOT button while you plug in the
     power. After about 3 s RKDevTool shows "Found One LOADER Device".
  2. Tab "Download Image": UNTICK every default row. Add two rows
     (right-click > Add Item), tick them, and tick "Write by Address":
        Address 0x00032000   Name recovery   File px30-rescue.img
        Address 0x00008020   Name misc       File misc-boot-recovery.img
     The addresses are sectors on the STOCK Android layout (recovery at
     0x32000, 64 MiB; misc at 0x8000). Check them against partitions.txt of
     your backup.
  3. Click "Run". NEVER write anything else: no loader, uboot, trust or
     parameter rows.

Step 2 - start the rescue image
  Unplug and replug the power (no BOOT button). The misc row makes the panel
  start the rescue image instead of Android. (The misc row has not been
  tested yet: if Android starts anyway, run "adb reboot recovery".)
  The screen shows "PX30 RESCUE MODE". Windows finds a USB network adapter
  ("UsbNcm Host Device"); the panel is 10.55.0.1.

Step 3 - install (Git Bash, in the unzipped folder)
     bash px30-install.sh . --repartition
  --repartition writes the PX30 partition table once. uboot, trust, misc and
  resource stay where they are; the Smatek partitions are copied along; all
  Android partitions are gone afterwards. It takes about 5 minutes.
  Then start Debian:
     ssh root@10.55.0.1 px30-rescue reboot
  (rescue mode needs no password over USB)

Step 4 - set up
  - Wi-Fi: the panel opens its own network PX30-xxxx and shows two QR codes.
    Scan the first (join PX30-xxxx), then the second (setup page), pick your
    Wi-Fi and enter its password.
  - Then the normal setup page: Home Assistant login, MQTT, SSH access,
    radio module (Thread / Zigbee / Off), sensors, services.
  - Rescue menu on the panel: tap twice in every corner of the screen.
  - USB rescue at any time: ssh root@10.55.0.1 in rescue mode.

Files
{files}
Later updates over Wi-Fi: tools/px30-ota.sh in the project (see docs).
"""


def bcb():
    """struct bootloader_message: command[32] status[32] recovery[768] stage[32] reserved[1184]"""
    m = bytearray(2048)
    m[0:13] = b"boot-recovery"
    rec = b"recovery\n"
    m[64:64 + len(rec)] = rec
    return bytes(m)


def main(a):
    if not a:
        sys.exit(__doc__)
    rel = a[0]
    out = a[1] if len(a) > 1 else os.path.dirname(os.path.abspath(rel))
    ver = open(os.path.join(rel, "VERSION")).read().strip()
    names = ["px30-boot.img", "px30-rescue.img", "px30-rootfs.img.zst", "VERSION", "SHA256SUMS"]
    for n in names:
        if not os.path.isfile(os.path.join(rel, n)):
            sys.exit(f"missing in {rel}: {n}")
    proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    extra = {"misc-boot-recovery.img": bcb(),
             "px30-install.sh": open(os.path.join(proj, "tools/px30-install.sh"), "rb").read()}
    sums = []
    for n in names:
        h = hashlib.sha256(open(os.path.join(rel, n), "rb").read()).hexdigest()
        sums.append(f"  {n:24} {os.path.getsize(os.path.join(rel, n)):>11} bytes  sha256 {h}")
    for n, b in extra.items():
        sums.append(f"  {n:24} {len(b):>11} bytes  sha256 {hashlib.sha256(b).hexdigest()}")
    text = INSTALL.format(ver=ver, files="\n".join(sums)).replace("\n", "\r\n")
    base = f"px30-wallpanel-{ver}"
    zpath = os.path.join(out, base + ".zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            # the images are compressed already (zstd) or mostly empty: store vs deflate
            z.write(os.path.join(rel, n), f"{base}/{n}",
                    compress_type=zipfile.ZIP_STORED if n.endswith(".zst") else zipfile.ZIP_DEFLATED)
        for n, b in extra.items():
            z.writestr(f"{base}/{n}", b)
        z.writestr(f"{base}/INSTALL.txt", text)
    h = hashlib.sha256(open(zpath, "rb").read()).hexdigest()
    with open(zpath + ".sha256", "w") as f:
        f.write(f"{h}  {base}.zip\n")
    print(zpath, os.path.getsize(zpath), h)


if __name__ == "__main__":
    main(sys.argv[1:])
