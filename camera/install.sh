#!/usr/bin/env bash
# Provision the "Speak Cam" virtual webcam (v4l2loopback) — ONE-TIME, needs sudo/kernel.
#
# Unlike noise/install.sh (pure `systemd --user`, no root), this installs an out-of-tree
# KERNEL MODULE and writes to /etc, so it MUST run as root. The spik app itself NEVER runs
# modprobe/sudo at runtime — it only *writes filtered frames* to the /dev/video node this
# script provisions (the same way the noise filter only *uses* an already-loaded RNNoise).
#
# What it does (all as root):
#   1) Installs akmod-v4l2loopback from RPM Fusion (akmod auto-rebuilds after kernel updates).
#   2) Pins the virtual device at /dev/video10 labelled "Speak Cam" via /etc/modprobe.d.
#   3) Loads it on every boot via /etc/modules-load.d.
#   4) Loads it now (no reboot needed) and verifies it appears.
#
# v4l2         = Video4Linux2, the Linux kernel video API for /dev/video* nodes.
# v4l2loopback = out-of-tree kernel module exposing a VIRTUAL /dev/video that apps read as a
#                webcam and one writer feeds. Unlike a real camera it allows MANY readers.
# akmod        = Fedora packaging that REBUILDS the module automatically after a kernel update.
set -euo pipefail

VIDEO_NR=10                       # fixes /dev/video10 (stable for regex/validation/pickers)
CARD_LABEL="Speak Cam"            # friendly name read back from /sys/class/video4linux/*/name
DEVICE="/dev/video${VIDEO_NR}"
MODPROBE_CONF="/etc/modprobe.d/speak-cam.conf"
MODULES_CONF="/etc/modules-load.d/speak-cam.conf"

if [[ "${EUID}" -ne 0 ]]; then
    echo "This script needs root (kernel module + /etc). Re-run with: sudo bash camera/install.sh" >&2
    exit 1
fi

echo "== 1) Installing akmod-v4l2loopback (RPM Fusion) =="
if ! rpm -q akmod-v4l2loopback >/dev/null 2>&1; then
    # RPM Fusion free must be enabled; see the README if this fails.
    dnf install -y akmod-v4l2loopback v4l2loopback-utils || {
        echo "ERROR: could not install akmod-v4l2loopback. Enable RPM Fusion first:" >&2
        echo "  sudo dnf install https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-\$(rpm -E %fedora).noarch.rpm" >&2
        exit 1
    }
else
    echo "  already installed."
fi

echo "== 2) Pinning ${DEVICE} labelled \"${CARD_LABEL}\" (${MODPROBE_CONF}) =="
# exclusive_caps=1 => the node advertises capture caps only, required for Chrome/Zoom/Meet
# to list it as a webcam. video_nr fixes the number; card_label sets the friendly name.
cat > "${MODPROBE_CONF}" <<EOF
options v4l2loopback exclusive_caps=1 video_nr=${VIDEO_NR} card_label="${CARD_LABEL}"
EOF
echo "  ${MODPROBE_CONF}"

echo "== 3) Loading on boot (${MODULES_CONF}) =="
echo "v4l2loopback" > "${MODULES_CONF}"
echo "  ${MODULES_CONF}"

echo "== 4) Loading now (no reboot) =="
# Reload cleanly so the /etc/modprobe.d options take effect even if it was already loaded.
modprobe -r v4l2loopback 2>/dev/null || true
modprobe v4l2loopback exclusive_caps=1 "video_nr=${VIDEO_NR}" "card_label=${CARD_LABEL}"

echo
if [[ -e "${DEVICE}" ]]; then
    NAME="$(cat "/sys/class/video4linux/video${VIDEO_NR}/name" 2>/dev/null || echo '?')"
    echo "✓ Installed. ${DEVICE} is up (name: ${NAME})."
    echo
    echo "Verify:   v4l2-ctl --list-devices    # look for \"${CARD_LABEL}\""
    echo "Use it:   turn on \"Speak Cam\" in spik (Checker tab), then pick \"${CARD_LABEL}\""
    echo "          as your webcam in Zoom/Meet/OBS."
else
    echo "WARNING: ${DEVICE} did not appear. After a kernel update akmod may still be" >&2
    echo "         rebuilding the module; wait a minute or reboot, then re-run this script." >&2
    exit 1
fi
