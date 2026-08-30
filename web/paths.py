"""Filesystem paths and constants derived from :mod:`spik.config`.

Centralizing these keeps the routers free of ``Path`` arithmetic and makes the
locations easy to override in tests (monkeypatch the importing module's binding).
"""

from __future__ import annotations

from pathlib import Path

from spik import config

# Repository layout: web/paths.py -> web/ -> project root.
PROJECT_DIR = Path(__file__).resolve().parent.parent
# Legacy plain static frontend (index.html/app.js/app.css). Kept for rollback safety;
# served only when the new built frontend (DIST_DIR) is absent.
STATIC_DIR = Path(__file__).resolve().parent / "static"
# Built React/Vite frontend (frontend/ -> vite build outDir). This is the primary UI.
DIST_DIR = Path(__file__).resolve().parent / "dist"
RECORD_SCRIPT = PROJECT_DIR / "capture" / "record.sh"

# systemd --user unit that loads the "Speak Clean Mic" filter-chain (host only).
NOISE_UNIT = "filter-chain.service"

# Files living under the data volume (data/ is gitignored).
PROJECTS_FILE = config.DATA_DIR / ".projects.json"   # persists empty projects
PREFS_FILE = config.DATA_DIR / ".ui-prefs.json"      # default UI devices
MIC_TEST_WAV = config.DATA_DIR / ".mic-test.wav"     # overwritten on each mic test
