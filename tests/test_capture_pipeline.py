"""Tests for the single-owner capture command builder (web/capture_pipeline.py).

These are pure argument-list assertions: no ffmpeg is launched and no camera is opened.
They pin the codec-selection logic ported from capture/record.sh (H.264 copy > MJPEG copy
> raw x264) and the per-sink output topology.
"""

from __future__ import annotations

import pytest

from web.capture_pipeline import (
    DENOISE_PRESETS,
    FILTER_DEFAULT,
    FILTER_RANGES,
    SINK_PREVIEW,
    SINK_RECORD,
    SINK_VCAM,
    build_capture_cmd,
    build_filter_chain,
    pick_input_args,
)

# Representative `ffmpeg -list_formats` fragments.
_H264 = "[video4linux2] Raw       : yuyv422 : 640x480\n[video4linux2] Compressed : h264 : 1280x720 1920x1080"
_MJPEG = "[video4linux2] Compressed : mjpeg : 1280x720 640x480"
_RAW = "[video4linux2] Raw : yuyv422 : 640x480 1280x720"


def test_pick_input_args_prefers_h264_copy():
    input_args, enc = pick_input_args(_H264, "/dev/video4")
    assert enc == ["-c:v", "copy"]
    assert "-input_format" in input_args and "h264" in input_args
    # Largest H.264 resolution wins.
    assert "1920x1080" in input_args


def test_pick_input_args_uses_mjpeg_copy():
    input_args, enc = pick_input_args(_MJPEG, "/dev/video4")
    assert enc == ["-c:v", "copy"]
    assert "mjpeg" in input_args
    assert "1280x720" in input_args


def test_pick_input_args_falls_back_to_x264_for_raw():
    input_args, enc = pick_input_args(_RAW, "/dev/video4")
    assert enc[:2] == ["-c:v", "libx264"]
    assert "-input_format" not in input_args  # raw: no compressed input format requested
    assert "1280x720" in input_args           # still picks the largest raw size


def test_build_preview_only_has_pipe_and_no_audio():
    cmd = build_capture_cmd(device="/dev/video4", sinks={SINK_PREVIEW}, formats=_MJPEG)
    # The device is opened exactly once.
    assert cmd.count("/dev/video4") == 1
    assert "pipe:1" in cmd
    # Preview is video-only: no pulse audio input.
    assert "pulse" not in cmd
    assert "-map" in cmd and "1:a" not in cmd


def test_build_record_maps_audio_only_to_the_file():
    cmd = build_capture_cmd(
        device="/dev/video4", sinks={SINK_RECORD}, formats=_MJPEG,
        out_path="/data/out.mkv", audio_source="mysrc",
    )
    assert cmd.count("/dev/video4") == 1
    assert "-f" in cmd and "pulse" in cmd and "mysrc" in cmd
    assert "1:a" in cmd and "flac" in cmd and cmd[-1] == "/data/out.mkv"
    # Record-only: no browser preview pipe.
    assert "pipe:1" not in cmd


def test_build_preview_and_record_share_one_device_open():
    cmd = build_capture_cmd(
        device="/dev/video4", sinks={SINK_PREVIEW, SINK_RECORD}, formats=_H264,
        out_path="/data/out.mkv", audio_source="mysrc",
    )
    # Still ONE camera open feeding both sinks (the whole point of Option A).
    assert cmd.count("/dev/video4") == 1
    assert "pipe:1" in cmd             # preview branch
    assert "/data/out.mkv" in cmd      # record branch
    assert "-c:v" in cmd and "copy" in cmd  # H.264 sensor => copy to file


def test_build_rejects_empty_sinks():
    with pytest.raises(ValueError):
        build_capture_cmd(device="/dev/video4", sinks=set(), formats=_MJPEG)


def test_build_rejects_record_without_audio_or_path():
    with pytest.raises(ValueError):
        build_capture_cmd(device="/dev/video4", sinks={SINK_RECORD}, formats=_MJPEG)


