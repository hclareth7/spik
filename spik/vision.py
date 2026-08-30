"""Nonverbal metrics: gestures, posture, facial expression and eye-contact proxy.

Phase 3. Runs 100% LOCALLY on CPU with MediaPipe (Google's on-device ML vision library):
- FaceLandmarker (blendshapes + facial transformation matrix) -> expressions, blink, gaze proxy
- PoseLandmarker (lite) -> posture and hand/arm movement (wrists come from the pose, so no
  separate HandLandmarker is loaded -> saves CPU)

Privacy ("todo local"): frames are read straight from the recorded video, converted to
landmarks/blendshapes (numbers), aggregated, and then DISCARDED. Only the aggregated NUMBERS
persist (metrics_json) and are sent to Claude. No frame/image ever leaves this function.

Honesty about what is measured (surfaced verbatim in the UI/prompt):
- "eye contact" is a HEAD-ORIENTATION proxy (yaw/pitch near frontal), not true gaze tracking.
- "expression" = observable blendshape coefficients (smile, brow-raise, blink), NOT emotion.
- "posture" is a shoulder-levelness + head-over-shoulders heuristic proxy.

Heavy deps (``cv2``/``mediapipe``) are imported lazily INSIDE functions so the package (and the
test suite) imports without the ``[vision]`` extra installed. Install with:
``pip install -e ".[vision]"``.
"""

from __future__ import annotations

import math
import statistics
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from . import config

# ---------------------------------------------------------------------------
# Model assets (public MediaPipe Tasks models). Downloading a public model does NOT
# violate "todo local" — it is the same pattern WhisperX uses to pull its weights. The
# recorded VIDEO never leaves the machine; only the model file is fetched (once, cached).
# ---------------------------------------------------------------------------
_MODELS: dict[str, tuple[str, int]] = {
    # name: (url, minimum plausible size in bytes — a corrupt/HTML error page is far smaller)
    "face_landmarker": (
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
        "face_landmarker/float16/1/face_landmarker.task",
        1_000_000,
    ),
    "pose_landmarker": (
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
        "pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
        1_000_000,
    ),
}

# ---------------------------------------------------------------------------
# Detection thresholds (heuristics; documented as proxies). Kept as module constants so the
# aggregation math is deterministic and unit-testable with injected observations.
# ---------------------------------------------------------------------------
SMILE_THRESHOLD = 0.30          # blendshape score above which a frame counts as "smiling"
FLAT_EXPRESSION_THRESHOLD = 0.05  # combined expressiveness below this = "flat affect"
# eyeBlink hovers near ~0.5 for many faces (half-lidded baseline); a genuine blink spikes
# higher, so 0.6 avoids counting baseline noise as blinks. NOTE: at low sampling FPS a blink
# (~120 ms) is often shorter than the frame interval, so blink rate is a ROUGH indicator.
BLINK_THRESHOLD = 0.60          # eyeBlink score crossing this (rising edge) = one blink
BLINK_LOW = 0.40                # must drop below this to re-arm (hysteresis vs. flicker noise)
BROW_THRESHOLD = 0.40           # browOuterUp score crossing this (rising edge) = one brow raise
EYE_CONTACT_YAW_DEG = 20.0      # |head yaw| within this = looking roughly at the camera
EYE_CONTACT_PITCH_DEG = 15.0    # |head pitch| within this = looking roughly at the camera
SHOULDER_TILT_MAX = 0.18        # |shoulder dy| / shoulder-width below this = shoulders level
# (shoulder_y - nose_y)/shoulder-width above this = head held up over the shoulders. Calibrated
# against real seated webcam framing (mean ~0.25); below it the head is dropped toward the desk.
NECK_RATIO_MIN = 0.15
GESTURE_SPEED_THRESHOLD = 0.18  # normalized wrist speed (per second) above this = active gesturing
HEAD_STABILITY_SCALE = 0.30     # normalized head speed (per second) mapped to a 0..1 stability
POSE_VISIBILITY_MIN = 0.5       # landmark visibility below this = not reliably detected

