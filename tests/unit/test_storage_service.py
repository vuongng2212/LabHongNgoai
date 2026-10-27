"""Unit tests for file-based durable storage repository (JSON hard-drive stores)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import ir_terminal.services.storage_service as storage_module
from ir_terminal.services.storage_service import StorageService


def make_storage(tmp_path: Path) -> StorageService:
    """Builds an isolated storage rooted at a fresh data directory."""
    return StorageService(data_dir=tmp_path / "data")


class TestStorageInit:
    """Store bootstrap behaviour."""

    def test_init_creates_data_dir(self, tmp_path: Path) -> None:
        storage = make_storage(tmp_path)
        assert storage._data_dir.exists()
        assert storage._data_dir.is_dir()

    def test_empty_store_reads(self, tmp_path: Path) -> None:
        storage = make_storage(tmp_path)
        assert storage.get_chat_history() == []
        assert storage.get_file_transfers() == []
        assert storage.get_telemetry_history() == []
        assert storage.get_setting("missing", "fallback") == "fallback"

    def test_empty_csv_exports_have_headers_only(self, tmp_path: Path) -> None:
        storage = make_storage(tmp_path)
        telemetry_csv = storage.export_telemetry_csv()
        assert "Throughput_KBps" in telemetry_csv
        assert telemetry_csv.count("\n") == 1
        transfers_csv = storage.export_transfers_csv()
        assert "Filename" in transfers_csv
        assert transfers_csv.count("\n") == 1


class TestChatPersistence:
    """Chat history durability on hard drive."""

    def test_save_and_reload_single_message(self, tmp_path: Path) -> None:
        storage = make_storage(tmp_path)
        msg_id = storage.save_chat_message(
            sender="Station Alpha",
            text="Optical Test Message",
            timestamp="14:30:00",
            seq=1,
            is_outgoing=True,
        )
        assert msg_id == 1

        history = storage.get_chat_history(limit=10)
        assert len(history) == 1
        assert history[0]["text"] == "Optical Test Message"
        assert history[0]["sender"] == "Station Alpha"
        assert history[0]["seq"] == 1
        assert history[0]["is_outgoing"] is True
        assert history[0]["created_at"] != ""

    def test_chronological_order_and_limit_takes_most_recent(self, tmp_path: Path) -> None:
        storage = make_storage(tmp_path)
        for i in range(5):
            storage.save_chat_message("A", f"msg-{i}", "00:00:00", i, i % 2 == 0)

        history = storage.get_chat_history(limit=2)
        assert [m["text"] for m in history] == ["msg-3", "msg-4"]
        assert [m["id"] for m in history] == [4, 5]

    def test_unicode_text_roundtrip(self, tmp_path: Path) -> None:
        storage = make_storage(tmp_path)
        storage.save_chat_message(
            "Trạm A", "Chùm tia hồng ngoại 880nm ổn định", "12:00:00", 7, False
        )
        history = storage.get_chat_history()
        assert history[0]["text"] == "Chùm tia hồng ngoại 880nm ổn định"

    def test_persistence_across_instances(self, tmp_path: Path) -> None:
        first = make_storage(tmp_path)
        first.save_chat_message("A", "persist me", "01:02:03", 9, True)

        second = StorageService(data_dir=tmp_path / "data")
        history = second.get_chat_history()
        assert len(history) == 1
        assert history[0]["text"] == "persist me"

    def test_store_file_is_plain_json(self, tmp_path: Path) -> None:
        storage = make_storage(tmp_path)
        storage.save_chat_message("A", "x", "00:00:00", 1, True)
        raw = (tmp_path / "data" / "chat_history.json").read_text(encoding="utf-8")
        assert json.loads(raw)[0]["text"] == "x"

    def test_corrupt_chat_file_recovers(self, tmp_path: Path) -> None:
        storage = make_storage(tmp_path)
        (tmp_path / "data" / "chat_history.json").write_text("{broken", encoding="utf-8")
        assert storage.get_chat_history() == []
        assert storage.save_chat_message("A", "after-corrupt", "00:00:00", 1, True) == 1
        assert storage.get_chat_history()[0]["text"] == "after-corrupt"

    def test_non_list_shape_recovers(self, tmp_path: Path) -> None:
        storage = make_storage(tmp_path)
        (tmp_path / "data" / "chat_history.json").write_text('{"a": 1}', encoding="utf-8")
        assert storage.get_chat_history() == []


class TestFileTransferPersistence:
    """Transfer log durability on hard drive."""

    def test_save_and_reload_with_path(self, tmp_path: Path) -> None:
        storage = make_storage(tmp_path)
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
        assert record_id == 1

        transfers = storage.get_file_transfers(limit=5)
        assert len(transfers) == 1
        assert transfers[0]["filename"] == "laser_spec.bin"
        assert transfers[0]["file_path"] == "/tmp/laser_spec.bin"
        assert transfers[0]["direction"] == "TX"
        assert transfers[0]["size_bytes"] == 4096
        assert transfers[0]["crc32_hex"] == "0xABCD1234"
        assert transfers[0]["status"] == "SUCCESS"

    def test_newest_first_and_limit(self, tmp_path: Path) -> None:
        storage = make_storage(tmp_path)
        for i in range(4):
            storage.save_file_transfer(
                f"f{i}.bin", f"/tmp/f{i}.bin", "RX", 10, "0x0", 1.0, 0.1, "SUCCESS"
            )

        transfers = storage.get_file_transfers(limit=2)
        assert [t["filename"] for t in transfers] == ["f3.bin", "f2.bin"]

    def test_persistence_across_instances(self, tmp_path: Path) -> None:
        make_storage(tmp_path).save_file_transfer(
            "p.bin", "/tmp/p.bin", "RX", 8, "0x1", 2.0, 0.2, "FAILED"
        )
        second = StorageService(data_dir=tmp_path / "data")
        assert second.get_file_transfers()[0]["status"] == "FAILED"

    def test_transfers_csv_export(self, tmp_path: Path) -> None:
        storage = make_storage(tmp_path)
        storage.save_file_transfer(
            "data.bin", "/tmp/data.bin", "TX", 1024, "0x12345678", 10.0, 0.1, "SUCCESS"
        )
        csv_path = tmp_path / "transfers_out.csv"
        csv_text = storage.export_transfers_csv(csv_path)
        assert "Filename" in csv_text
        assert "data.bin" in csv_text
        assert "/tmp/data.bin" in csv_text
        assert csv_path.exists()

    def test_corrupt_transfers_file_recovers(self, tmp_path: Path) -> None:
        storage = make_storage(tmp_path)
        (tmp_path / "data" / "file_transfers.json").write_text("nope", encoding="utf-8")
        assert storage.get_file_transfers() == []


class TestTelemetryPersistence:
    """Telemetry sample durability on hard drive."""

    def test_save_and_reload_sample(self, tmp_path: Path) -> None:
        storage = make_storage(tmp_path)
        sample_id = storage.save_telemetry_sample(
            timestamp_epoch=1700000000.0,
            throughput_kbps=9.85,
            rtt_ms=34.2,
            packet_loss_rate=0.0,
            total_packets_sent=120,
            total_packets_lost=0,
        )
        assert sample_id == 1

        samples = storage.get_telemetry_history(limit=10)
        assert len(samples) == 1
        assert samples[0]["throughput_kbps"] == 9.85
        assert samples[0]["rtt_ms"] == 34.2
        assert samples[0]["total_packets_sent"] == 120

        csv_path = tmp_path / "telemetry_out.csv"
        csv_text = storage.export_telemetry_csv(csv_path)
        assert "Throughput_KBps" in csv_text
        assert "9.85" in csv_text
        assert csv_path.exists()

    def test_history_limit_takes_most_recent(self, tmp_path: Path) -> None:
        storage = make_storage(tmp_path)
        for i in range(5):
            storage.save_telemetry_sample(float(i), 1.0, 2.0, 0.0, i, 0)
        samples = storage.get_telemetry_history(limit=2)
        assert [s["timestamp_epoch"] for s in samples] == [3.0, 4.0]

    def test_sample_cap_bounds_disk_usage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(storage_module, "_MAX_TELEMETRY_SAMPLES", 3)
        storage = make_storage(tmp_path)
        for i in range(5):
            storage.save_telemetry_sample(float(i), 1.0, 2.0, 0.0, i, 0)
        samples = storage.get_telemetry_history(limit=10)
        assert len(samples) == 3
        assert [s["timestamp_epoch"] for s in samples] == [2.0, 3.0, 4.0]

    def test_corrupt_telemetry_file_recovers(self, tmp_path: Path) -> None:
        storage = make_storage(tmp_path)
        (tmp_path / "data" / "telemetry_samples.json").write_text("[1,2", encoding="utf-8")
        assert storage.get_telemetry_history() == []


class TestSettings:
    """Key-value settings durability."""

    def test_roundtrip_update_and_default(self, tmp_path: Path) -> None:
        storage = make_storage(tmp_path)
        storage.save_setting("last_port", "COM4")
        assert storage.get_setting("last_port") == "COM4"
        assert storage.get_setting("non_existent", "default_val") == "default_val"

        storage.save_setting("last_port", "COM7")
        assert storage.get_setting("last_port") == "COM7"

    def test_settings_survive_reopen(self, tmp_path: Path) -> None:
        make_storage(tmp_path).save_setting("baud", "115200")
        assert StorageService(data_dir=tmp_path / "data").get_setting("baud") == "115200"

    def test_corrupt_settings_recover(self, tmp_path: Path) -> None:
        storage = make_storage(tmp_path)
        (tmp_path / "data" / "settings.json").write_text("zzz", encoding="utf-8")
        assert storage.get_setting("k", "d") == "d"
        storage.save_setting("k", "v")
        assert storage.get_setting("k") == "v"

    def test_non_object_settings_recover(self, tmp_path: Path) -> None:
        storage = make_storage(tmp_path)
        (tmp_path / "data" / "settings.json").write_text("[1, 2]", encoding="utf-8")
        assert storage.get_setting("k", "d") == "d"


class TestConcurrency:
    """Thread-safety smoke test for background Qt worker writes."""

    def test_parallel_chat_saves_keep_unique_ids(self, tmp_path: Path) -> None:
        storage = make_storage(tmp_path)
        errors: list[Exception] = []

        def worker(n: int) -> None:
            try:
                for i in range(25):
                    storage.save_chat_message("T", f"t{n}-{i}", "00:00:00", i, True)
            except Exception as exc:  # pragma: no cover - any error fails the test
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        history = storage.get_chat_history(limit=500)
        assert len(history) == 200
        assert len({m["id"] for m in history}) == 200
