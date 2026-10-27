"""Unit tests for structured IoT logging helpers and per-layer log coverage.

Covers the logging improvements handed over for IoT debugging: hex previews,
frame summaries, framer error counters, RX overflow drops, invalid payloads,
state transitions, and telemetry/storage diagnostics.
"""

from __future__ import annotations

import logging
import struct
import tempfile
from pathlib import Path

import pytest
from _pytest.logging import LogCaptureFixture
from PySide6.QtCore import QCoreApplication

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.core.serial_worker import SerialWorker
from ir_terminal.protocol.constants import (
    EOF_MAGIC,
    MAX_PAYLOAD_SIZE,
    RING_BUFFER_CAPACITY,
    SOF_MAGIC,
    Opcode,
)
from ir_terminal.protocol.file_payloads import (
    AckPayload,
    FileChunkPayload,
    FileEndPayload,
    FileStartPayload,
)
from ir_terminal.protocol.framer import (
    FramerStats,
    pack_frame,
    parse_frames_with_stats,
)
from ir_terminal.protocol.packet import Header, Packet
from ir_terminal.services.chat_service import ChatService
from ir_terminal.services.file_transfer_service import FileTransferService, TransferState
from ir_terminal.services.storage_service import StorageService
from ir_terminal.services.telemetry_service import TelemetryService
from ir_terminal.ui.backend_bridge import BackendBridge
from ir_terminal.utils.logger import (
    _LOG_DATEFMT,
    _LOG_FORMAT,
    LogTag,
    ack_status_name,
    format_frame_summary,
    format_hex_preview,
    opcode_name,
    setup_logger,
)


def _ensure_app() -> None:
    """Creates a QCoreApplication instance when the test process has none."""
    if QCoreApplication.instance() is None:
        QCoreApplication([])


def _make_packet(seq: int, opcode: int, payload: bytes) -> Packet:
    """Builds an inbound packet bypassing wire parsing."""
    header = Header(sof=SOF_MAGIC, seq=seq, opcode=opcode, length=len(payload))
    return Packet(header=header, payload=payload, crc32=0, eof=EOF_MAGIC)


class TestLogHelpers:
    """Tests pure formatting helpers in utils/logger.py."""

    def test_opcode_name_known_and_fallback(self) -> None:
        assert opcode_name(Opcode.DATA_TEXT) == "DATA_TEXT"
        assert opcode_name(Opcode.ACK) == "ACK"
        assert opcode_name(Opcode.TRANSFER_RESUME) == "TRANSFER_RESUME"
        assert opcode_name(0xFF) == "OP_0xFF"

    def test_ack_status_name_known_and_fallback(self) -> None:
        assert ack_status_name(0x00) == "OK"
        assert ack_status_name(0x01) == "CRC_ERROR"
        assert ack_status_name(0x03) == "TIMEOUT"
        assert ack_status_name(0x09) == "STATUS_0x09"

    def test_format_hex_preview_empty(self) -> None:
        assert format_hex_preview(b"") == "<empty>"

    def test_format_hex_preview_short(self) -> None:
        assert format_hex_preview(b"\x55\xaa\x02") == "55 AA 02 (len=3B)"

    def test_format_hex_preview_truncated(self) -> None:
        data = bytes(range(50))
        preview = format_hex_preview(data)
        assert "len=50B" in preview
        assert "shown=32B" in preview
        assert preview.startswith("00 01 02")

    def test_format_hex_preview_accepts_bytearray_and_memoryview(self) -> None:
        assert format_hex_preview(bytearray(b"\x01\x02")) == "01 02 (len=2B)"
        assert format_hex_preview(memoryview(b"\x0a\x0b")) == "0A 0B (len=2B)"

    def test_format_hex_preview_custom_max_bytes(self) -> None:
        preview = format_hex_preview(b"\x01\x02\x03\x04", max_bytes=2)
        assert preview == "01 02 ... (len=4B, shown=2B)"

    def test_format_frame_summary_without_crc(self) -> None:
        summary = format_frame_summary(5, Opcode.DATA_TEXT, 12)
        assert summary == "seq=5 opcode=DATA_TEXT(0x02) len=12B"

    def test_format_frame_summary_with_crc_and_unknown_opcode(self) -> None:
        summary = format_frame_summary(7, 0xFF, 3, 0x1234ABCD)
        assert summary == "seq=7 opcode=OP_0xFF(0xFF) len=3B crc=0x1234ABCD"


