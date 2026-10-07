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

        # Service properties
        assert bridge.telemetry_service is not None
        assert bridge.chat_service is not None
        assert bridge.file_service is not None

        # 1. Chat history getter
        chat_hist = bridge.getChatHistory()
        assert isinstance(json.loads(chat_hist), list)

        # 2. File history getter
        file_hist = bridge.getFileHistory()
        assert isinstance(json.loads(file_hist), list)

        # 3. Telemetry summary getter
        telemetry_stats = bridge.getTelemetryStats()
        stats_dict = json.loads(telemetry_stats)
        assert "throughput_kbps" in stats_dict
        assert "rtt_ms" in stats_dict

        # 4. Sniffer records getter
        sniffer_records = bridge.getSnifferRecords()
        assert isinstance(json.loads(sniffer_records), list)

        # 5. Reset counters
        bridge.resetTelemetryCounters()

        # 6. Window control signals
        with qtbot.waitSignal(bridge.closeWindowRequested, timeout=1000):
            bridge.closeWindow()

        with qtbot.waitSignal(bridge.minimizeWindowRequested, timeout=1000):
            bridge.minimizeWindow()

        with qtbot.waitSignal(bridge.maximizeWindowRequested, timeout=1000):
            bridge.toggleMaximizeWindow()

        manager.shutdown()
