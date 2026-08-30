#!/usr/bin/env bash
# Record video + audio in a REPRODUCIBLE and PORTABLE way, without a GUI. Ctrl-C / SIGINT to stop.
#
# Picks the best video strategy based on what the camera supports (max quality, min CPU):
#   1) H.264 on the sensor  -> -c:v copy          (0 CPU, no re-encoding; ideal)
#   2) MJPEG on the sensor  -> -c:v copy          (0 CPU; good quality)
#   3) Raw (YUYV, etc.)     -> libx264 -crf 18    (re-encodes on CPU; high visual quality)
# Audio is captured via PipeWire/PulseAudio (-f pulse), NOT via ALSA hw:.
#
# Usage:  ./capture/record.sh <output.mkv> [audio_source] [video_device]
#   audio_source:  PipeWire/Pulse source name (list: `pactl list sources short`).
#                  If omitted, uses the default source (`pactl get-default-source`).
#   video_device:  defaults to /dev/video0.
set -euo pipefail

out="${1:-}"
audio_src="${2:-}"
video_dev="${3:-/dev/video0}"

if [[ -z "$out" ]]; then
  echo "Usage: $0 <output.mkv> [audio_source] [video_device]" >&2
  echo >&2
  echo "Audio sources (PipeWire/Pulse):" >&2
  pactl list sources short >&2 2>/dev/null || true
  exit 1
fi

# Default audio source = the system default (portable across machines).
if [[ -z "$audio_src" ]]; then
  audio_src="$(pactl get-default-source 2>/dev/null || true)"
  if [[ -z "$audio_src" ]]; then
    echo "Could not determine the default audio source. Pass it as the 2nd argument." >&2
    exit 1
  fi
fi

# Warn if another app (e.g. the browser) is holding the camera.
if command -v fuser >/dev/null && fuser "$video_dev" >/dev/null 2>&1; then
  echo "WARNING: $video_dev is in use by another app (close tabs/apps using the camera)." >&2
fi

# --- Probe the formats supported by the camera ---
formats="$(ffmpeg -hide_banner -f v4l2 -list_formats all -i "$video_dev" 2>&1 || true)"

# Print the LARGEST resolution (AxB) listed for the given format pattern (or empty).
pick_size() {
  echo "$formats" | grep -iE "$1" | grep -oE '[0-9]+x[0-9]+' \
    | sort -t x -k1,1n -k2,2n | tail -n1
}

vid_args=()
enc_args=()
if echo "$formats" | grep -qiE '\bh264\b'; then
  size="$(pick_size '\bh264\b')"
  vid_args=(-f v4l2 -input_format h264 ${size:+-video_size "$size"} -i "$video_dev")
  enc_args=(-c:v copy)
  echo "video: H.264 on camera ${size:+($size) }-> copy (0 CPU)"
elif echo "$formats" | grep -qiE '\bmjpeg\b'; then
  size="$(pick_size '\bmjpeg\b')"
  vid_args=(-f v4l2 -input_format mjpeg ${size:+-video_size "$size"} -i "$video_dev")
  enc_args=(-c:v copy)
  echo "video: MJPEG on camera ${size:+($size) }-> copy (0 CPU)"
else
  size="$(pick_size '[0-9]+x[0-9]+')"
  vid_args=(-f v4l2 ${size:+-video_size "$size"} -i "$video_dev")
  enc_args=(-c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p)
  echo "video: raw ${size:+($size) }-> x264 CRF 18 (re-encodes on CPU)"
fi

echo "audio: $audio_src (48 kHz, mono, FLAC, via PipeWire/Pulse)"
echo "Ctrl-C to stop."

# SIGINT (sent by /api/record/stop) makes ffmpeg close the container cleanly.
exec ffmpeg -hide_banner \
  "${vid_args[@]}" \
  -f pulse -i "$audio_src" \
  "${enc_args[@]}" \
  -ac 1 -ar 48000 -c:a flac \
  "$out"