class TestLoggerSetup:
    """Tests the shared formatter and setup idempotency."""

    def test_log_format_has_ms_thread_and_caller(self) -> None:
        assert "%(msecs)03d" in _LOG_FORMAT
        assert "%(threadName)s" in _LOG_FORMAT
        assert "%(funcName)s" in _LOG_FORMAT
        assert "%(lineno)d" in _LOG_FORMAT
        assert "%Y-%m-%d %H:%M:%S" in _LOG_DATEFMT

    def test_formatted_record_contains_timestamp_ms(self) -> None:
        formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_LOG_DATEFMT)
        record = logging.LogRecord(
            name="ir_terminal.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        text = formatter.format(record)
        assert "hello" in text
        assert "MainThread" in text
        assert "ir_terminal.test" in text

    def test_setup_logger_is_idempotent(self, tmp_path: Path) -> None:
        first, _ = setup_logger(log_dir=tmp_path / "logs")
        count = len(first.handlers)
        second, _ = setup_logger(log_dir=tmp_path / "logs")
        assert first is second
        assert len(second.handlers) == count


class TestFramerLogging:
    """Tests framer validation logs and parse error counters."""

    def test_pack_rejects_oversize_payload_with_error_log(self, caplog: LogCaptureFixture) -> None:
        with caplog.at_level(logging.ERROR):
            with pytest.raises(ValueError):
                pack_frame(seq=1, opcode=Opcode.DATA_TEXT, payload=b"X" * (MAX_PAYLOAD_SIZE + 1))
        assert "exceeds MAX" in caplog.text
        assert LogTag.PROTO_FRAME in caplog.text

    def test_pack_rejects_bad_seq_and_opcode(self, caplog: LogCaptureFixture) -> None:
        with caplog.at_level(logging.ERROR):
            with pytest.raises(ValueError):
                pack_frame(seq=999, opcode=Opcode.DATA_TEXT)
            with pytest.raises(ValueError):
                pack_frame(seq=1, opcode=999)
        assert "out of range" in caplog.text

    def test_pack_success_emits_debug_summary(self, caplog: LogCaptureFixture) -> None:
        with caplog.at_level(logging.DEBUG, logger="ir_terminal.protocol.framer"):
            frame = pack_frame(seq=5, opcode=Opcode.DATA_TEXT, payload=b"hi")
        assert len(frame) == 6 + 2 + 6
        assert "TX pack" in caplog.text
        assert "seq=5" in caplog.text

    def test_len_error_counter_and_warning(self, caplog: LogCaptureFixture) -> None:
        bad_header = struct.pack("<HBBH", SOF_MAGIC, 1, Opcode.DATA_TEXT, MAX_PAYLOAD_SIZE + 88)
        stats = FramerStats()
        packets, _remaining, out = parse_frames_with_stats(bad_header + b"\x00" * 8, stats)
        assert packets == []
        assert out.len_errors == 1
        assert "len_error" in caplog.text

    def test_eof_error_counter_and_warning(self, caplog: LogCaptureFixture) -> None:
        frame = bytearray(pack_frame(seq=9, opcode=Opcode.PING, payload=b"ping"))
        frame[-2:] = b"\x00\x00"  # corrupt EOF magic
        stats = FramerStats()
        packets, _remaining, out = parse_frames_with_stats(bytes(frame), stats)
        assert packets == []
        assert out.eof_errors == 1
        assert "eof_error" in caplog.text
        assert "0x0000" in caplog.text

    def test_crc_error_counter_and_warning(self, caplog: LogCaptureFixture) -> None:
        frame = bytearray(pack_frame(seq=3, opcode=Opcode.DATA_TEXT, payload=b"abcdef"))
        frame[6] ^= 0xFF  # flip first payload byte -> CRC mismatch
        stats = FramerStats()
        packets, _remaining, out = parse_frames_with_stats(bytes(frame), stats)
        assert packets == []
        assert out.crc_errors == 1
        assert "crc_error" in caplog.text
        assert "expected=0x" in caplog.text

    def test_noise_bytes_counted_and_valid_frame_recovers(self, caplog: LogCaptureFixture) -> None:
        frame = pack_frame(seq=11, opcode=Opcode.PONG, payload=b"ok")
        stats = FramerStats()
        with caplog.at_level(logging.DEBUG, logger="ir_terminal"):
            packets, _remaining, out = parse_frames_with_stats(b"\x00\xff\x00" + frame, stats)
        assert len(packets) == 1
        assert packets[0].seq == 11
        assert out.noise_bytes == 3
        assert out.frames_ok == 1


class TestPayloadLogging:
    """Tests binary payload unpack failure logs."""

    def test_file_start_too_short(self, caplog: LogCaptureFixture) -> None:
        with pytest.raises(ValueError):
            FileStartPayload.unpack(b"\x00" * 5)
        assert "FileStartPayload" in caplog.text

    def test_file_start_truncated_filename(self, caplog: LogCaptureFixture) -> None:
        payload = FileStartPayload(
            file_size=10, chunk_size=240, total_chunks=1, file_crc32=1, filename="abcdef"
        ).pack()
        with pytest.raises(ValueError):
            FileStartPayload.unpack(payload[:-2])
        assert "truncated" in caplog.text.lower()

    def test_file_start_long_filename_warns_and_truncates(self, caplog: LogCaptureFixture) -> None:
        payload = FileStartPayload(
            file_size=10, chunk_size=240, total_chunks=1, file_crc32=1, filename="n" * 300
        ).pack()
        assert "truncated" in caplog.text.lower()
        assert payload[12] == 255

    def test_chunk_end_ack_too_short(self, caplog: LogCaptureFixture) -> None:
        with pytest.raises(ValueError):
            FileChunkPayload.unpack(b"\x00")
        with pytest.raises(ValueError):
            FileEndPayload.unpack(b"\x00" * 3)
        with pytest.raises(ValueError):
            AckPayload.unpack(b"\x00")
        assert "FAILED" in caplog.text


class TestSerialWorkerLogging:
    """Tests worker overflow accounting and TX/RX descriptors."""

    def test_rx_overflow_drops_oldest_and_warns(self, caplog: LogCaptureFixture) -> None:
        _ensure_app()
        worker = SerialWorker()
        with caplog.at_level(logging.WARNING):
            worker._append_rx_chunk(b"X" * (RING_BUFFER_CAPACITY + 100))
        assert worker.rx_dropped_bytes == 100
        assert "overflow" in caplog.text
        assert "total_dropped=100" in caplog.text.replace(" ", "")

    def test_describe_tx_bytes_sof_and_raw(self, caplog: LogCaptureFixture) -> None:
        _ensure_app()
        worker = SerialWorker()
        frame = pack_frame(seq=4, opcode=Opcode.PING, payload=b"hi")
        with caplog.at_level(logging.DEBUG, logger="ir_terminal"):
            worker._describe_tx_bytes(frame)
            worker._describe_tx_bytes(b"\x00\x01")
        assert "TX bytes" in caplog.text
        assert "no SOF header" in caplog.text

    def test_send_bytes_without_connection_warns(self, caplog: LogCaptureFixture) -> None:
        _ensure_app()
        worker = SerialWorker()
        errors: list[str] = []
        worker.error_occurred.connect(errors.append)
        with caplog.at_level(logging.WARNING):
            worker.send_bytes(b"\x55\xaa\x00")
        assert errors == ["Cannot send data: Serial port is not connected"]
        assert "not connected" in caplog.text


class TestSerialManagerLogging:
    """Tests manager packet logging and pack failure tracing."""

    def test_send_packet_invalid_seq_raises_and_logs(self, caplog: LogCaptureFixture) -> None:
        _ensure_app()
        manager = SerialManager()
        try:
            with pytest.raises(ValueError):
                manager.send_packet(seq=999, opcode=Opcode.PING, payload=b"x")
        finally:
            manager.shutdown()
        assert "pack FAILED" in caplog.text


class TestChatLogging:
    """Tests chat service log context."""

    def test_empty_message_skipped_with_debug(self, caplog: LogCaptureFixture) -> None:
        _ensure_app()
        manager = SerialManager()
        try:
            chat = ChatService(serial_manager=manager)
            with caplog.at_level(logging.DEBUG, logger="ir_terminal"):
                chat.send_message("   ")
            assert "skipped" in caplog.text
        finally:
            manager.shutdown()

    def test_undecodable_payload_falls_back_to_hex(self, caplog: LogCaptureFixture) -> None:
        _ensure_app()
        manager = SerialManager()
        try:
            chat = ChatService(serial_manager=manager, storage_service=StorageService())
            received: list[tuple[object, ...]] = []
            chat.message_received.connect(lambda *a: received.append(a))
            bad = _make_packet(6, Opcode.DATA_TEXT, b"\xff\xfe\xfd")
            with caplog.at_level(logging.WARNING):
                chat.handle_packet(bad)
            assert len(received) == 1
            assert "hex" in caplog.text.lower()
        finally:
            manager.shutdown()

    def test_non_packet_and_foreign_opcode_ignored(self, caplog: LogCaptureFixture) -> None:
        _ensure_app()
        manager = SerialManager()
        try:
            chat = ChatService(serial_manager=manager)
            with caplog.at_level(logging.DEBUG, logger="ir_terminal"):
                chat.handle_packet("not-a-packet")
                chat.handle_packet(_make_packet(1, Opcode.PING, b"x"))
            assert "non-Packet" in caplog.text
            assert "ignored opcode" in caplog.text
        finally:
            manager.shutdown()


class TestFileTransferLogging:
    """Tests ARQ state transition and dispatch logs."""

    def _service(
        self,
    ) -> tuple[SerialManager, FileTransferService, tempfile.TemporaryDirectory[str]]:
        _ensure_app()
        manager = SerialManager()
        tmp = tempfile.TemporaryDirectory()
        storage = StorageService(data_dir=Path(tmp.name) / "data")
        service = FileTransferService(
            serial_manager=manager,
            storage_service=storage,
            transfers_dir=Path(tmp.name) / "transfers",
        )
        return manager, service, tmp

    def test_state_transition_logs_old_to_new(self, caplog: LogCaptureFixture) -> None:
        manager, service, tmp = self._service()
        try:
            with caplog.at_level(logging.INFO):
                service._set_state(TransferState.HANDSHAKING)
            assert "IDLE -> HANDSHAKING" in caplog.text
            assert service.state == TransferState.HANDSHAKING
        finally:
            manager.shutdown()
            tmp.cleanup()

    def test_non_packet_dispatch_ignored(self, caplog: LogCaptureFixture) -> None:
        manager, service, tmp = self._service()
        try:
            with caplog.at_level(logging.DEBUG, logger="ir_terminal"):
                service._handle_packet(object())
            assert "non-Packet" in caplog.text
        finally:
            manager.shutdown()
            tmp.cleanup()

    def test_chunk_out_of_state_warns(self, caplog: LogCaptureFixture) -> None:
        manager, service, tmp = self._service()
        try:
            pkt = _make_packet(1, Opcode.DATA_CHUNK, b"\x00\x00data")
            with caplog.at_level(logging.WARNING):
                service._handle_packet(pkt)
            assert "out-of-state" in caplog.text
        finally:
            manager.shutdown()
            tmp.cleanup()

    def test_unknown_opcode_dispatch_ignored(self, caplog: LogCaptureFixture) -> None:
        manager, service, tmp = self._service()
        try:
            with caplog.at_level(logging.DEBUG, logger="ir_terminal"):
                service._handle_packet(_make_packet(2, 0xFF, b""))
            assert "Ignored opcode" in caplog.text
        finally:
            manager.shutdown()
            tmp.cleanup()


class TestTelemetryLogging:
    """Tests telemetry loss/RTT/reset diagnostics."""

    def _telemetry(self) -> tuple[TelemetryService, tempfile.TemporaryDirectory[str]]:
        _ensure_app()
        tmp = tempfile.TemporaryDirectory()
        storage = StorageService(data_dir=Path(tmp.name) / "data")
        return TelemetryService(storage_service=storage), tmp

    def test_framer_error_counted_and_logged(self, caplog: LogCaptureFixture) -> None:
        telemetry, tmp = self._telemetry()
        try:
            with caplog.at_level(logging.WARNING):
                telemetry.record_framer_error("crc_errors", 2)
            assert telemetry.total_packets_lost == 2
            assert "crc_errors x2" in caplog.text
        finally:
            tmp.cleanup()

    def test_packet_lost_logs_running_rate(self, caplog: LogCaptureFixture) -> None:
        telemetry, tmp = self._telemetry()
        try:
            telemetry.record_packet_sent(seq=1, opcode=Opcode.PING, length=7)
            with caplog.at_level(logging.WARNING):
                telemetry.record_packet_lost()
            assert telemetry.total_packets_lost == 1
            assert "loss=" in caplog.text
        finally:
            tmp.cleanup()

    def test_rtt_clamped_to_minimum(self, caplog: LogCaptureFixture) -> None:
        telemetry, tmp = self._telemetry()
        try:
            with caplog.at_level(logging.DEBUG, logger="ir_terminal"):
                telemetry.record_rtt(-5.0)
            assert telemetry.last_rtt_ms == 0.1
            assert "RTT update" in caplog.text
        finally:
            tmp.cleanup()

    def test_reset_logs_previous_totals(self, caplog: LogCaptureFixture) -> None:
        telemetry, tmp = self._telemetry()
        try:
            telemetry.record_packet_sent(seq=1, opcode=Opcode.PING, length=7)
            with caplog.at_level(logging.INFO):
                telemetry.reset_counters()
            assert telemetry.total_packets_sent == 0
            assert "was" in caplog.text
        finally:
            tmp.cleanup()


class TestStorageLogging:
    """Tests storage diagnostics for corrupt and missing stores."""

    def test_missing_store_logs_debug_and_returns_empty(self, caplog: LogCaptureFixture) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = StorageService(data_dir=Path(tmpdir) / "data")
            with caplog.at_level(logging.DEBUG, logger="ir_terminal"):
                assert storage.get_chat_history() == []
            assert "Store miss" in caplog.text

    def test_corrupt_store_backed_up_and_emptied(self, caplog: LogCaptureFixture) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            storage = StorageService(data_dir=data_dir)
            (data_dir / "chat_history.json").write_text("{not-json", encoding="utf-8")
            with caplog.at_level(logging.WARNING):
                assert storage.get_chat_history() == []
            assert "Corrupt store" in caplog.text
            backups = list(data_dir.glob("chat_history.json.corrupt-*"))
            assert len(backups) == 1


class TestBridgeLogging:
    """Tests bridge link and packet dispatch logs."""

    def test_link_up_down_and_non_packet_logged(self, caplog: LogCaptureFixture) -> None:
        _ensure_app()
        manager = SerialManager()
        try:
            bridge = BackendBridge(serial_manager=manager)
            with caplog.at_level(logging.DEBUG, logger="ir_terminal"):
                bridge._on_link_up("COM9", "COM10", 115200)
                bridge._on_packet_received(object())
                bridge._on_link_down()
            assert "link UP" in caplog.text
            assert "non-Packet" in caplog.text
            assert "link DOWN" in caplog.text
            bridge.shutdown()
        finally:
            manager.shutdown()
