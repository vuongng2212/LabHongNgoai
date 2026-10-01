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

    def test_telemetry_samples_and_csv_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_terminal.db"
            storage = StorageService(db_path=db_path)

            sample_id = storage.save_telemetry_sample(
                timestamp_epoch=1700000000.0,
                throughput_kbps=9.85,
                rtt_ms=34.2,
                packet_loss_rate=0.0,
                total_packets_sent=120,
                total_packets_lost=0,
            )
            assert sample_id > 0

            samples = storage.get_telemetry_history(limit=10)
            assert len(samples) == 1
            assert samples[0]["throughput_kbps"] == 9.85
            assert samples[0]["rtt_ms"] == 34.2
            assert samples[0]["total_packets_sent"] == 120

            # Test CSV export
            csv_path = Path(tmpdir) / "telemetry_out.csv"
            csv_text = storage.export_telemetry_csv(csv_path)
            assert "Throughput_KBps" in csv_text
            assert "9.85" in csv_text
            assert csv_path.exists()

    def test_file_transfers_csv_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_terminal.db"
            storage = StorageService(db_path=db_path)

            storage.save_file_transfer(
                filename="data.bin",
                file_path="/tmp/data.bin",
                direction="TX",
                size_bytes=1024,
                crc32_hex="0x12345678",
                speed_kbps=10.0,
                duration_sec=0.1,
                status="SUCCESS",
            )
            csv_path = Path(tmpdir) / "transfers_out.csv"
            csv_text = storage.export_transfers_csv(csv_path)
            assert "Filename" in csv_text
            assert "data.bin" in csv_text
            assert csv_path.exists()

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
