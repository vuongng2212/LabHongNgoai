"""Integration test for simultaneous concurrent chat messages and file transfers."""

from __future__ import annotations

import tempfile
from pathlib import Path

from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.services.chat_service import ChatService
from ir_terminal.services.file_transfer_service import (
    FileTransferService,
    TransferState,
)


class TestConcurrentChatAndFileTransfer:
    """Verifies that chat messages and file transfer ARQ engine run concurrently without interference."""

    def test_simultaneous_chat_and_file_transfer(self, qtbot: QtBot) -> None:
        """Sends chat messages while file transfer chunks are streaming in the background."""
        manager = SerialManager()
        manager.connect_port("MOCK_COM_LOOPBACK", 115200)

        chat_service = ChatService(manager)
        file_service = FileTransferService(manager, chunk_size=64)

        # Create multi-chunk test file
        test_content = b"Concurrent Chat and File Transfer Optical Test\n" * 20
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
            tmp.write(test_content)
            tmp_path = tmp.name

        chat_received_messages: list[str] = []
        chat_service.message_received.connect(
            lambda text, _ts, _out: chat_received_messages.append(text)
        )

        try:
            # Start file transfer
            file_service.start_transfer(tmp_path)

            # Send multiple chat messages concurrently
            chat_service.send_message("Chat message 1 during file transfer")
            chat_service.send_message("Chat message 2 during file transfer")

            # Wait for file transfer to complete
            with qtbot.waitSignal(file_service.transfer_completed, timeout=10000) as blocker:
                pass

            filename, bytes_sent, _, _ = blocker.args
            assert filename == Path(tmp_path).name
            assert bytes_sent == len(test_content)
            assert file_service.state == TransferState.COMPLETED

            # Verify chat messages were sent and recorded
            assert "Chat message 1 during file transfer" in chat_received_messages
            assert "Chat message 2 during file transfer" in chat_received_messages

        finally:
            Path(tmp_path).unlink(missing_ok=True)
            manager.shutdown()
