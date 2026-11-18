"""End-to-end integration tests for optical chat over virtual file envelopes."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, cast

from PySide6.QtCore import QObject, Signal
from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import SEQ_END, SEQ_HEADER
from ir_terminal.protocol.framer import pack_packet, parse_stream, unpack_header_payload
from ir_terminal.services.chat_service import ChatService
from ir_terminal.services.file_transfer_service import FileTransferService, is_chat_envelope_name
from ir_terminal.services.storage_service import StorageService


class ChatLoopbackManager(QObject):
    """Bridges outgoing frames directly to peer receiver."""

    packet_received: ClassVar[Signal] = Signal(object)

    def __init__(self, baud: int = 19200, peer: ChatLoopbackManager | None = None) -> None:
        super().__init__()
        self.current_baud = baud
        self.peer = peer
        self.sent: list[tuple[int, bytes]] = []

    def send_packet(self, sequence: int, data: bytes = b"") -> None:
        payload = bytes(data)
        self.sent.append((sequence, payload))
        if self.peer is None:
            return
        packets, _ = parse_stream(bytearray(pack_packet(sequence, payload)))
        for packet in packets:
            self.peer.packet_received.emit(packet)


def make_chat_node(
    tmp_path: Path, name: str, local_station: str, remote_station: str, peer: ChatLoopbackManager | None
) -> tuple[ChatService, FileTransferService, ChatLoopbackManager, StorageService]:
    """Initializes one chat node with isolated storage and transfer directories."""
    storage = StorageService(data_dir=tmp_path / name / "data")
    manager = ChatLoopbackManager(peer=peer)
    file_service = FileTransferService(
        cast(SerialManager, manager),
        storage_service=storage,
        transfers_dir=tmp_path / name / "transfers",
    )
    chat_service = ChatService(
        serial_manager=cast(SerialManager, manager),
        storage_service=storage,
        local_name=local_station,
        remote_name=remote_station,
        file_transfer_service=file_service,
    )
    return chat_service, file_service, manager, storage


class TestChatEnvelopeE2E:
    """Verifies complete round-trip chat streaming between two optical stations."""

    def test_bidirectional_chat_over_optical_channel(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        chat_a, file_a, mgr_a, storage_a = make_chat_node(
            tmp_path, "node_a", "Station_Alpha", "Station_Bravo", None
        )
        chat_b, file_b, mgr_b, storage_b = make_chat_node(
            tmp_path, "node_b", "Station_Bravo", "Station_Alpha", mgr_a
        )
        mgr_a.peer = mgr_b

        rx_messages_b: list[tuple[str, str, bool]] = []
        chat_b.message_received.connect(lambda *args: rx_messages_b.append(args))

        msg_a_to_b = "Xin chào trạm B từ trạm A! Kiểm tra quang 880nm 🚀"

        with qtbot.waitSignal(chat_b.message_received, timeout=5000):
            success = chat_a.send_message(msg_a_to_b)

        assert success is True
        assert len(rx_messages_b) == 1
        text_received, _, is_outgoing = rx_messages_b[0]
        assert text_received == msg_a_to_b
        assert is_outgoing is False

        sequences = [seq for seq, _ in mgr_a.sent]
        assert sequences == [SEQ_HEADER, 0, SEQ_END]

        header_payload = mgr_a.sent[0][1]
        announced_name, total_packets = unpack_header_payload(header_payload)
        assert is_chat_envelope_name(announced_name) is True
        assert "Station_Alpha" in announced_name
        assert total_packets == 1

        assert mgr_a.sent[1][1] == msg_a_to_b.encode("utf-8")
        assert mgr_a.sent[2][1] == b""

        dir_a_files = list((tmp_path / "node_a" / "transfers").glob("*"))
        dir_b_files = list((tmp_path / "node_b" / "transfers").glob("*"))
        assert dir_a_files == []
        assert dir_b_files == []

        rx_messages_a: list[tuple[str, str, bool]] = []
        chat_a.message_received.connect(lambda *args: rx_messages_a.append(args))

        msg_b_to_a = "Chào trạm A, đã nhận thông điệp rõ ràng."
        with qtbot.waitSignal(chat_a.message_received, timeout=5000):
            success_reply = chat_b.send_message(msg_b_to_a)

        assert success_reply is True
        assert len(rx_messages_a) == 1
        reply_received, _, is_outgoing_reply = rx_messages_a[0]
        assert reply_received == msg_b_to_a
        assert is_outgoing_reply is False

        history_a = storage_a.get_chat_history()
        history_b = storage_b.get_chat_history()
        assert len(history_a) == 2
        assert len(history_b) == 2

    def test_multi_chunk_chat_message(self, qtbot: QtBot, tmp_path: Path) -> None:
        chat_a, file_a, mgr_a, _ = make_chat_node(
            tmp_path, "node_a", "Station_Alpha", "Station_Bravo", None
        )
        chat_b, file_b, mgr_b, _ = make_chat_node(
            tmp_path, "node_b", "Station_Bravo", "Station_Alpha", mgr_a
        )
        mgr_a.peer = mgr_b

        rx_messages_b: list[tuple[str, str, bool]] = []
        chat_b.message_received.connect(lambda *args: rx_messages_b.append(args))

        long_msg = "Đây là thông điệp chat quang học nhiều phân đoạn. " * 15
        assert len(long_msg.encode("utf-8")) > 500

        with qtbot.waitSignal(chat_b.message_received, timeout=8000):
            success = chat_a.send_message(long_msg)

        assert success is True
        assert len(rx_messages_b) == 1
        assert rx_messages_b[0][0] == long_msg

        sequences = [seq for seq, _ in mgr_a.sent]
        assert sequences == [SEQ_HEADER, 0, 1, 2, 3, SEQ_END]

        dir_b_files = list((tmp_path / "node_b" / "transfers").glob("*"))
        assert dir_b_files == []
