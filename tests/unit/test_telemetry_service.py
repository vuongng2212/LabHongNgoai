"""Unit tests for TelemetryService, sniffer tracking, and CSV exports."""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from ir_terminal.protocol.constants import Opcode
from ir_terminal.protocol.packet import Header, Packet
from ir_terminal.services.storage_service import StorageService
from ir_terminal.services.telemetry_service import TelemetryService


class TestTelemetryService:
    """Test suite for realtime optical channel metrics and sniffer features."""

    def test_telemetry_throughput_and_events(self) -> None:
        app = QCoreApplication.instance() or QCoreApplication([])
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = StorageService(db_path=Path(tmpdir) / "test.db")
            telemetry = TelemetryService(storage_service=storage, window_sec=1.0)

            # Register traffic
            telemetry.record_bytes_sent(1024)
            telemetry.record_bytes_received(512)
            assert telemetry.total_packets_sent == 0

            telemetry.record_packet_sent(seq=1, opcode=Opcode.DATA_CHUNK, length=256)
            assert telemetry.total_packets_sent == 1

            # Register incoming packet
            hdr = Header(sof=0xAA55, seq=1, opcode=Opcode.ACK, length=2)
            pkt = Packet(header=hdr, payload=b"\x01\x00", crc32=0x12345678, eof=0x55AA)
            telemetry.record_packet_received(pkt, latency_ms=28.5)
            assert telemetry.total_packets_received == 1
            assert telemetry.last_rtt_ms == 28.5

            # Register chunk transfer
            telemetry.record_chunk_transferred()
            assert telemetry.total_chunks_transferred == 1

            # Trigger tick calculation
            telemetry._on_tick()
            app.processEvents()

            summary = telemetry.get_summary()
            assert summary["total_packets_sent"] == 1
            assert summary["total_packets_received"] == 1
            assert summary["total_chunks_transferred"] == 1
            assert summary["rtt_ms"] == 28.5

    def test_sniffer_records_and_csv_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = StorageService(db_path=Path(tmpdir) / "test.db")
            telemetry = TelemetryService(storage_service=storage)

            telemetry.record_packet_sent(seq=10, opcode=Opcode.PING, length=7)
            hdr = Header(sof=0xAA55, seq=10, opcode=Opcode.PONG, length=7)
            pkt = Packet(header=hdr, payload=b"IR_PING", crc32=0x9999, eof=0x55AA)
            telemetry.record_packet_received(pkt, latency_ms=35.0)

            records = telemetry.get_sniffer_records(limit=10)
            assert len(records) == 2
            assert records[0]["type_str"] == "LINK_PING"
            assert records[1]["type_str"] == "LINK_PONG"

            # Export sniffer CSV
            csv_path = Path(tmpdir) / "sniffer_test.csv"
            csv_text = telemetry.export_sniffer_csv(csv_path)
            assert "LINK_PING" in csv_text
            assert "LINK_PONG" in csv_text
            assert csv_path.exists()

    def test_loss_rate_and_reset_counters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = StorageService(db_path=Path(tmpdir) / "test.db")
            telemetry = TelemetryService(storage_service=storage)

            telemetry.record_packet_sent(seq=1, opcode=Opcode.DATA_CHUNK, length=240)
            telemetry.record_packet_lost()
            telemetry._on_tick()

            summary = telemetry.get_summary()
            assert summary["total_packets_lost"] == 1
            assert summary["packet_loss_rate"] == 50.0  # 1 sent, 1 lost => 50%

            telemetry.reset_counters()
            reset_summary = telemetry.get_summary()
            assert reset_summary["total_packets_sent"] == 0
            assert reset_summary["total_packets_lost"] == 0
            assert reset_summary["packet_loss_rate"] == 0.0
            assert len(telemetry.get_sniffer_records()) == 0
