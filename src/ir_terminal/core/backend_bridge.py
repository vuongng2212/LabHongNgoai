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
from ir_terminal.services.telemetry_service import TelemetryService
from ir_terminal.utils.logger import LogTag
from ir_terminal.utils.path_helpers import get_logs_dir

logger = logging.getLogger(__name__)


class BackendBridge(QObject):
    """Exposes methods and signals to Chromium JavaScript via QWebChannel."""

    # Outgoing Link & Hardware Signals to JavaScript
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

    # Telemetry & Sniffer Signals
    telemetryUpdated: ClassVar[Signal] = Signal(
        float, float, float, int, int
    )  # (speed_kbps, loss_pct, rtt_ms, chunks_count, frames_count)
    snifferFrameLogged: ClassVar[Signal] = Signal(
        str, str, str, str, str, str, float
    )  # (time_str, frame_id, carrier, type_str, payload_preview, checksum_status, latency_ms)

    def __init__(
        self,
        serial_manager: SerialManager,
        storage_service: StorageService | None = None,
        telemetry_service: TelemetryService | None = None,
    ) -> None:
        """Initializes backend bridge connecting core services to Web UI.

        Args:
            serial_manager: Active SerialManager instance.
            storage_service: Optional StorageService instance.
            telemetry_service: Optional TelemetryService instance.
        """
        super().__init__()
        self._serial_manager = serial_manager
        self._storage = storage_service or StorageService()
        self._chat_service = ChatService(self._serial_manager, self._storage)
        self._file_service = FileTransferService(
            self._serial_manager, storage_service=self._storage
        )
        self._telemetry = telemetry_service or TelemetryService(storage_service=self._storage)

        self._last_ping_time: float = 0.0
        self._ping_seq: int = 1

        self._wire_signals()

    def _wire_signals(self) -> None:
        """Connects underlying core services to bridge signals and telemetry listeners."""
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
        self._file_service.progress_updated.connect(self._on_transfer_progress)
        self._file_service.transfer_completed.connect(self.transferCompleted)
        self._file_service.transfer_failed.connect(self._on_transfer_failed)
        self._file_service.state_changed.connect(self.transferStateChanged)

        # Telemetry Service
        self._telemetry.telemetry_updated.connect(self.telemetryUpdated)
        self._telemetry.sniffer_frame_logged.connect(self.snifferFrameLogged)

    def _on_raw_rx(self, data: bytes) -> None:
        """Handles incoming raw bytes from transport."""
        self._telemetry.record_bytes_received(len(data))
        self.rawRxData.emit(data.hex(" ").upper())

    def _on_raw_tx(self, data: bytes) -> None:
        """Handles outgoing raw bytes to transport."""
        self._telemetry.record_bytes_sent(len(data))
        self.rawTxData.emit(data.hex(" ").upper())

    def _on_packet_received(self, pkt: object) -> None:
        """Handles parsed incoming packet for RTT and telemetry tracking."""
        if not isinstance(pkt, Packet):
            return

        rtt_ms = 0.0
        if pkt.opcode == Opcode.PONG:
            rtt_ms = (
                (time.time() - self._last_ping_time) * 1000.0 if self._last_ping_time > 0 else 32.0
            )
            self._telemetry.record_rtt(rtt_ms)
            self.pingResult.emit(rtt_ms, pkt.seq)

        self._telemetry.record_packet_received(pkt, latency_ms=rtt_ms)

    def _on_transfer_progress(
        self, pct: int, speed_kbps: float, eta_sec: float, cur_chunk: int, total_chunks: int
    ) -> None:
        """Forwards transfer progress and registers chunk in telemetry."""
        self._telemetry.record_chunk_transferred()
        self.transferProgress.emit(pct, speed_kbps, eta_sec, cur_chunk, total_chunks)

    def _on_transfer_failed(self, reason: str) -> None:
        """Registers packet loss on transfer failure and emits signal."""
        self._telemetry.record_packet_lost()
        self.transferFailed.emit(reason)

    @property
    def telemetry_service(self) -> TelemetryService:
        """Returns the TelemetryService instance."""
        return self._telemetry

    @property
    def chat_service(self) -> ChatService:
        """Returns the ChatService instance."""
        return self._chat_service

    @property
    def file_service(self) -> FileTransferService:
        """Returns the FileTransferService instance."""
        return self._file_service

    # Slots callable from JavaScript
    @Slot(result=str)
    def scanPorts(self) -> str:
        """Returns JSON list of available COM ports."""
        ports = self._serial_manager.get_port_list()
        return json.dumps(ports)

    @Slot(str, int)
    def connectPort(self, port_name: str, baudrate: int) -> None:
        """Connects to specified Serial or Mock COM port.

        Args:
            port_name: Target serial port device name.
            baudrate: Baud rate integer.
        """
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
        self._telemetry.record_packet_sent(self._ping_seq, Opcode.PING, len(b"IR_PING"))
        logger.info("%s Transmitted PING frame (Seq #%d)", LogTag.PROTO_FRAME, self._ping_seq)

    @Slot(str)
    def sendChat(self, text: str) -> None:
        """Transmits chat message via ChatService.

        Args:
            text: UTF-8 message content string.
        """
        self._chat_service.send_message(text)

    @Slot(str)
    def startFileTransfer(self, file_path: str) -> None:
        """Starts optical file transfer.

        Args:
            file_path: Local path to the file.
        """
        self._file_service.start_transfer(file_path)

    @Slot()
    def abortFileTransfer(self) -> None:
        """Aborts current optical file transfer."""
        self._file_service.abort_transfer()

    @Slot(result=str)
    def openFileDialog(self) -> str:
        """Opens native file chooser dialog and returns selected path.

        Returns:
            Selected file path string or empty string.
        """
        path, _ = QFileDialog.getOpenFileName(
            None,
            "Select File for Optical Transmission",
            "",
            "All Files (*.*);;Documents (*.pdf;*.docx;*.txt);;Images (*.png;*.jpg;*.bin)",
        )
        return path

    @Slot(result=str)
    def getChatHistory(self) -> str:
        """Retrieves durable chat history from SQLite database as JSON string.

        Returns:
            JSON formatted array of chat history objects.
        """
        history = self._storage.get_chat_history(limit=200)
        return json.dumps(history)

    @Slot(result=str)
    def getFileHistory(self) -> str:
        """Retrieves durable file transfer logs from SQLite database as JSON string.

        Returns:
            JSON formatted array of file transfer objects.
        """
        transfers = self._storage.get_file_transfers(limit=50)
        return json.dumps(transfers)

    @Slot(result=str)
    def getTelemetryStats(self) -> str:
        """Retrieves current telemetry summary as JSON string.

        Returns:
            JSON serialized TelemetrySummary dictionary.
        """
        summary = self._telemetry.get_summary()
        return json.dumps(summary)

    @Slot(result=str)
    def getSnifferRecords(self) -> str:
        """Retrieves recent sniffer records as JSON string.

        Returns:
            JSON serialized list of SnifferRecord items.
        """
        records = self._telemetry.get_sniffer_records(limit=100)
        return json.dumps(records)

    @Slot(result=str)
    def exportTelemetryCsv(self) -> str:
        """Exports telemetry history to CSV file via Save File dialog or default path.

        Returns:
            Destination file path string on success, or empty string on cancellation.
        """
        default_dir = str(get_logs_dir())
        dest_path, _ = QFileDialog.getSaveFileName(
            None,
            "Export Telemetry CSV",
            f"{default_dir}/telemetry_export_{int(time.time())}.csv",
            "CSV Files (*.csv);;All Files (*.*)",
        )
        if not dest_path:
            return ""

        from pathlib import Path

        self._telemetry.export_telemetry_csv(Path(dest_path))
        logger.info("%s Exported telemetry CSV to %s", LogTag.IPC_BRIDGE, dest_path)
        return dest_path

    @Slot(result=str)
    def exportTransfersCsv(self) -> str:
        """Exports file transfer history to CSV file via Save File dialog.

        Returns:
            Destination file path string on success, or empty string on cancellation.
        """
        default_dir = str(get_logs_dir())
        dest_path, _ = QFileDialog.getSaveFileName(
            None,
            "Export File Transfers CSV",
            f"{default_dir}/transfers_export_{int(time.time())}.csv",
            "CSV Files (*.csv);;All Files (*.*)",
        )
        if not dest_path:
            return ""

        from pathlib import Path

        self._storage.export_transfers_csv(Path(dest_path))
        logger.info("%s Exported file transfers CSV to %s", LogTag.IPC_BRIDGE, dest_path)
        return dest_path

    @Slot()
    def resetTelemetryCounters(self) -> None:
        """Resets telemetry metrics and sniffer counters."""
        self._telemetry.reset_counters()
        logger.info("%s Reset telemetry counters requested via UI", LogTag.IPC_BRIDGE)
