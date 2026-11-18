"""Unit tests for the frozen chat compatibility surface."""

from __future__ import annotations

import pytest

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.packet import WinFormPacket
from ir_terminal.services.chat_service import CHAT_DISABLED_REASON, ChatService


class TestChatService:
    """Verifies that chat remains disabled while its signal contract survives."""

    def test_send_message_is_rejected(self) -> None:
        manager = SerialManager()
        service = ChatService(manager)

        with pytest.raises(RuntimeError, match=CHAT_DISABLED_REASON):
            service.send_message("Hello optical station")

        manager.shutdown()

    def test_handle_packet_emits_nothing(self) -> None:
        manager = SerialManager()
        service = ChatService(manager)
        emitted: list[tuple[str, str, bool]] = []
        service.message_received.connect(lambda *args: emitted.append(args))

        service.handle_packet(WinFormPacket(sequence=0, length=3, data=b"abc"))

        assert emitted == []
        manager.shutdown()

    def test_signal_contract_remains_connectable(self) -> None:
        manager = SerialManager()
        service = ChatService(manager)

        service.frame_inspected.connect(lambda *args: None)
        service.message_truncated.connect(lambda *args: None)

        manager.shutdown()
