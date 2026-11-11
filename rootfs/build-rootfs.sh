#!/usr/bin/env bash
# =====================================================================
# Debian 13 (trixie) arm64 kiosk rootfs for the Smatek PX30 wall panel.
# Rootless via mmdebstrap --mode=unshare (needs: mmdebstrap, uidmap,
# qemu-user-static + binfmt). Writes NOTHING to the panel.
#
# Usage (in WSL):  bash rootfs/build-rootfs.sh
# Optional:
#   SSH_PUBKEY=/path/to/key.pub      key for the user 'admin'
#   WIFI_SSID=... WIFI_PSK=...       preset Wi-Fi (otherwise later)
#   KERNEL_OUT=~/px30-build/out      kernel/DTB/modules (kernel/build-kernel.sh)
# Output: ~/px30-build/rootfs/px30-rootfs.tar and px30-rootfs.ext4
# =====================================================================
set -euo pipefail
PROJ="$(cd "$(dirname "$0")/.." && pwd)"
OVERLAY="$PROJ/rootfs/overlay"
KOUT="${KERNEL_OUT:-$HOME/px30-build/out}"
DEST="${DEST:-$HOME/px30-build/rootfs}"
SUITE=trixie
# Base image as small as practical: installing (px30-rescue write-rootfs /
# reinstall) grows it with resize2fs to rootfs_a/b (2.8 GiB). This also keeps
# the factory copy (zstd) small.
IMG_SIZE="${IMG_SIZE:-1800M}"
# kernel cmdline: one source for extlinux.conf and recovery.img (no initramfs -> PARTLABEL, no LABEL)
KCMDLINE="$(head -1 "$PROJ/kernel/cmdline.txt")"
mkdir -p "$DEST"

KVER="$(ls "$KOUT/lib/modules" | head -1)"
[ -n "$KVER" ] && [ -f "$KOUT/boot/Image" ] || { echo "no kernel in $KOUT; run kernel/build-kernel.sh first"; exit 1; }
# Optional. Without it SSH access is set up on the device (setup page: own
# public key, generated key pair or password). Public images: no key.
SSH_PUBKEY="${SSH_PUBKEY:-}"
if [ -n "$SSH_PUBKEY" ]; then [ -r "$SSH_PUBKEY" ] || { echo "ERROR: ssh key $SSH_PUBKEY not readable"; exit 1; }; else echo "note: no SSH_PUBKEY, SSH access via the setup page"; fi
echo "kernel: $KVER   suite: $SUITE   target: $DEST"

PKGS=(
  # base
  systemd systemd-sysv udev dbus kmod iproute2 ca-certificates
  systemd-resolved systemd-timesyncd systemd-zram-generator
  sudo less nano procps
  # network
  iwd firmware-realtek wireless-regdb openssh-server
  # kiosk: Cog/WPE on DRM + Mesa (Panfrost)
  # libgles2: without libGLESv2.so.2 WPEWebProcess crashes on every page (black screen)
  cog wpewebkit-driver libgl1-mesa-dri libgles2 libegl1 fonts-dejavu-core
  # boot animation (script plugin + DRM renderer are part of 'plymouth' itself)
  plymouth
  # extras: sensor daemon, audio, i2c debugging
  python3 alsa-utils i2c-tools zstd rsync
  # Thread border router: otbr-agent (built here, see zigbee/build-otbr.sh) publishes via avahi
  avahi-daemon libavahi-client3 libnss-mdns
  # Home Assistant: px30-agent (MQTT discovery); venv for universal-silabs-flasher (px30-radio)
  python3-paho-mqtt python3-venv
  # crashes (WebKit renderer) leave a core dump + stack trace: coredumpctl
  systemd-coredump
  # px30-panel: QR code for setup via phone (+ openssl for its self-signed certificate)
  python3-qrcode openssl
)
# radio firmware (zigbee/build-*.sh) -> /usr/share/px30-radio/firmware
RADIO_DIST="${RADIO_DIST:-$HOME/px30-build/zigbee/dist}"
# otbr-agent + ot-ctl (zigbee/build-otbr.sh); without it the Thread stack is not available
OTBR_DIST="${OTBR_DIST:-$HOME/px30-build/zigbee/otbr-dist}"
[ -x "$OTBR_DIST/usr/sbin/otbr-agent" ] || { echo "WARNING: no otbr-agent in $OTBR_DIST: image without Thread"; OTBR_DIST=; }
RADIO_FW=(ot-rcp-tyzs13-gsdk4.4.6-115200-hwflow-btl.gbl ncp-uart-hw-tyzs13-ezsp13-gsdk4.4.6-115200-hwflow.gbl)
for f in "${RADIO_FW[@]}"; do [ -r "$RADIO_DIST/$f" ] || { echo "ERROR: $RADIO_DIST/$f missing"; exit 1; }; done
RADIO_HOOKS=()
for f in "${RADIO_FW[@]}"; do RADIO_HOOKS+=(--customize-hook="upload '$RADIO_DIST/$f' /usr/share/px30-radio/firmware/$f"); done
# universal-silabs-flasher (pip, not in Debian); empty = newest
USF_VERSION="${USF_VERSION:-}"