# MediaPipe Pose landmark indices we use.
_NOSE = 0
_L_SHOULDER, _R_SHOULDER = 11, 12
_L_WRIST, _R_WRIST = 15, 16


@dataclass
class Observation:
    """Raw per-sampled-frame signals. The heavy detection stage fills these in; the pure
    :func:`_aggregate` turns a sequence of them into metrics (so the math is testable)."""

    t_ms: float
    face: bool = False
    smile: float | None = None
    brow: float | None = None
    blink: float | None = None
    yaw: float | None = None
    pitch: float | None = None
    head_cx: float | None = None       # normalized face-center x (0..1)
    head_cy: float | None = None       # normalized face-center y (0..1)
    pose: bool = False
    shoulder_tilt: float | None = None  # |dy|/width (0 = perfectly level)
    neck_ratio: float | None = None     # (shoulder_y - nose_y)/width (bigger = head up/upright)
    wrist_cx: float | None = None       # midpoint of visible wrists, normalized x
    wrist_cy: float | None = None       # midpoint of visible wrists, normalized y
    hands_visible: bool = False


@dataclass
class NonverbalMetrics:
    """Aggregated nonverbal metrics of a session (all local, numbers only)."""

    frames_analyzed: int
    face_detected_ratio: float
    pose_detected_ratio: float
    # Face / expression
    smile_ratio: float
    expression_variability: float
    flat_affect_ratio: float
    blink_rate_per_min: float
    brow_raise_events: int
    eye_contact_ratio: float
    head_stability: float
    # Posture (proxy) + gestures
    posture_upright_ratio: float
    slouch_ratio: float
    gesture_rate_per_min: float
    hands_visible_ratio: float
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable, no heavy deps)
# ---------------------------------------------------------------------------
def _matrix_to_yaw_pitch(matrix) -> tuple[float, float]:
    """Extract (yaw, pitch) in DEGREES from a 4x4 facial transformation matrix.

    ``matrix`` may be a numpy array or a list of lists. Uses the upper-left 3x3 rotation
    block and a standard Euler (X=pitch, Y=yaw) decomposition. Identity -> (0, 0).
    """
    r = [[float(matrix[i][j]) for j in range(3)] for i in range(3)]
    sy = math.sqrt(r[0][0] * r[0][0] + r[1][0] * r[1][0])
    pitch = math.degrees(math.atan2(r[2][1], r[2][2]))
    yaw = math.degrees(math.atan2(-r[2][0], sy))
    return yaw, pitch


def _rising_edges(
    values: Iterable[float | None], threshold: float, low: float | None = None,
) -> int:
    """Count low->high crossings of ``threshold`` with optional hysteresis.

    After a rising edge is counted, the signal must fall back below ``low`` before another edge
    can be counted — this debounces a value that flickers around a single threshold (e.g. the
    eyeBlink blendshape). With ``low is None`` it degrades to a plain single-threshold detector.
    A ``None`` sample (undetected frame) re-arms, so a gap never fabricates an edge.
    """
    low = threshold if low is None else low
    events = 0
    armed = True
    for v in values:
        if v is None:
            armed = True
            continue
        if armed and v >= threshold:
            events += 1
            armed = False
        elif not armed and v <= low:
            armed = True
    return events


