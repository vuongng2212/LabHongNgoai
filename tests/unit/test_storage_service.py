"""Unit tests for SQLite persistent storage repository."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ir_terminal.services.storage_service import StorageService


class TestStorageService:
    """Test suite for local SQLite persistent storage."""

    def test_init_and_chat_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_terminal.db"
            storage = StorageService(db_path=db_path)

            msg_id = storage.save_chat_message(
                sender="Station Alpha",
                text="Optical Test Message",
                timestamp="14:30:00",
                seq=1,
                is_outgoing=True,
            )
            assert msg_id > 0

            history = storage.get_chat_history(limit=10)
            assert len(history) == 1
            assert history[0]["text"] == "Optical Test Message"
            assert history[0]["seq"] == 1
            assert history[0]["is_outgoing"] is True

    def test_file_transfer_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_terminal.db"
            storage = StorageService(db_path=db_path)

            record_id = storage.save_file_transfer(
                filename="laser_spec.bin",
                file_path="/tmp/laser_spec.bin",
                direction="TX",
                size_bytes=4096,
                crc32_hex="0xABCD1234",
                speed_kbps=11.2,
                duration_sec=0.35,
                status="SUCCESS",
            )
            assert record_id > 0

            transfers = storage.get_file_transfers(limit=5)
            assert len(transfers) == 1
            assert transfers[0]["filename"] == "laser_spec.bin"
            assert transfers[0]["size_bytes"] == 4096
            assert transfers[0]["crc32_hex"] == "0xABCD1234"
            assert transfers[0]["status"] == "SUCCESS"

    def test_settings_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_terminal.db"
            storage = StorageService(db_path=db_path)

            storage.save_setting("last_port", "COM4")
            assert storage.get_setting("last_port") == "COM4"
            assert storage.get_setting("non_existent", "default_val") == "default_val"

            # Update setting
            storage.save_setting("last_port", "COM7")
            assert storage.get_setting("last_port") == "COM7"
