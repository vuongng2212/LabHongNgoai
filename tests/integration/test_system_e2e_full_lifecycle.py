"""Comprehensive End-to-End (E2E) System Lifecycle Test Suite for IR-OWC Lab Terminal."""

from __future__ import annotations

import tempfile
from pathlib import Path

from pytestqt.qtbot import QtBot

from ir_terminal.core.mock_serial import MockSerialPort
from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.services.storage_service import StorageService
from ir_terminal.ui.backend_bridge import BackendBridge


class TestSystemE2EFullLifecycle:
    """E2E Verification of Hardware Connect, Chat, File ARQ, Resilience, and Storage."""

    def test_full_system_lifecycle_e2e(self, qtbot: QtBot) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "e2e_data"
            storage = StorageService(data_dir=data_dir)
            manager = SerialManager()

            bridge = BackendBridge(serial_manager=manager, storage_service=storage)

            with qtbot.waitSignal(bridge.portConnected, timeout=1000) as conn_blocker:
                bridge.connectPort("MOCK_COM_E2E", 115200)

            tx_port, rx_port, baud = conn_blocker.args
            assert "MOCK" in tx_port
            assert tx_port == rx_port
            assert baud == 115200

            with qtbot.waitSignal(bridge.pingResult, timeout=1000) as ping_blocker:
                bridge.sendPing()

            rtt_ms, ping_seq = ping_blocker.args
            assert rtt_ms >= 0.0
            assert ping_seq > 0

            with qtbot.waitSignal(bridge.chatMessageReceived, timeout=1000) as chat_blocker:
                bridge.sendChat("E2E Optical Verification Test - 880nm NIR Link Active")

            text, time_str, is_outgoing = chat_blocker.args
            assert text == "E2E Optical Verification Test - 880nm NIR Link Active"
            assert is_outgoing is True

            chat_history = storage.get_chat_history()
            assert len(chat_history) >= 1
            assert chat_history[-1]["text"] == text

            sample_file = Path(tmpdir) / "payload_e2e.bin"
            file_payload_bytes = b"OPTICAL_PACKET_STREAM_CHUNK_" * 120  # ~3.3 KB (14 chunks)
            sample_file.write_bytes(file_payload_bytes)

            progress_events: list[int] = []

            def on_progress(pct: int, speed: float, eta: float, cur: int, total: int) -> None:
                progress_events.append(pct)

            bridge.transferProgress.connect(on_progress)

            with qtbot.waitSignal(bridge.transferCompleted, timeout=5000) as file_blocker:
                bridge.startFileTransfer(str(sample_file))

            fname, bytes_sent, elapsed, crc_hex = file_blocker.args
            assert fname == "payload_e2e.bin"
            assert bytes_sent == len(file_payload_bytes)
            assert elapsed > 0.0
            assert crc_hex.startswith("0x")
            assert len(progress_events) >= 10
            assert progress_events[-1] == 100

            transfers = storage.get_file_transfers()
            assert len(transfers) >= 1
            assert transfers[0]["filename"] == "payload_e2e.bin"
            assert transfers[0]["size_bytes"] == len(file_payload_bytes)
            assert transfers[0]["status"] == "SUCCESS"

            worker = manager._worker
            if worker and isinstance(worker._transport, MockSerialPort):
                mock_port = worker._transport
                mock_port.set_los_blocked(True)

                blocked_file = Path(tmpdir) / "blocked.bin"
                blocked_file.write_bytes(b"DATA" * 50)

                with qtbot.waitSignal(bridge.transferFailed, timeout=5000) as fail_blocker:
                    bridge.startFileTransfer(str(blocked_file))

                (fail_reason,) = fail_blocker.args
                assert "timeout" in fail_reason.lower() or "retries" in fail_reason.lower()

                mock_port.set_los_blocked(False)
                with qtbot.waitSignal(bridge.pingResult, timeout=1000):
                    bridge.sendPing()

            with qtbot.waitSignal(bridge.portDisconnected, timeout=1000):
                bridge.disconnectPort()

            manager.shutdown()