def _aggregate(observations: list[Observation], duration_s: float) -> NonverbalMetrics:
    """Turn per-frame observations into aggregated metrics. Pure; never raises on empty
    detection — it returns zeros/partial ratios and records a note instead."""
    n = len(observations)
    minutes = duration_s / 60.0 if duration_s > 0 else 0.0
    notes: list[str] = []

    def _ratio(count: int, total: int) -> float:
        return round(count / total, 3) if total else 0.0

    face_frames = [o for o in observations if o.face]
    pose_frames = [o for o in observations if o.pose]

    if not face_frames:
        notes.append("no face detected")
    if not pose_frames:
        notes.append("no pose/body detected")

    # --- Face / expression ---
    smiles = [o.smile for o in face_frames if o.smile is not None]
    smile_ratio = _ratio(sum(1 for s in smiles if s >= SMILE_THRESHOLD), len(smiles))

    expr = [
        (o.smile or 0.0) + (o.brow or 0.0)
        for o in face_frames
        if o.smile is not None or o.brow is not None
    ]
    expression_variability = round(statistics.pstdev(expr), 4) if len(expr) >= 2 else 0.0
    flat_affect_ratio = _ratio(sum(1 for e in expr if e < FLAT_EXPRESSION_THRESHOLD), len(expr))

    blink_events = _rising_edges((o.blink for o in face_frames), BLINK_THRESHOLD, low=BLINK_LOW)
    blink_rate_per_min = round(blink_events / minutes, 1) if minutes > 0 else 0.0
    brow_raise_events = _rising_edges((o.brow for o in face_frames), BROW_THRESHOLD)

    gaze = [
        o for o in face_frames
        if o.yaw is not None and o.pitch is not None
    ]
    eye_contact_ratio = _ratio(
        sum(1 for o in gaze
            if abs(o.yaw) <= EYE_CONTACT_YAW_DEG and abs(o.pitch) <= EYE_CONTACT_PITCH_DEG),
        len(gaze),
    )

    head_stability = _movement_stability(
        [(o.t_ms, o.head_cx, o.head_cy) for o in face_frames], HEAD_STABILITY_SCALE,
    )

    # --- Posture (proxy) ---
    upright = 0
    posed = 0
    for o in pose_frames:
        if o.shoulder_tilt is None or o.neck_ratio is None:
            continue
        posed += 1
        if o.shoulder_tilt <= SHOULDER_TILT_MAX and o.neck_ratio >= NECK_RATIO_MIN:
            upright += 1
    posture_upright_ratio = _ratio(upright, posed)
    slouch_ratio = round(1.0 - posture_upright_ratio, 3) if posed else 0.0

    # --- Gestures (wrist movement bursts) ---
    gesture_events = _gesture_events([(o.t_ms, o.wrist_cx, o.wrist_cy) for o in pose_frames])
    gesture_rate_per_min = round(gesture_events / minutes, 1) if minutes > 0 else 0.0
    hands_visible_ratio = _ratio(sum(1 for o in pose_frames if o.hands_visible), len(pose_frames))

    return NonverbalMetrics(
        frames_analyzed=n,
        face_detected_ratio=_ratio(len(face_frames), n),
        pose_detected_ratio=_ratio(len(pose_frames), n),
        smile_ratio=smile_ratio,
        expression_variability=expression_variability,
        flat_affect_ratio=flat_affect_ratio,
        blink_rate_per_min=blink_rate_per_min,
        brow_raise_events=brow_raise_events,
        eye_contact_ratio=eye_contact_ratio,
        head_stability=head_stability,
        posture_upright_ratio=posture_upright_ratio,
        slouch_ratio=slouch_ratio,
        gesture_rate_per_min=gesture_rate_per_min,
        hands_visible_ratio=hands_visible_ratio,
        notes=notes,
    )


def _movement_stability(
    points: list[tuple[float, float | None, float | None]], scale: float,
) -> float:
    """Map the mean per-second displacement of a tracked point to a 0..1 stability score
    (1 = perfectly still). Consecutive samples with coordinates are used pairwise."""
    speeds: list[float] = []
    prev: tuple[float, float, float] | None = None
    for t_ms, x, y in points:
        if x is None or y is None:
            prev = None
            continue
        if prev is not None:
            dt = (t_ms - prev[0]) / 1000.0
            if dt > 0:
                dist = math.hypot(x - prev[1], y - prev[2])
                speeds.append(dist / dt)
        prev = (t_ms, x, y)
    if not speeds:
        return 0.0
    mean_speed = sum(speeds) / len(speeds)
    return round(max(0.0, min(1.0, 1.0 - mean_speed / scale)), 3)


