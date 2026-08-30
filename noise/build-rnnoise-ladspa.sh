#!/usr/bin/env bash
# Build the RNNoise LADSPA wrapper (librnnoise_ladspa.so) just ONCE.
#
# Why it's needed: Fedora's `rnnoise` package only ships the base library
# (librnnoise.so), NOT the LADSPA wrapper that PipeWire needs for the filter-chain, and
# `noise-suppression-for-voice` is not in the repos. So we build it from source.
#
# Does NOT require sudo: installs the .so into ~/.local/lib/ladspa/ (inside your HOME).
# Idempotent: if the .so already exists, it does not rebuild (use --force to rebuild).
#
# Requirements (already present on this machine): git, cmake, gcc/g++, make.
set -euo pipefail

REPO_URL="https://github.com/werman/noise-suppression-for-voice.git"
DEST_DIR="${HOME}/.local/lib/ladspa"
DEST_SO="${DEST_DIR}/librnnoise_ladspa.so"
BUILD_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}/speak/rnnoise-build"

if [[ "${1:-}" == "--force" ]]; then
    rm -f "$DEST_SO"
fi

if [[ -f "$DEST_SO" ]]; then
    echo "✓ Already exists: $DEST_SO (use '$0 --force' to rebuild)."
    exit 0
fi

for tool in git cmake make cc; do
    command -v "$tool" >/dev/null || { echo "ERROR: missing '$tool'. Install it and retry." >&2; exit 1; }
done

echo "== Cloning/updating $REPO_URL =="
mkdir -p "$BUILD_ROOT"
if [[ -d "$BUILD_ROOT/src/.git" ]]; then
    git -C "$BUILD_ROOT/src" pull --ff-only --recurse-submodules
    git -C "$BUILD_ROOT/src" submodule update --init --recursive
else
    git clone --recurse-submodules "$REPO_URL" "$BUILD_ROOT/src"
fi

echo "== Building (cmake + make) =="
cmake -S "$BUILD_ROOT/src" -B "$BUILD_ROOT/build" -DCMAKE_BUILD_TYPE=Release
# Only the target we need: the LADSPA wrapper. Avoids building the test targets
# (e.g. common_plugin_tests), which link against the UBSan runtime
# (libubsan) — not always installed on Fedora and unrelated to the plugin we use.
cmake --build "$BUILD_ROOT/build" --parallel --target rnnoise_ladspa

echo "== Installing the plugin (no sudo) into $DEST_DIR =="
built_so="$(find "$BUILD_ROOT/build" -name 'librnnoise_ladspa.so' -print -quit)"
if [[ -z "$built_so" ]]; then
    echo "ERROR: could not find librnnoise_ladspa.so after building. Check the output above." >&2
    exit 1
fi
mkdir -p "$DEST_DIR"
install -m 0755 "$built_so" "$DEST_SO"

echo
echo "✓ Done: $DEST_SO"
echo "  Next step: bash noise/install.sh   (registers the filter and explains how to activate it)"