# ---------------------------------------------------------------------------
# Speak Cam: filter chain + vcam (v4l2loopback) sink
# ---------------------------------------------------------------------------
def test_filter_chain_neutral_is_just_eq_and_format():
    # No filters -> neutral eq (all no-op values) + format; no denoise, no sharpen.
    chain = build_filter_chain(None)
    assert chain == "eq=brightness=0:contrast=1:gamma=1:saturation=1,format=yuv420p"


def test_filter_chain_order_denoise_color_sharpen_format():
    chain = build_filter_chain({
        "denoise": "strong", "brightness": 0.05, "contrast": 1.1,
        "gamma": 1.0, "saturation": 1.2, "sharpness": 0.8,
    })
    assert chain == (
        "hqdn3d=6:4:9:6,"
        "eq=brightness=0.05:contrast=1.1:gamma=1:saturation=1.2,"
        "unsharp=5:5:0.8,"
        "format=yuv420p"
    )


def test_filter_chain_omits_sharpen_at_zero_and_denoise_off():
    chain = build_filter_chain({"denoise": "off", "sharpness": 0})
    assert "unsharp" not in chain
    assert "hqdn3d" not in chain


def test_filter_chain_without_format_for_preview():
    # The preview branch handles its own pixel format (MJPEG); no trailing format=yuv420p.
    chain = build_filter_chain(None, include_format=False)
    assert "format=yuv420p" not in chain


def test_build_vcam_writes_filtered_branch_to_loopback():
    cmd = build_capture_cmd(
        device="/dev/video4", sinks={SINK_VCAM}, formats=_MJPEG,
        filters={"brightness": 0.1}, vcam_device="/dev/video10",
    )
    assert cmd.count("/dev/video4") == 1        # real camera opened once
    assert "/dev/video10" in cmd                # loopback output present
    assert "v4l2" in cmd and "yuv420p" in cmd
    # The filter chain reaches the vcam branch via -vf.
    vf = cmd[cmd.index("-vf") + 1]
    assert "eq=brightness=0.1" in vf


def test_build_vcam_and_preview_share_one_open_and_mirror_filters():
    cmd = build_capture_cmd(
        device="/dev/video4", sinks={SINK_VCAM, SINK_PREVIEW}, formats=_MJPEG,
        filters={"saturation": 1.5}, vcam_device="/dev/video10",
    )
    assert cmd.count("/dev/video4") == 1        # one real-camera open feeds both
    assert "/dev/video10" in cmd and "pipe:1" in cmd
    # Both -vf branches carry the same filter (WYSIWYG preview): vcam + preview.
    assert " ".join(cmd).count("saturation=1.5") == 2


def test_build_rejects_vcam_without_device():
    with pytest.raises(ValueError):
        build_capture_cmd(device="/dev/video4", sinks={SINK_VCAM}, formats=_MJPEG)


def test_filter_default_is_within_ranges():
    """The "podcast studio" preset must stay inside the validated slider ranges and use a
    real denoise preset — otherwise vcam.py would reject its own seeded defaults (400)."""
    for name, (lo, hi) in FILTER_RANGES.items():
        value = FILTER_DEFAULT[name]
        assert isinstance(value, (int, float)), f"{name} default must be numeric"
        assert lo <= value <= hi, f"{name}={value} outside [{lo}, {hi}]"
    assert FILTER_DEFAULT["denoise"] in DENOISE_PRESETS


def test_filter_default_builds_full_studio_chain():
    """The studio preset exercises every filter branch: denoise + color + sharpen + format."""
    chain = build_filter_chain(FILTER_DEFAULT)
    assert chain.startswith("hqdn3d=")          # light denoise present
    assert "eq=brightness=0.05" in chain        # color grade present
    assert "unsharp=5:5:0.7" in chain           # sharpen present (sharpness > 0)
    assert chain.endswith("format=yuv420p")     # loopback-ready pixel format