def _gesture_events(points: list[tuple[float, float | None, float | None]]) -> int:
    """Count bursts of hand movement: contiguous runs where the normalized wrist speed
    exceeds :data:`GESTURE_SPEED_THRESHOLD` count as ONE gesture each (rising edges)."""
    events = 0
    active = False
    prev: tuple[float, float, float] | None = None
    for t_ms, x, y in points:
        if x is None or y is None:
            prev = None
            active = False
            continue
        fast = False
        if prev is not None:
            dt = (t_ms - prev[0]) / 1000.0
            if dt > 0:
                speed = math.hypot(x - prev[1], y - prev[2]) / dt
                fast = speed >= GESTURE_SPEED_THRESHOLD
        if fast and not active:
            events += 1
        active = fast
        prev = (t_ms, x, y)
    return events


# ---------------------------------------------------------------------------
# Heavy detection stage (cv2 + mediapipe) — imported lazily
# ---------------------------------------------------------------------------
def _ensure_model(name: str) -> Path:
    """Resolve a ``.task`` model file, downloading it once if needed.

    Order: ``config.VISION_MODEL_DIR/<name>.task`` (offline/pinned) -> the cache under
    ``DATA_DIR/.cache/mediapipe/`` (auto-downloaded). A too-small file (e.g. an HTML error
    page) is re-downloaded.
    """
    url, min_size = _MODELS[name]
    if config.VISION_MODEL_DIR:
        candidate = Path(config.VISION_MODEL_DIR) / f"{name}.task"
        if candidate.exists():
            return candidate
    cache_dir = config.DATA_DIR / ".cache" / "mediapipe"
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / f"{name}.task"
    if dest.exists() and dest.stat().st_size >= min_size:
        return dest
    tmp = dest.with_suffix(".task.part")
    urllib.request.urlretrieve(url, tmp)  # noqa: S310 - pinned https storage.googleapis.com URL
    if tmp.stat().st_size < min_size:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded MediaPipe model '{name}' looks corrupt (too small).")
    tmp.replace(dest)
    return dest


def _build_face_landmarker():
    """Construct a MediaPipe FaceLandmarker in VIDEO mode (blendshapes + transform matrix)."""
    from mediapipe.tasks import python as mp_python  # noqa: PLC0415
    from mediapipe.tasks.python import vision as mp_vision  # noqa: PLC0415

    opts = mp_vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(_ensure_model("face_landmarker"))),
        running_mode=mp_vision.RunningMode.VIDEO,
        num_faces=1,
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=True,
    )
    return mp_vision.FaceLandmarker.create_from_options(opts)


def _build_pose_landmarker():
    """Construct a MediaPipe PoseLandmarker (lite) in VIDEO mode."""
    from mediapipe.tasks import python as mp_python  # noqa: PLC0415
    from mediapipe.tasks.python import vision as mp_vision  # noqa: PLC0415

    opts = mp_vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(_ensure_model("pose_landmarker"))),
        running_mode=mp_vision.RunningMode.VIDEO,
        num_poses=1,
    )
    return mp_vision.PoseLandmarker.create_from_options(opts)


def _blendshapes_to_dict(categories) -> dict[str, float]:
    """Flatten a MediaPipe blendshape category list into {name: score}."""
    return {c.category_name: c.score for c in categories}


