"""Small process/file helpers shared by the web layer.

Security: subprocess is always invoked with an argument list and never with
``shell=True`` (device/source names originate from the browser).
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def run(cmd: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
    """Run a command (argument list, no shell) and return the completed process."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def read_tail(log_path: str | None, limit: int = 600) -> str:
    """Return the last ~``limit`` characters of an ffmpeg log, to report a failure cause."""
    if not log_path:
        return ""
    try:
        text = Path(log_path).read_text(errors="replace").strip()
    except OSError:
        return ""
    return text[-limit:]