# --- Real-time latency caps for the virtual camera ---

# Formats where the sensor advertises both a capped (<=1280) mode and a huge one.
_H264_BIG = "[video4linux2] Compressed : h264 : 1280x720 2560x1440"
# Formats where the ONLY h264 mode exceeds the cap (forces native fallback + scale filter).
_H264_HUGE_ONLY = "[video4linux2] Compressed : h264 : 2560x1440"


def _vcam_cmd(formats, *, max_width=1280, fps=30):
    return build_capture_cmd(
        device="/dev/video4", sinks={SINK_VCAM}, formats=formats,
        filters=FILTER_DEFAULT, vcam_device="/dev/video10",
        max_width=max_width, fps=fps,
    )


def test_vcam_vf_starts_with_scale():
    """Scale is the FIRST filter so hqdn3d/unsharp run on the downscaled frame (the latency win)."""
    cmd = _vcam_cmd(_H264_BIG)
    vf = cmd[cmd.index("-vf") + 1]
    assert vf.startswith("scale=min(1280\\,iw):-2,")
    assert vf.index("scale") < vf.index("hqdn3d")


def test_vcam_has_fps_cap():
    cmd = _vcam_cmd(_H264_BIG)
    assert "-r" in cmd and cmd[cmd.index("-r") + 1] == "30"


def test_vcam_has_low_latency_input_flags():
    """nobuffer/low_delay are INPUT flags — they must precede the -i on the real camera."""
    cmd = _vcam_cmd(_H264_BIG)
    assert "nobuffer" in cmd and "low_delay" in cmd
    assert cmd.index("nobuffer") < cmd.index("-i")
    assert cmd.index("low_delay") < cmd.index("-i")


def test_vcam_input_size_capped_when_mode_available():
    """A real <=1280 h264 mode is requested outright, avoiding a 1440p decode."""
    cmd = _vcam_cmd(_H264_BIG)
    assert "1280x720" in cmd
    assert "2560x1440" not in cmd


def test_vcam_input_falls_back_to_native_when_no_capped_mode():
    """No <=1280 mode: request native size, and the scale filter still downsizes it."""
    cmd = _vcam_cmd(_H264_HUGE_ONLY)
    assert "2560x1440" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    assert vf.startswith("scale=min(1280\\,iw):-2,")


def test_preview_branch_unchanged_by_caps():
    """Preview keeps its own trailing scale and its own -r, and gets no vcam size cap.

    Preview-only DOES get the low-latency demuxer flags + per-frame pipe flush (a preview with
    no recording sharing the input), but never the vcam ``scale=min(...)`` input cap.
    """
    cmd = build_capture_cmd(
        device="/dev/video4", sinks={SINK_PREVIEW}, formats=_MJPEG,
        max_width=1280, fps=15,
    )
    vf = cmd[cmd.index("-vf") + 1]
    assert vf == "scale=1280:-2:flags=lanczos"   # no leading scale=min(...), unchanged
    assert cmd[cmd.index("-r") + 1] == "30"      # preview's own cap, not the caps fps (15)
    # Low-latency preview: demuxer flags on the input, per-frame flush on the pipe output.
    assert "nobuffer" in cmd and "low_delay" in cmd
    assert cmd[cmd.index("-flush_packets") + 1] == "1"


def test_record_branch_unchanged_by_caps():
    """Recording keeps full-resolution -c:v copy and gets no cap/latency flags."""
    cmd = build_capture_cmd(
        device="/dev/video4", sinks={SINK_RECORD}, formats=_H264,
        out_path="/tmp/out.mkv", audio_source="mic", max_width=1280, fps=30,
    )
    assert "-c:v" in cmd and "copy" in cmd
    assert "1920x1080" in cmd                     # largest h264 mode, not capped
    assert "nobuffer" not in cmd and "low_delay" not in cmd
