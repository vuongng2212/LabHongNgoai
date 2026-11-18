"""Unit tests for the optical chat service using virtual file envelopes."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, cast

from PySide6.QtCore import QObject, Signal
from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.packet import WinFormPacket
from ir_terminal.services.chat_service import ChatService
from ir_terminal.services.file_transfer_service import FileTransferService
from ir_terminal.services.storage_service import StorageService


class FakeSerialManager(QObject):
    """Captures transmitted frames and injects received frames."""

    packet_received: ClassVar[Signal] = Signal(object)

    def __init__(self, baud: int = 19200) -> None:
        super().__init__()
        self.current_baud = baud
        self.sent: list[tuple[int, bytes]] = []

    def send_packet(self, sequence: int, data: bytes = b"") -> None:
        self.sent.append((sequence, bytes(data)))

    def deliver(self, sequence: int, data: bytes = b"") -> None:
        self.packet_received.emit(WinFormPacket(sequence=sequence, length=len(data), data=data))


def make_chat_system(
    tmp_path: Path,
) -> tuple[ChatService, FileTransferService, FakeSerialManager, StorageService]:
    """Constructs a fully wired chat and file transfer service suite."""
    storage = StorageService(data_dir=tmp_path / "data")
    fake_mgr = FakeSerialManager()
    manager = cast(SerialManager, fake_mgr)
    file_service = FileTransferService(
        serial_manager=manager,
        storage_service=storage,
        transfers_dir=tmp_path / "transfers",
    )
    chat_service = ChatService(
        serial_manager=manager,
        storage_service=storage,
        local_name="Local Station Alpha",
        remote_name="Remote Station Bravo",
        file_transfer_service=file_service,
    )
    return chat_service, file_service, fake_mgr, storage


class TestChatService:
    """Verifies transmission, reception, and persistence of chat envelopes."""

    def test_send_message_success(self, qtbot: QtBot, tmp_path: Path) -> None:
        chat_service, file_service, fake_mgr, storage = make_chat_system(tmp_path)
        emitted: list[tuple[str, str, bool]] = []
        chat_service.message_received.connect(lambda *args: emitted.append(args))

        with qtbot.waitSignal(chat_service.message_received, timeout=1000):
            result = chat_service.send_message("Xin chào trạm quang!")

        assert result is True
        assert len(emitted) == 1
        text, timestamp, is_outgoing = emitted[0]
        assert text == "Xin chào trạm quang!"
        assert is_outgoing is True
        assert len(timestamp) == 8

        history = storage.get_chat_history()
        assert len(history) == 1
        assert history[0]["text"] == "Xin chào trạm quang!"
        assert history[0]["is_outgoing"] is True

        assert len(fake_mgr.sent) >= 1

    def test_send_message_empty_rejected(self, tmp_path: Path) -> None:
        chat_service, _, _, _ = make_chat_system(tmp_path)
        assert chat_service.send_message("") is False
        assert chat_service.send_message("   ") is False

    def test_send_message_busy_rejected(self, tmp_path: Path) -> None:
        chat_service, file_service, _, _ = make_chat_system(tmp_path)
        file_path = tmp_path / "data.bin"
        file_path.write_bytes(b"X" * 1024)
        file_service.start_transfer(str(file_path))

        assert file_service.is_active is True
        assert chat_service.send_message("Channel is busy") is False

    def test_receive_virtual_envelope(self, qtbot: QtBot, tmp_path: Path) -> None:
        chat_service, file_service, _, storage = make_chat_system(tmp_path)
        emitted: list[tuple[str, str, bool]] = []
        chat_service.message_received.connect(lambda *args: emitted.append(args))

        incoming_name = "_chat_20261118_153045_Remote_Station_Bravo.txt"
        incoming_bytes = "Phản hồi từ trạm xa".encode()

        with qtbot.waitSignal(chat_service.message_received, timeout=1000):
            file_service.virtual_transfer_completed.emit(incoming_name, incoming_bytes)

        assert len(emitted) == 1
        text, timestamp, is_outgoing = emitted[0]
        assert text == "Phản hồi từ trạm xa"
        assert is_outgoing is False

        history = storage.get_chat_history()
        assert len(history) == 1
        assert history[0]["text"] == "Phản hồi từ trạm xa"
        assert history[0]["sender"] == "Remote_Station_Bravo"
        assert history[0]["is_outgoing"] is False

    def test_receive_ignores_non_chat_envelope(self, qtbot: QtBot, tmp_path: Path) -> None:
        chat_service, file_service, _, storage = make_chat_system(tmp_path)
        emitted: list[tuple[str, str, bool]] = []
        chat_service.message_received.connect(lambda *args: emitted.append(args))

        file_service.virtual_transfer_completed.emit("test_document.txt", b"plain file content")
        qtbot.wait(100)

        assert emitted == []
        assert storage.get_chat_history() == []

    def test_signal_contract_remains_connectable(self, tmp_path: Path) -> None:
        chat_service, _, _, _ = make_chat_system(tmp_path)
        chat_service.message_received.connect(lambda *args: None)
        chat_service.handle_packet(WinFormPacket(sequence=0, length=3, data=b"abc"))
