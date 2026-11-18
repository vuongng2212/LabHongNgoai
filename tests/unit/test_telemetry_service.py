"""Unit tests for open-loop telemetry and sniffer tracking."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ir_terminal.protocol.constants import SEQ_END, SEQ_HEADER
from ir_terminal.protocol.packet import WinFormPacket
from ir_terminal.services.storage_service import StorageService
from ir_terminal.services.telemetry_service import (
    OPEN_LOOP_STATUS,
    TelemetryService,
    frame_type_name,
)


class TestTelemetryService:
    """Verifies counters, frame typing and CSV export for the WinForm wire format."""

    def test_frame_type_mapping(self) -> None:
        assert frame_type_name(SEQ_HEADER) == "FILE_HEADER"
        assert frame_type_name(SEQ_END) == "FILE_END"
        assert frame_type_name(0) == "DATA_CHUNK"

    def test_counters_and_sniffer_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = StorageService(data_dir=Path(tmpdir) / "data")
            telemetry = TelemetryService(storage_service=storage, window_sec=1.0)

            telemetry.record_bytes_sent(1024)
            telemetry.record_bytes_received(512)
            telemetry.record_packet_sent(sequence=SEQ_HEADER, length=27)
            telemetry.record_packet_sent(sequence=0, length=256)
            telemetry.record_packet_received(
                WinFormPacket(sequence=0, length=256, data=b"abc"), latency_ms=12.5
            )

            summary = telemetry.get_summary()
            assert summary["total_packets_sent"] == 2
            assert summary["total_packets_received"] == 1
            assert summary["total_frames_captured"] == 3
            assert summary["rtt_ms"] == 12.5

            records = telemetry.get_sniffer_records()
            assert [record["type_str"] for record in records] == [
                "FILE_HEADER",
                "DATA_CHUNK",
                "DATA_CHUNK",
            ]
            assert all(record["checksum_status"] == OPEN_LOOP_STATUS for record in records)
            assert records[-1]["payload_preview"] == "PRE [61 62 63]"

    def test_reset_clears_counters_and_sniffer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = StorageService(data_dir=Path(tmpdir) / "data")
            telemetry = TelemetryService(storage_service=storage)
            telemetry.record_packet_sent(sequence=SEQ_END, length=0)
            telemetry.record_packet_lost()

            telemetry.reset_counters()
            summary = telemetry.get_summary()

            assert summary["total_packets_sent"] == 0
            assert summary["total_packets_lost"] == 0
            assert telemetry.get_sniffer_records() == []

    def test_sniffer_csv_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            storage = StorageService(data_dir=root / "data")
            telemetry = TelemetryService(storage_service=storage)
            telemetry.record_packet_received(WinFormPacket(sequence=1, length=2, data=b"hi"))

            csv_text = telemetry.export_sniffer_csv(root / "sniffer.csv")

            assert "FrameID" in csv_text
            assert "#0x00000001" in csv_text
            assert (root / "sniffer.csv").read_text(encoding="utf-8").splitlines() == (
                csv_text.splitlines()
            )
