"""Unit tests for optical realtime chat service."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import EOF_MAGIC, SOF_MAGIC, Opcode
from ir_terminal.protocol.packet import Header, Packet
from ir_terminal.services.chat_service import ChatService


class TestChatService:
    """Tests optical chat text transmission and frame inspection."""

    def test_send_chat_message(self, qtbot: QtBot) -> None:
        manager = SerialManager()
        manager.connect_port("MOCK_COM_LOOPBACK", 115200)

        chat_service = ChatService(manager)

        with qtbot.waitSignal(chat_service.message_received, timeout=1000) as blocker:
            chat_service.send_message("Hello Optical Station Bravo! 880nm")

        text, time_str, is_outgoing = blocker.args
        assert text == "Hello Optical Station Bravo! 880nm"
        assert is_outgoing is True
        assert len(time_str) > 0

        manager.shutdown()

    def test_handle_incoming_chat_packet(self, qtbot: QtBot) -> None:
        manager = SerialManager()
        chat_service = ChatService(manager)

        payload = b"Hello from Remote Transmitter"
        header = Header(sof=SOF_MAGIC, seq=42, opcode=Opcode.DATA_TEXT, length=len(payload))
        incoming_packet = Packet(
            header=header,
            payload=payload,
            crc32=0x12345678,
            eof=EOF_MAGIC,
        )

        with qtbot.waitSignal(chat_service.message_received, timeout=1000) as blocker:
            chat_service.handle_packet(incoming_packet)

        text, time_str, is_outgoing = blocker.args
        assert text == "Hello from Remote Transmitter"
        assert is_outgoing is False
        assert len(time_str) > 0

        manager.shutdown()
