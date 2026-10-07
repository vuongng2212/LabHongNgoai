"""Unit tests for path resolution helpers."""

import sys
from pathlib import Path

import pytest

from ir_terminal.utils.path_helpers import (
    get_app_data_dir,
    get_assets_dir,
    get_db_path,
    get_logs_dir,
    get_project_root,
    get_transfers_dir,
    get_web_index_path,
)


class TestPathHelpers:
    """Verifies filesystem path and bundle resolution."""

    def test_get_project_root(self) -> None:
        """Verifies root path resolution."""
        root = get_project_root()
        assert isinstance(root, Path)
        assert (root / "pyproject.toml").exists()

    def test_get_project_root_meipass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verifies PyInstaller _MEIPASS detection."""
        monkeypatch.setattr(sys, "_MEIPASS", "/tmp/mock_bundle", raising=False)
        assert get_project_root() == Path("/tmp/mock_bundle")

    def test_get_web_index_path(self) -> None:
        """Verifies web index path points to an actual index.html file."""
        index_path = get_web_index_path()
        assert index_path.name == "index.html"
        assert index_path.exists()

    def test_get_assets_dir(self) -> None:
        """Verifies assets dir path object."""
        assets = get_assets_dir()
        assert isinstance(assets, Path)

    def test_app_data_directories(self) -> None:
        """Verifies creation and resolution of data, db, transfers, and logs directories."""
        data_dir = get_app_data_dir()
        assert data_dir.exists()

        db_path = get_db_path()
        assert db_path.name == "ir_terminal.db"
        assert db_path.parent.exists()

        transfers = get_transfers_dir()
        assert transfers.exists()
        assert transfers.name == "transfers"

        logs = get_logs_dir()
        assert logs.exists()
        assert logs.name == "logs"
