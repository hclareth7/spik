"""Guard the analysis progress-bar stage weights.

``global_pct`` maps (stage, fraction) into a monotonic 0..100 bar; it only reaches 100% if the
weights sum to exactly 1.0. The nonverbal ``vision`` stage must be present with a nonzero weight.
"""

from __future__ import annotations

from web.state import JobRegistry


def test_stage_weights_sum_to_one():
    total = sum(w for _name, w in JobRegistry.STAGE_WEIGHTS)
    assert round(total, 6) == 1.0


def test_vision_stage_present():
    names = {name for name, _w in JobRegistry.STAGE_WEIGHTS}
    assert "vision" in names
    weight = dict(JobRegistry.STAGE_WEIGHTS)["vision"]
    assert weight > 0.0


def test_global_pct_reaches_100_at_last_stage():
    jobs = JobRegistry()
    assert jobs.global_pct("save", 1.0) == 100.0
