"""Unit tests for path resolution helpers."""

import sys
from pathlib import Path

import pytest

from ir_terminal.utils import path_helpers
from ir_terminal.utils.path_helpers import (
    DATA_DIR_ENV,
    get_app_data_dir,
    get_app_root,
    get_assets_dir,
    get_data_dir,
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
        """Verifies creation and resolution of data, transfers, and logs directories."""
        data_dir = get_app_data_dir()
        assert data_dir.exists()

        data_dir = get_data_dir()
        assert data_dir.exists()
        assert data_dir.name == "app_data"  # IR_DATA_DIR override in conftest

        transfers = get_transfers_dir()
        assert transfers.exists()
        assert transfers.name == "transfers"

        logs = get_logs_dir()
        assert logs.exists()
        assert logs.name == "logs"


class TestAppDataRootResolution:
    """Verifies dev-root and PyInstaller-frozen data placement."""

    def test_dev_default_is_project_root_data(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Development runs keep data/ at the repository root by default."""
        monkeypatch.delenv(DATA_DIR_ENV, raising=False)
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        monkeypatch.setattr(path_helpers, "get_project_root", lambda: tmp_path)

        assert get_app_root() == tmp_path
        app_data = get_app_data_dir()
        assert app_data == tmp_path / "data"
        assert (tmp_path / "data").is_dir()

    def test_frozen_uses_executable_directory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Frozen builds put data/ next to the .exe, not in the home directory."""
        monkeypatch.delenv(DATA_DIR_ENV, raising=False)
        exe_dir = tmp_path / "dist" / "IR_OWC_Lab_Terminal"
        exe_dir.mkdir(parents=True)
        exe = exe_dir / "IR_OWC_Lab_Terminal.exe"
        exe.write_bytes(b"")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe))

        assert get_app_root() == exe_dir
        assert get_app_data_dir() == exe_dir / "data"
        assert get_data_dir() == exe_dir / "data"
        assert get_transfers_dir() == exe_dir / "data" / "transfers"
        assert get_logs_dir() == exe_dir / "data" / "logs"
        assert (exe_dir / "data" / "transfers").is_dir()
        assert (exe_dir / "data" / "logs").is_dir()

    def test_override_and_instance_isolation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """IR_DATA_DIR wins and dual-mock instances get isolated subtrees."""
        override = tmp_path / "portable"
        monkeypatch.setenv(DATA_DIR_ENV, str(override))

        assert get_app_data_dir() == override
        assert get_app_data_dir("a") == override / "instances" / "A"
        assert get_data_dir("b") == override / "instances" / "B"
        assert get_transfers_dir("A") == override / "instances" / "A" / "transfers"
        assert get_logs_dir("b") == override / "instances" / "B" / "logs"
        assert (override / "instances" / "A" / "transfers").is_dir()
        assert (override / "instances" / "B" / "logs").is_dir()
