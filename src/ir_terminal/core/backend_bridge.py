"""QWebChannel Bridge exposing Python Core Engine & Services to Web UI JavaScript."""

from __future__ import annotations

import json
import logging
import time
from typing import ClassVar

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QFileDialog

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import Opcode
from ir_terminal.protocol.packet import Packet
from ir_terminal.services.chat_service import ChatService
from ir_terminal.services.file_transfer_service import FileTransferService
from ir_terminal.services.storage_service import StorageService
from ir_terminal.utils.logger import LogTag

logger = logging.getLogger(__name__)


class BackendBridge(QObject):
    """Exposes methods and signals to Chromium JavaScript via QWebChannel."""

    # Outgoing Signals to JavaScript
    portConnected: ClassVar[Signal] = Signal(str, int)  # (port, baud)
    portDisconnected: ClassVar[Signal] = Signal()
    errorOccurred: ClassVar[Signal] = Signal(str)
    pingResult: ClassVar[Signal] = Signal(float, int)  # (rtt_ms, seq)
    rawRxData: ClassVar[Signal] = Signal(str)  # hex string
    rawTxData: ClassVar[Signal] = Signal(str)  # hex string

    # Chat Signals
    chatMessageReceived: ClassVar[Signal] = Signal(str, str, bool)  # (text, time, is_outgoing)
    chatFrameInspected: ClassVar[Signal] = Signal(str, int, int, int)  # (hex, seq, cmd, len)

    # File Transfer Signals
    transferProgress: ClassVar[Signal] = Signal(
        int, float, float, int, int
    )  # (pct, speed_kbps, eta_sec, cur_chunk, total_chunks)
    transferCompleted: ClassVar[Signal] = Signal(
        str, int, float, str
    )  # (filename, bytes_sent, elapsed_sec, crc_hex)
    transferFailed: ClassVar[Signal] = Signal(str)  # (reason)
    transferStateChanged: ClassVar[Signal] = Signal(str)  # (state)

    def __init__(
        self,
        serial_manager: SerialManager,
        storage_service: StorageService | None = None,
    ) -> None:
        super().__init__()
        self._serial_manager = serial_manager
        self._storage = storage_service or StorageService()
        self._chat_service = ChatService(self._serial_manager, self._storage)
        self._file_service = FileTransferService(
            self._serial_manager, storage_service=self._storage
        )

        self._last_ping_time: float = 0.0
        self._ping_seq: int = 1

        self._wire_signals()

    def _wire_signals(self) -> None:
        # Serial Manager
        self._serial_manager.connected.connect(self.portConnected)
        self._serial_manager.disconnected.connect(self.portDisconnected)
        self._serial_manager.error_occurred.connect(self.errorOccurred)
        self._serial_manager.raw_rx.connect(self._on_raw_rx)
        self._serial_manager.raw_tx.connect(self._on_raw_tx)
        self._serial_manager.packet_received.connect(self._on_packet_received)

        # Chat Service
        self._chat_service.message_received.connect(self.chatMessageReceived)
        self._chat_service.frame_inspected.connect(self.chatFrameInspected)

        # File Transfer Service
        self._file_service.progress_updated.connect(self.transferProgress)
        self._file_service.transfer_completed.connect(self.transferCompleted)
        self._file_service.transfer_failed.connect(self.transferFailed)
        self._file_service.state_changed.connect(self.transferStateChanged)

    def _on_raw_rx(self, data: bytes) -> None:
        self.rawRxData.emit(data.hex(" ").upper())

    def _on_raw_tx(self, data: bytes) -> None:
        self.rawTxData.emit(data.hex(" ").upper())

    def _on_packet_received(self, pkt: object) -> None:
        if not isinstance(pkt, Packet):
            return

        if pkt.opcode == Opcode.PONG:
            rtt_ms = (
                (time.time() - self._last_ping_time) * 1000.0 if self._last_ping_time > 0 else 32.0
            )
            self.pingResult.emit(rtt_ms, pkt.seq)

    # Slots callable from JavaScript
    @Slot(result=str)
    def scanPorts(self) -> str:
        """Returns JSON list of available COM ports."""
        ports = self._serial_manager.get_port_list()
        return json.dumps(ports)

    @Slot(str, int)
    def connectPort(self, port_name: str, baudrate: int) -> None:
        """Connects to specified Serial or Mock COM port."""
        logger.info(
            "%s Connect requested: port=%s baudrate=%d",
            LogTag.HW_SERIAL,
            port_name,
            baudrate,
        )
        self._serial_manager.connect_port(port_name, baudrate)

    @Slot()
    def disconnectPort(self) -> None:
        """Disconnects current Serial connection."""
        logger.info("%s Disconnect requested by UI", LogTag.HW_SERIAL)
        self._serial_manager.disconnect_port()

    @Slot()
    def sendPing(self) -> None:
        """Transmits PING frame over optical link."""
        self._ping_seq = (self._ping_seq + 1) % 256
        self._last_ping_time = time.time()
        self._serial_manager.send_packet(seq=self._ping_seq, opcode=Opcode.PING, payload=b"IR_PING")
        logger.info("%s Transmitted PING frame (Seq #%d)", LogTag.PROTO_FRAME, self._ping_seq)

    @Slot(str)
    def sendChat(self, text: str) -> None:
        """Transmits chat message via ChatService."""
        self._chat_service.send_message(text)

    @Slot(str)
    def startFileTransfer(self, file_path: str) -> None:
        """Starts optical file transfer."""
        self._file_service.start_transfer(file_path)

    @Slot()
    def abortFileTransfer(self) -> None:
        """Aborts current optical file transfer."""
        self._file_service.abort_transfer()

    @Slot(result=str)
    def openFileDialog(self) -> str:
        """Opens native file chooser dialog and returns selected path."""
        path, _ = QFileDialog.getOpenFileName(
            None,
            "Select File for Optical Transmission",
            "",
            "All Files (*.*);;Documents (*.pdf;*.docx;*.txt);;Images (*.png;*.jpg;*.bin)",
        )
        return path

    @Slot(result=str)
    def getChatHistory(self) -> str:
        """Retrieves durable chat history from SQLite database as JSON string."""
        history = self._storage.get_chat_history(limit=200)
        return json.dumps(history)

    @Slot(result=str)
    def getFileHistory(self) -> str:
        """Retrieves durable file transfer logs from SQLite database as JSON string."""
        transfers = self._storage.get_file_transfers(limit=50)
        return json.dumps(transfers)
