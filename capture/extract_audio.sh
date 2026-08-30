#!/usr/bin/env bash
# Extract audio from a recording for the analysis pipeline.
# Output: mono WAV, 16 kHz, PCM 16-bit  (optimal format for Whisper).
#
# Usage:  ./capture/extract_audio.sh <video> [output.wav]
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <video> [output.wav]" >&2
  exit 1
fi

in="$1"
if [[ ! -f "$in" ]]; then
  echo "Error: file '$in' does not exist" >&2
  exit 1
fi

# Default output: same name with .wav extension
out="${2:-${in%.*}.wav}"

echo "Extracting audio: $in -> $out"
ffmpeg -y -i "$in" -vn -ac 1 -ar 16000 -c:a pcm_s16le "$out"
echo "Done: $out"
