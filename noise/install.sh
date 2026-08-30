#!/usr/bin/env bash
# Register the "Speak Clean Mic" filter in PipeWire (no sudo) and set up the toggle.
#
# What it does:
#   1) Links the speak-clean-mic.conf fragment into ~/.config/pipewire/filter-chain.conf.d/
#      (loaded by the `filter-chain.service` unit that Fedora already ships).
#   2) Adds a systemd drop-in so the unit finds librnnoise_ladspa.so in
#      ~/.local/lib/ladspa (via LADSPA_PATH), without touching system paths.
#   3) Reloads the user systemd and prints how to activate/use the filter.
#
# Prerequisite: having run `bash noise/build-rnnoise-ladspa.sh`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRAGMENT_SRC="${SCRIPT_DIR}/speak-clean-mic.conf"
LADSPA_SO="${HOME}/.local/lib/ladspa/librnnoise_ladspa.so"

CONF_DIR="${HOME}/.config/pipewire/filter-chain.conf.d"
UNIT_DROPIN_DIR="${HOME}/.config/systemd/user/filter-chain.service.d"

if [[ ! -f "$LADSPA_SO" ]]; then
    echo "ERROR: $LADSPA_SO does not exist" >&2
    echo "       Run first:  bash ${SCRIPT_DIR}/build-rnnoise-ladspa.sh" >&2
    exit 1
fi

echo "== 1) Registering the filter fragment =="
mkdir -p "$CONF_DIR"
ln -sf "$FRAGMENT_SRC" "$CONF_DIR/speak-clean-mic.conf"
echo "  ${CONF_DIR}/speak-clean-mic.conf -> $FRAGMENT_SRC"

echo "== 2) Exporting LADSPA_PATH to the filter-chain.service unit =="
mkdir -p "$UNIT_DROPIN_DIR"
cat > "$UNIT_DROPIN_DIR/10-speak-ladspa.conf" <<'EOF'
[Service]
# Lets filter-chain.service find the RNNoise plugin installed in HOME.
Environment=LADSPA_PATH=%h/.local/lib/ladspa
EOF
echo "  ${UNIT_DROPIN_DIR}/10-speak-ladspa.conf"

echo "== 3) Reloading the user systemd =="
systemctl --user daemon-reload

cat <<EOF

✓ Installed.

Activate the filter (creates the "Speak Clean Mic" source):
    systemctl --user start filter-chain.service
Make it start on every login:
    systemctl --user enable filter-chain.service
Turn it off:
    systemctl --user stop filter-chain.service

Verify the source appears:
    wpctl status | grep -i "Speak Clean Mic"

Use it as the default microphone (Chrome/Zoom will pick it up automatically):
    wpctl set-default \$(pactl list sources short | awk '/speak_clean_mic/{print \$1; exit}')

From the Speak GUI ("Checker" tab) you can toggle it on/off and set it as default
with one click — it runs exactly these commands under the hood.
EOF
