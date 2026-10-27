"""Unit tests for application entry wiring, WebMainWindow, and BackendBridge slots."""

import json

from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.ui.backend_bridge import BackendBridge


class TestAppAndUi:
    """Tests backend bridge slots, queries, and window integration."""

    def test_bridge_getters_and_window_controls(self, qtbot: QtBot) -> None:
        """Verifies BackendBridge slots for database queries and window controls."""
        manager = SerialManager()
        bridge = BackendBridge(manager)

        assert bridge.telemetry_service is not None
        assert bridge.chat_service is not None
        assert bridge.file_service is not None

        chat_hist = bridge.getChatHistory()
        assert isinstance(json.loads(chat_hist), list)

        file_hist = bridge.getFileHistory()
        assert isinstance(json.loads(file_hist), list)

        telemetry_stats = bridge.getTelemetryStats()
        stats_dict = json.loads(telemetry_stats)
        assert "throughput_kbps" in stats_dict
        assert "rtt_ms" in stats_dict

        sniffer_records = bridge.getSnifferRecords()
        assert isinstance(json.loads(sniffer_records), list)

        bridge.resetTelemetryCounters()

        with qtbot.waitSignal(bridge.closeWindowRequested, timeout=1000):
            bridge.closeWindow()

        with qtbot.waitSignal(bridge.minimizeWindowRequested, timeout=1000):
            bridge.minimizeWindow()

        with qtbot.waitSignal(bridge.maximizeWindowRequested, timeout=1000):
            bridge.toggleMaximizeWindow()

        manager.shutdown()
