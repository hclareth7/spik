"""Tests for the nonverbal (vision) aggregation math — pure, no cv2/mediapipe/network.

The heavy per-frame detection stage is bypassed: we build ``Observation`` sequences by hand
(as the real sampler would) and assert the aggregation (ratios, event counts, rates, stability)
and the graceful no-detection path. Thresholds live in ``spik.vision`` as module constants, so
these tests pin the exact behavior at those thresholds.
"""

from __future__ import annotations

import math

from spik import vision
from spik.vision import Observation


# ============================================================================
# _matrix_to_yaw_pitch (head-orientation proxy)
# ============================================================================
def test_matrix_identity_is_frontal():
    identity = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    yaw, pitch = vision._matrix_to_yaw_pitch(identity)
    assert abs(yaw) < 1e-6
    assert abs(pitch) < 1e-6


def test_matrix_yaw_rotation():
    # Rotation about the Y axis by 30° -> yaw ~30, pitch ~0.
    t = math.radians(30)
    r = [
        [math.cos(t), 0, math.sin(t), 0],
        [0, 1, 0, 0],
        [-math.sin(t), 0, math.cos(t), 0],
        [0, 0, 0, 1],
    ]
    yaw, pitch = vision._matrix_to_yaw_pitch(r)
    assert abs(yaw - 30.0) < 1e-6
    assert abs(pitch) < 1e-6


# ============================================================================
# _rising_edges (blink / brow event counting)
# ============================================================================
def test_rising_edges_counts_low_to_high():
    assert vision._rising_edges([0.1, 0.6, 0.1, 0.6], 0.5) == 2
    assert vision._rising_edges([0.6, 0.6, 0.6], 0.5) == 1  # one continuous run
    assert vision._rising_edges([0.1, 0.2, 0.3], 0.5) == 0


def test_rising_edges_none_breaks_the_run():
    # A None (undetected) sample resets the state, so a gap does not fabricate an edge.
    assert vision._rising_edges([0.6, None, 0.6], 0.5) == 2


# ============================================================================
# _aggregate (the full metric computation)
# ============================================================================
def _sample_observations() -> list[Observation]:
    """Four sampled frames (t = 0/200/400/600 ms) with hand-chosen signals."""
    return [
        Observation(
            t_ms=0, face=True, smile=0.5, brow=0.1, blink=0.1, yaw=5, pitch=5,
            head_cx=0.5, head_cy=0.5, pose=True, shoulder_tilt=0.05, neck_ratio=0.6,
            wrist_cx=0.5, wrist_cy=0.5, hands_visible=True,
        ),
        Observation(
            t_ms=200, face=True, smile=0.5, brow=0.5, blink=0.6, yaw=5, pitch=5,
            head_cx=0.5, head_cy=0.5, pose=True, shoulder_tilt=0.05, neck_ratio=0.6,
            wrist_cx=0.5, wrist_cy=0.5, hands_visible=True,
        ),
        Observation(
            t_ms=400, face=True, smile=0.1, brow=0.1, blink=0.1, yaw=30, pitch=5,
            head_cx=0.5, head_cy=0.5, pose=True, shoulder_tilt=0.30, neck_ratio=0.6,
            wrist_cx=0.6, wrist_cy=0.5, hands_visible=True,
        ),
        Observation(
            t_ms=600, face=True, smile=0.5, brow=0.5, blink=0.6, yaw=5, pitch=5,
            head_cx=0.5, head_cy=0.5, pose=True, shoulder_tilt=0.05, neck_ratio=0.1,
            wrist_cx=0.5, wrist_cy=0.5, hands_visible=True,
        ),
    ]


def test_aggregate_full_math():
    m = vision._aggregate(_sample_observations(), duration_s=60.0)  # minutes = 1.0
    assert m.frames_analyzed == 4
    assert m.face_detected_ratio == 1.0
    assert m.pose_detected_ratio == 1.0
    # smiles >= 0.30: frames 1,2,4 -> 3/4
    assert m.smile_ratio == 0.75
    # eye contact (|yaw|<=20 and |pitch|<=15): frames 1,2,4 -> 3/4
    assert m.eye_contact_ratio == 0.75
    # blink rising edges: 0.1,0.6,0.1,0.6 -> 2; rate per min (minutes=1) -> 2.0
    assert m.blink_rate_per_min == 2.0
    # brow rising edges: 0.1,0.5,0.1,0.5 -> 2
    assert m.brow_raise_events == 2
    # posture upright: tilt<=0.18 AND neck>=0.45 -> frames 1,2 -> 2/4
    assert m.posture_upright_ratio == 0.5
    assert m.slouch_ratio == 0.5
    # head never moves -> perfect stability
    assert m.head_stability == 1.0
    # one gesture burst (wrist jumps 0.5->0.6 then 0.6->0.5, both fast, one rising edge)
    assert m.gesture_rate_per_min == 1.0
    assert m.hands_visible_ratio == 1.0
    # expressiveness (smile+brow) = [0.6,1.0,0.2,1.0]; none below 0.05
    assert m.flat_affect_ratio == 0.0
    assert m.expression_variability > 0.0
    assert m.notes == []


def test_aggregate_no_detection_is_graceful():
    # Frames where nothing was detected -> all-zero metrics + explanatory notes, no exception.
    obs = [Observation(t_ms=0), Observation(t_ms=200)]
    m = vision._aggregate(obs, duration_s=10.0)
    assert m.frames_analyzed == 2
    assert m.face_detected_ratio == 0.0
    assert m.pose_detected_ratio == 0.0
    assert m.smile_ratio == 0.0
    assert m.eye_contact_ratio == 0.0
    assert m.gesture_rate_per_min == 0.0
    assert "no face detected" in m.notes
    assert "no pose/body detected" in m.notes


def test_aggregate_zero_duration_no_divide_by_zero():
    m = vision._aggregate(_sample_observations(), duration_s=0.0)
    assert m.blink_rate_per_min == 0.0
    assert m.gesture_rate_per_min == 0.0


# ============================================================================
# analyze() with an injected observer (no cv2/mediapipe)
# ============================================================================
def test_analyze_uses_injected_observer():
    captured = {}

    def fake_observe(path):
        captured["path"] = path
        return _sample_observations(), 60.0

    out = vision.analyze("/some/video.mkv", observe=fake_observe)
    assert isinstance(out, dict)
    assert out["frames_analyzed"] == 4
    assert out["smile_ratio"] == 0.75
    assert str(captured["path"]).endswith("video.mkv")
    assert "notes" in out
