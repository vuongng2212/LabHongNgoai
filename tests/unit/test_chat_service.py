"""Unit tests for optical realtime chat service."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import (
    EOF_MAGIC,
    MAX_PAYLOAD_SIZE,
    SOF_MAGIC,
    Opcode,
)
from ir_terminal.protocol.packet import Header, Packet
from ir_terminal.services.chat_service import ChatService


class TestChatService:
    """Tests optical chat text transmission and frame inspection."""

    def test_send_chat_message(self, qtbot: QtBot) -> None:
        """Verifies sending valid chat message and emitting inspection signals."""
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

    def test_send_empty_or_whitespace_chat_message_ignored(self) -> None:
        """Verifies empty or whitespace-only strings do not trigger transmission."""
        manager = SerialManager()
        chat_service = ChatService(manager)

        received_events: list[object] = []
        chat_service.message_received.connect(lambda *args: received_events.append(args))

        chat_service.send_message("")
        chat_service.send_message("   \t\n  ")
        assert len(received_events) == 0
        assert chat_service._seq == 0

        manager.shutdown()

    def test_send_oversized_chat_message_truncated_safely(self, qtbot: QtBot) -> None:
        """Verifies that sending a message larger than MAX_PAYLOAD_SIZE is safely truncated."""
        manager = SerialManager()
        chat_service = ChatService(manager)

        oversized_text = "A" * (MAX_PAYLOAD_SIZE + 200)

        with qtbot.waitSignal(chat_service.message_received, timeout=1000) as blocker:
            chat_service.send_message(oversized_text)

        text, _, is_outgoing = blocker.args
        assert len(text.encode("utf-8")) <= MAX_PAYLOAD_SIZE
        assert is_outgoing is True
        assert chat_service._seq == 1

        manager.shutdown()

    def test_handle_incoming_chat_packet(self, qtbot: QtBot) -> None:
        """Verifies handling a valid incoming UTF-8 chat packet."""
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

    def test_handle_incoming_corrupted_utf8_payload(self, qtbot: QtBot) -> None:
        """Verifies fallback to hex display when incoming payload is invalid UTF-8."""
        manager = SerialManager()
        chat_service = ChatService(manager)

        invalid_utf8 = b"\x80\x81\xff\xfe"
        header = Header(
            sof=SOF_MAGIC,
            seq=43,
            opcode=Opcode.DATA_TEXT,
            length=len(invalid_utf8),
        )
        packet = Packet(
            header=header,
            payload=invalid_utf8,
            crc32=0x12345678,
            eof=EOF_MAGIC,
        )

        with qtbot.waitSignal(chat_service.message_received, timeout=1000) as blocker:
            chat_service.handle_packet(packet)

        text, _, is_outgoing = blocker.args
        assert text == invalid_utf8.hex(" ")
        assert is_outgoing is False

        manager.shutdown()

    def test_handle_non_packet_or_other_opcodes_ignored(self) -> None:
        """Verifies ChatService ignores non-Packet objects and non-DATA_TEXT opcodes."""
        manager = SerialManager()
        chat_service = ChatService(manager)

        received_events: list[object] = []
        chat_service.message_received.connect(lambda *args: received_events.append(args))

        chat_service.handle_packet("NOT_A_PACKET")
        assert len(received_events) == 0

        header = Header(sof=SOF_MAGIC, seq=1, opcode=Opcode.PING, length=4)
        ping_pkt = Packet(header=header, payload=b"PING", crc32=0, eof=EOF_MAGIC)
        chat_service.handle_packet(ping_pkt)
        assert len(received_events) == 0

        manager.shutdown()