# Wi-Fi profile only if passed in via the environment (never in git)
WIFI_TMP=""
if [ -n "${WIFI_SSID:-}" ] && [ -n "${WIFI_PSK:-}" ]; then
  WIFI_TMP="$(mktemp -d)"
  printf '[Security]\nPassphrase=%s\n' "$WIFI_PSK" > "$WIFI_TMP/$WIFI_SSID.psk"
  chmod 600 "$WIFI_TMP/$WIFI_SSID.psk"
elif [ -n "${IWD_DIR:-}" ]; then
  # ready-made iwd profiles (e.g. tools/android-wifi-to-iwd.py), kept private outside the project
  WIFI_TMP="$(mktemp -d)"
  cp "$IWD_DIR"/*.psk "$WIFI_TMP/"
  chmod 600 "$WIFI_TMP"/*.psk
fi
trap '[ -n "$WIFI_TMP" ] && rm -rf "$WIFI_TMP"' EXIT

mmdebstrap \
  --mode=unshare \
  --architectures=arm64 \
  --variant=minbase \
  --components="main non-free-firmware" \
  --include="$(IFS=,; echo "${PKGS[*]}")" \
  --dpkgopt='path-exclude=/usr/share/doc/*' \
  --dpkgopt='path-include=/usr/share/doc/*/copyright' \
  --dpkgopt='path-exclude=/usr/share/man/*' \
  --dpkgopt='path-exclude=/usr/share/locale/*' \
  --customize-hook="sync-in '$OVERLAY' /" \
  --customize-hook="mkdir -p \"\$1/usr/local/lib/px30\"" \
  --customize-hook="upload '$PROJ/initramfs/lib/px30-state.sh' /usr/local/lib/px30/px30-state.sh" \
  --customize-hook='mkdir -p "$1/usr/share/px30-radio/firmware" "$1/usr/share/doc/px30-agent" "$1/usr/lib/sysusers.d" "$1/usr/lib/tmpfiles.d"' \
  "${RADIO_HOOKS[@]}" \
  --customize-hook="upload '$PROJ/agent/px30-agent.py' /usr/local/bin/px30-agent.py" \
  --customize-hook="upload '$PROJ/agent/px30-update' /usr/local/sbin/px30-update" \
  --customize-hook="upload '$PROJ/agent/px30-agent.service' /etc/systemd/system/px30-agent.service" \
  --customize-hook="upload '$PROJ/agent/30-px30-agent.sudoers' /etc/sudoers.d/30-px30-agent" \
  --customize-hook="upload '$PROJ/agent/px30-agent.sysusers' /usr/lib/sysusers.d/px30-agent.conf" \
  --customize-hook="upload '$PROJ/agent/px30-agent.tmpfiles' /usr/lib/tmpfiles.d/px30-agent.conf" \
  --customize-hook="upload '$PROJ/agent/61-px30-backlight.rules' /etc/udev/rules.d/61-px30-backlight.rules" \
  --customize-hook="upload '$PROJ/agent/README.md' /usr/share/doc/px30-agent/README.md" \
  --customize-hook="upload '$PROJ/agent/px30-agent.conf.example' /usr/share/doc/px30-agent/config.json.example" \
  --customize-hook="mkdir -p \"\$1/boot\" \"\$1/usr/lib/modules\"" \
  --customize-hook="sync-in '$KOUT/boot' /boot" \
  --customize-hook="sync-in '$KOUT/lib/modules' /usr/lib/modules" \
  ${WIFI_TMP:+--customize-hook="mkdir -p \"\$1/var/lib/iwd\""} \
  ${WIFI_TMP:+--customize-hook="sync-in '$WIFI_TMP' /var/lib/iwd"} \
  ${SSH_PUBKEY:+--customize-hook="upload '$SSH_PUBKEY' /root/admin.pub"} \
  ${OTBR_DIST:+--customize-hook="sync-in '$OTBR_DIST' /"} \
  --customize-hook='set -e
    R="$1"
    # no Python bytecode caches from the PC (py_compile checks) in the image
    find "$R/usr/local" -name __pycache__ -prune -exec rm -rf {} +
    # everything that came from the PC belongs to root (sync-in copies the host uid)
    chown -R 0:0 "$R/boot" "$R/usr/lib/modules" "$R/etc" "$R/usr/local"
    # files from /mnt/c arrive as 0777 and sync-in also applies that mode to
    # existing directories (/, /etc, /usr ...) -> normalise everything from the overlay
    # (owner too: sync-in puts host uid 1000 on /, /usr ... = the kiosk user!)
    (cd '"'$OVERLAY'"' && find . -type d) | while IFS= read -r d; do chown 0:0 "$R/$d"; chmod 755 "$R/$d"; done
    chown 0:0 "$R" "$R/usr" "$R/usr/lib"
    (cd '"'$OVERLAY'"' && find . -type f) | while IFS= read -r f; do chown 0:0 "$R/$f"; chmod 644 "$R/$f"; done
    [ -d "$R/var/lib/iwd" ] && chown -R 0:0 "$R/var/lib/iwd" && chmod 700 "$R/var/lib/iwd" && chmod 600 "$R"/var/lib/iwd/* 2>/dev/null || true
    chown 0:0 "$R/etc/sudoers.d/30-px30-agent"
    chmod 440 "$R/etc/sudoers.d/10-admin" "$R/etc/sudoers.d/20-kiosk-splash" "$R/etc/sudoers.d/30-px30-agent"
    chroot "$R" visudo -c -q
    chmod 755 "$R/usr/local/bin/kiosk-start" "$R/usr/local/bin/proximity-wake.py" "$R/usr/local/sbin/reboot-android" \
      "$R/usr/local/sbin/usb-gadget-ncm" "$R/usr/local/sbin/panel-diag" "$R/usr/local/sbin/px30-ctl" \
      "$R/usr/local/sbin/px30-radio" "$R/usr/local/sbin/px30-agent-setup" "$R/usr/local/sbin/px30-update" \
      "$R/usr/local/bin/px30-agent.py" "$R/etc/update-motd.d/50-px30" "$R/usr/local/sbin/px30-panel" "$R/usr/local/sbin/px30-ssh-prepare"
    # agent + radio files (uploaded, not in the overlay)
    chown -R 0:0 "$R/usr/share/px30-radio" "$R/usr/share/doc/px30-agent" "$R/usr/local/bin/px30-agent.py" \
      "$R/usr/local/sbin/px30-update" "$R/etc/sudoers.d/30-px30-agent" "$R/etc/systemd/system/px30-agent.service" \
      "$R/usr/lib/sysusers.d/px30-agent.conf" "$R/usr/lib/tmpfiles.d/px30-agent.conf" "$R/etc/udev/rules.d/61-px30-backlight.rules"
    chmod 644 "$R"/usr/share/px30-radio/firmware/* "$R/etc/systemd/system/px30-agent.service" \
      "$R/usr/lib/sysusers.d/px30-agent.conf" "$R/usr/lib/tmpfiles.d/px30-agent.conf" "$R/etc/udev/rules.d/61-px30-backlight.rules" \
      "$R/usr/share/doc/px30-agent/README.md" "$R/usr/share/doc/px30-agent/config.json.example"
    chmod 440 "$R/etc/sudoers.d/30-px30-agent"
    chroot "$R" systemd-sysusers
    # universal-silabs-flasher in /opt/usf (px30-radio); not fatal: radio switching is optional
    if chroot "$R" python3 -m venv /opt/usf && chroot "$R" /opt/usf/bin/pip install --no-cache-dir --disable-pip-version-check -q "universal-silabs-flasher'"${USF_VERSION:+==$USF_VERSION}"'"; then
      echo "usf: $(chroot "$R" /opt/usf/bin/pip show universal-silabs-flasher | sed -n "s/^Version: //p")"
    else
      echo "WARNING: universal-silabs-flasher not installed (px30-radio switch will not work)"; rm -rf "$R/opt/usf"
    fi
    chown 0:0 "$R/usr/local/lib/px30/px30-state.sh"; chmod 644 "$R/usr/local/lib/px30/px30-state.sh"
    # users: kiosk (autologin, no password, no ssh) and admin (ssh key only)
    chroot "$R" useradd -m -s /bin/bash -G video,render,input,audio kiosk
    chroot "$R" useradd -m -s /bin/bash -G sudo,video,render,input,audio,adm admin
    chroot "$R" passwd -l root
    chroot "$R" passwd -l admin
    printf "%s\n" "[ \"\$(tty)\" = /dev/tty1 ] && exec /usr/local/bin/kiosk-start" > "$R/home/kiosk/.bash_profile"
    chroot "$R" chown kiosk:kiosk /home/kiosk/.bash_profile
    if [ -f "$R/root/admin.pub" ]; then
      install -d -m 700 "$R/home/admin/.ssh"
      install -m 600 "$R/root/admin.pub" "$R/home/admin/.ssh/authorized_keys"
      # fail-safe copy outside the home dir (sshd_config.d/10-px30.conf)
      install -d -m 755 "$R/etc/ssh/failsafe_keys"
      install -m 644 "$R/root/admin.pub" "$R/etc/ssh/failsafe_keys/admin"
      rm -f "$R/root/admin.pub"
      chroot "$R" chown -R admin:admin /home/admin/.ssh
    fi
    # services
    chroot "$R" systemctl enable iwd systemd-resolved systemd-timesyncd ssh proximity-wake \
      systemd-networkd usb-gadget px30-boot-ok.timer px30-agent px30-audio px30-panel
    # otbr-agent (OTBR_DIST): installed but NOT enabled; px30-radio (switch/apply)
    # runs exactly one radio stack, the choice lives on /data
    if [ -x "$R/usr/sbin/otbr-agent" ]; then chown 0:0 "$R/usr/sbin/otbr-agent" "$R/usr/sbin/ot-ctl"; chmod 755 "$R/usr/sbin/otbr-agent" "$R/usr/sbin/ot-ctl"; fi
    # display diagnostics only in the debug phase (PANEL_DIAG=1)
    '"$( [ "${PANEL_DIAG:-0}" = 1 ] && echo 'chroot "$R" systemctl enable panel-diag; mkdir -p "$R/var/log/journal"; printf "[Journal]\nStorage=persistent\nSystemMaxUse=64M\n" > "$R/etc/systemd/journald.conf.d/zz-debug-persistent.conf"' )"'
    # no automatic apt runs on a kiosk with 1 GB RAM and eMMC; no daily dpkg
    # database backup either (the image is replaced as a whole, A/B)
    chroot "$R" systemctl disable apt-daily.timer apt-daily-upgrade.timer dpkg-db-backup.timer
    # boot animation: kiosk-start stops Plymouth itself right before Cog starts
    # (otherwise it stops at multi-user and you see a frozen frame or tty1)
    chroot "$R" systemctl mask plymouth-quit.service plymouth-quit-wait.service
    # check: nothing in / or /usr may still belong to a regular user
    bad="$(find "$R" -xdev \( -path "$R/home" -o -path "$R/proc" -o -path "$R/sys" \) -prune -o \( -uid 1000 -o -uid 1001 -o -gid 1000 -o -gid 1001 \) -print | head -5)"
    [ -z "$bad" ] || { echo "ERROR: non-root owner on: $bad"; exit 1; }
    ln -sf /run/systemd/resolve/stub-resolv.conf "$R/etc/resolv.conf"
    # ssh host keys only at first boot (otherwise all images share the same keys)
    rm -f "$R"/etc/ssh/ssh_host_*
    mkdir -p "$R/etc/systemd/system/ssh.service.d"
    # create the keys BEFORE Debian'"'"'s own "sshd -t" check (otherwise ssh.service fails)
    # keys on /data: kept across reinstalls (see sshd_config.d/10-px30.conf)
    printf "[Service]\nExecStartPre=\nExecStartPre=/usr/bin/mkdir -p /data/ssh/etc/ssh\nExecStartPre=/usr/bin/ssh-keygen -A -f /data/ssh\nExecStartPre=/usr/local/sbin/px30-ssh-prepare\nExecStartPre=/usr/sbin/sshd -t\n" > "$R/etc/systemd/system/ssh.service.d/hostkeys.conf"
    # modules-index
    chroot "$R" depmod -a '"$KVER"'
    # U-Boot distro-boot menu
    mkdir -p "$R/boot/extlinux"
    cat > "$R/boot/extlinux/extlinux.conf" <<EOT
default px30
timeout 3
label px30
    kernel /boot/Image
    fdt /boot/px30-smatek-wallpanel.dtb
    append '"$KCMDLINE"'
EOT
    # machine-id empty: created at first boot
    : > "$R/etc/machine-id"
  ' \
  "$SUITE" "$DEST/px30-rootfs.tar" http://deb.debian.org/debian

echo "--- tarball: $(du -h "$DEST/px30-rootfs.tar" | cut -f1)"
# ext4 image: unpack in a user namespace (subuid mapping) so the owners are
# right, then mke2fs -d on that directory. No root or libarchive needed.
rm -f "$DEST/px30-rootfs.ext4"
unshare --map-auto --map-root-user --mount bash -euc '
  T="$(mktemp -d)"
  # /dev nodes cannot be created without root; systemd mounts /dev itself
  tar -C "$T" --numeric-owner --exclude="./dev/*" -xpf "$1"
  mke2fs -q -t ext4 -L px30-root -E root_owner=0:0 -d "$T" "$2" "$3"
  rm -rf "$T"
' _ "$DEST/px30-rootfs.tar" "$DEST/px30-rootfs.ext4" "$IMG_SIZE"
echo "--- ext4 image: $DEST/px30-rootfs.ext4 ($IMG_SIZE, label px30-root)"