def _face_observation(obs: Observation, face_result) -> None:
    """Fill the face-related fields of ``obs`` from a FaceLandmarker result (in place)."""
    blendshapes = getattr(face_result, "face_blendshapes", None) or []
    landmarks = getattr(face_result, "face_landmarks", None) or []
    if not blendshapes or not landmarks:
        return
    obs.face = True
    bs = _blendshapes_to_dict(blendshapes[0])
    obs.smile = (bs.get("mouthSmileLeft", 0.0) + bs.get("mouthSmileRight", 0.0)) / 2.0
    obs.brow = (bs.get("browOuterUpLeft", 0.0) + bs.get("browOuterUpRight", 0.0)) / 2.0
    obs.blink = max(bs.get("eyeBlinkLeft", 0.0), bs.get("eyeBlinkRight", 0.0))

    matrixes = getattr(face_result, "facial_transformation_matrixes", None) or []
    if matrixes is not None and len(matrixes):
        obs.yaw, obs.pitch = _matrix_to_yaw_pitch(matrixes[0])

    pts = landmarks[0]
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    obs.head_cx = sum(xs) / len(xs)
    obs.head_cy = sum(ys) / len(ys)


def _pose_observation(obs: Observation, pose_result) -> None:
    """Fill the pose-related fields of ``obs`` from a PoseLandmarker result (in place)."""
    all_landmarks = getattr(pose_result, "pose_landmarks", None) or []
    if not all_landmarks:
        return
    lm = all_landmarks[0]
    obs.pose = True

    ls, rs = lm[_L_SHOULDER], lm[_R_SHOULDER]
    nose = lm[_NOSE]
    width = math.hypot(ls.x - rs.x, ls.y - rs.y) or 1e-6
    obs.shoulder_tilt = abs(ls.y - rs.y) / width
    shoulder_mid_y = (ls.y + rs.y) / 2.0
    obs.neck_ratio = (shoulder_mid_y - nose.y) / width

    wrists = [lm[_L_WRIST], lm[_R_WRIST]]
    visible = [w for w in wrists if getattr(w, "visibility", 1.0) >= POSE_VISIBILITY_MIN]
    if visible:
        obs.hands_visible = True
        obs.wrist_cx = sum(w.x for w in visible) / len(visible)
        obs.wrist_cy = sum(w.y for w in visible) / len(visible)


def _observe_video(video: Path) -> tuple[list[Observation], float]:
    """Sample frames from ``video`` and run both landmarkers, returning observations and
    the video duration in seconds. This is the ONLY function that touches cv2/mediapipe."""
    import cv2  # noqa: PLC0415
    import mediapipe as mp  # noqa: PLC0415

    from . import model_cache  # noqa: PLC0415

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video for vision analysis: {video}")
    try:
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
        if src_fps <= 0:
            src_fps = 30.0  # some containers don't report fps
        duration_s = frame_count / src_fps if frame_count > 0 else 0.0
        step = max(1, round(src_fps / max(1, config.VISION_FPS)))
        max_dim = config.VISION_MAX_DIM

        face = model_cache.get_face_landmarker()
        pose = model_cache.get_pose_landmarker()

        observations: list[Observation] = []
        idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % step != 0:
                idx += 1
                continue
            h, w = frame.shape[:2]
            scale = max_dim / max(h, w)
            if scale < 1.0:
                frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            t_ms = int(idx / src_fps * 1000)

            obs = Observation(t_ms=float(t_ms))
            try:
                _face_observation(obs, face.detect_for_video(mp_image, t_ms))
            except Exception:  # noqa: BLE001 - a single bad frame must not abort the pass
                pass
            try:
                _pose_observation(obs, pose.detect_for_video(mp_image, t_ms))
            except Exception:  # noqa: BLE001
                pass
            observations.append(obs)
            idx += 1

        if duration_s <= 0 and observations:
            duration_s = observations[-1].t_ms / 1000.0
        return observations, duration_s
    finally:
        cap.release()


def analyze(
    video: Path,
    observe: Callable[[Path], tuple[list[Observation], float]] | None = None,
) -> dict:
    """Analyze the nonverbal channel of ``video`` and return the metrics as a plain dict.

    ``observe`` is injectable for testing (defaults to the real cv2/mediapipe sampler). Never
    raises on empty detection — an undetected face/body yields zero ratios plus a note.
    """
    observe = observe or _observe_video
    observations, duration_s = observe(Path(video))
    return _aggregate(observations, duration_s).to_dict()
