"""Suite-wide fixtures: isolate every test from the developer's real home directory.

Services fall back to ~/.ir_terminal when no explicit path is injected, so the
whole suite runs with HOME redirected into a temporary directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redirects HOME so default storage/log paths never touch real user data."""
    monkeypatch.setenv("HOME", str(tmp_path))
