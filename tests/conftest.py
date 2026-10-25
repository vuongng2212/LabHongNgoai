"""Suite-wide fixtures: isolate every test from the developer's real data directory.

Runtime data defaults to ``<exe or project root>/data``; the whole suite runs
with ``IR_DATA_DIR`` redirected into a per-test temporary directory so tests
never touch the checkout's ``data/`` tree or the user's home directory.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redirects HOME and IR_DATA_DIR so tests never touch real user data."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("IR_DATA_DIR", str(tmp_path / "app_data"))
