"""Stop-and-Wait ARQ Optical File Transfer Service with disk persistence and tagged IoT logs."""

from __future__ import annotations

import logging
import time
import zlib
from enum import Enum, auto
from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import QObject, QTimer, Signal

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import (
    DEFAULT_ACK_TIMEOUT_SEC,
    MAX_RETRY_COUNT,
    AckStatus,
    Opcode,
)
from ir_terminal.protocol.file_payloads import (
    AckPayload,
    FileChunkPayload,
    FileEndPayload,
    FileStartPayload,
)
from ir_terminal.protocol.packet import Packet
from ir_terminal.services.storage_service import StorageService
from ir_terminal.utils.logger import LogTag

logger = logging.getLogger(__name__)


class TransferState(Enum):
    """File transfer state machine states."""

    IDLE = auto()
    HANDSHAKING = auto()
    TRANSFERRING = auto()
    FINALIZING = auto()
    COMPLETED = auto()
    FAILED = auto()
    ABORTED = auto()


class FileTransferService(QObject):
    """Orchestrates optical file upload/download via Stop-and-Wait ARQ and records logs to disk."""

    progress_updated: ClassVar[Signal] = Signal(
        int, float, float, int, int
    )  # (pct, speed_kbps, eta_sec, current_chunk, total_chunks)
    transfer_completed: ClassVar[Signal] = Signal(
        str, int, float, str
    )  # (filename, bytes_sent, elapsed_sec, crc32_hex)
    transfer_failed: ClassVar[Signal] = Signal(str)  # (reason)
    state_changed: ClassVar[Signal] = Signal(str)  # (state_name)

    def __init__(
        self,
        serial_manager: SerialManager,
        chunk_size: int = 240,
        storage_service: StorageService | None = None,
    ) -> None:
        super().__init__()
        self._serial_manager = serial_manager
        self._chunk_size = chunk_size
        self._storage = storage_service or StorageService()

        self._state = TransferState.IDLE
        self._current_file_path: Path | None = None
        self._file_data: bytes = b""
        self._file_size: int = 0
        self._file_crc32: int = 0
        self._total_chunks: int = 0
        self._current_chunk_idx: int = 0
        self._seq: int = 0
        self._retry_count: int = 0

        self._start_time: float = 0.0
        self._last_chunk_time: float = 0.0
        self._bytes_transferred: int = 0

        # ARQ Timeout Timer
        self._ack_timer = QTimer(self)
        self._ack_timer.setSingleShot(True)
        self._ack_timer.setInterval(int(DEFAULT_ACK_TIMEOUT_SEC * 1000))
        self._ack_timer.timeout.connect(self._on_ack_timeout)

        # Connect packet reception
        self._serial_manager.packet_received.connect(self._handle_packet)

    @property
    def state(self) -> TransferState:
        return self._state

    def _set_state(self, new_state: TransferState) -> None:
        self._state = new_state
        self.state_changed.emit(new_state.name)
        logger.info("%s FileTransfer state -> %s", LogTag.ARQ_ENGINE, new_state.name)

    def start_transfer(self, file_path_str: str) -> None:
        """Starts transmitting a file over optical link."""
        if self._state not in (
            TransferState.IDLE,
            TransferState.COMPLETED,
            TransferState.FAILED,
            TransferState.ABORTED,
        ):
            logger.warning(
                "%s Cannot start transfer: busy in state %s",
                LogTag.FILE_SVC,
                self._state.name,
            )
            return

        file_path = Path(file_path_str)
        if not file_path.exists() or not file_path.is_file():
            err_msg = f"File not found: {file_path_str}"
            logger.error("%s %s", LogTag.FILE_SVC, err_msg)
            self.transfer_failed.emit(err_msg)
            return

        try:
            self._file_data = file_path.read_bytes()
        except Exception as e:
            err_msg = f"Failed to read file: {e}"
            logger.error("%s %s", LogTag.FILE_SVC, err_msg)
            self.transfer_failed.emit(err_msg)
            return

        self._current_file_path = file_path
        self._file_size = len(self._file_data)
        self._file_crc32 = zlib.crc32(self._file_data) & 0xFFFFFFFF
        self._total_chunks = (
            (self._file_size + self._chunk_size - 1) // self._chunk_size
            if self._file_size > 0
            else 1
        )
        self._current_chunk_idx = 0
        self._retry_count = 0
        self._bytes_transferred = 0
        self._start_time = time.time()
        self._last_chunk_time = self._start_time

        self._set_state(TransferState.HANDSHAKING)

        # Send FileStartPayload
        start_payload = FileStartPayload(
            file_size=self._file_size,
            chunk_size=self._chunk_size,
            total_chunks=self._total_chunks,
            file_crc32=self._file_crc32,
            filename=file_path.name,
        )

        self._seq = (self._seq + 1) % 256
        self._serial_manager.send_packet(
            seq=self._seq,
            opcode=Opcode.TRANSFER_START,
            payload=start_payload.pack(),
        )
        self._ack_timer.start()
        logger.info(
            "%s Initiated file transfer: '%s' (%d bytes, %d chunks, CRC 0x%08X)",
            LogTag.FILE_SVC,
            file_path.name,
            self._file_size,
            self._total_chunks,
            self._file_crc32,
        )

    def abort_transfer(self) -> None:
        """Aborts current file transfer."""
        if self._state in (
            TransferState.HANDSHAKING,
            TransferState.TRANSFERRING,
            TransferState.FINALIZING,
        ):
            self._ack_timer.stop()
            self._seq = (self._seq + 1) % 256
            self._serial_manager.send_packet(
                seq=self._seq,
                opcode=Opcode.TRANSFER_PAUSE,
                payload=b"ABORT",
            )
            self._set_state(TransferState.ABORTED)
            self.transfer_failed.emit("Transfer cancelled by user")
            logger.info("%s Transfer aborted by user", LogTag.FILE_SVC)

    def _send_current_chunk(self, new_seq: bool = True) -> None:
        """Transmits current binary chunk.

        Args:
            new_seq: True to increment sequence number for a new chunk;
                False to reuse current sequence number for retransmission.
        """
        start_offset = self._current_chunk_idx * self._chunk_size
        end_offset = min(start_offset + self._chunk_size, self._file_size)
        chunk_data = self._file_data[start_offset:end_offset]

        chunk_payload = FileChunkPayload(
            chunk_id=self._current_chunk_idx,
            chunk_data=chunk_data,
        )

        if new_seq:
            self._seq = (self._seq + 1) % 256

        self._serial_manager.send_packet(
            seq=self._seq,
            opcode=Opcode.DATA_CHUNK,
            payload=chunk_payload.pack(),
        )
        self._ack_timer.start()
        logger.debug(
            "%s Sent Chunk %d/%d (Seq #%d, %d bytes)",
            LogTag.ARQ_ENGINE,
            self._current_chunk_idx + 1,
            self._total_chunks,
            self._seq,
            len(chunk_data),
        )

    def _send_file_end(self, new_seq: bool = True) -> None:
        """Transmits completion verification frame.

        Args:
            new_seq: True to increment sequence number; False for retransmission.
        """
        self._set_state(TransferState.FINALIZING)
        end_payload = FileEndPayload(
            file_crc32=self._file_crc32,
            total_bytes_sent=self._bytes_transferred,
        )
        if new_seq:
            self._seq = (self._seq + 1) % 256

        self._serial_manager.send_packet(
            seq=self._seq,
            opcode=Opcode.TRANSFER_END,
            payload=end_payload.pack(),
        )
        self._ack_timer.start()
        logger.info(
            "%s Sent TRANSFER_END (Seq #%d, CRC 0x%08X)",
            LogTag.FILE_SVC,
            self._seq,
            self._file_crc32,
        )

    def _handle_packet(self, pkt: object) -> None:
        """Processes incoming ACK/NACK frames."""
        if not isinstance(pkt, Packet):
            return

        if pkt.opcode == Opcode.ACK:
            self._on_ack_received(pkt)
        elif pkt.opcode == Opcode.NACK:
            self._on_nack_received(pkt)

    def _on_ack_received(self, pkt: Packet) -> None:
        """Handles successful ACK with sequence and status verification."""
        if self._state not in (
            TransferState.HANDSHAKING,
            TransferState.TRANSFERRING,
            TransferState.FINALIZING,
        ):
            return

        try:
            ack_info = AckPayload.unpack(pkt.payload)
            acked_seq = ack_info.acked_seq
            status = ack_info.status
        except Exception:
            acked_seq = pkt.seq
            status = AckStatus.OK

        if acked_seq != self._seq:
            logger.debug(
                "%s Stale ACK received: expected seq %d, got %d",
                LogTag.ARQ_ENGINE,
                self._seq,
                acked_seq,
            )
            return

        if status != AckStatus.OK:
            logger.warning(
                "%s Remote receiver reported error status %d for seq %d",
                LogTag.ARQ_ENGINE,
                status,
                acked_seq,
            )
            self._ack_timer.stop()
            self._retry_count += 1
            if self._retry_count > MAX_RETRY_COUNT:
                self._set_state(TransferState.FAILED)
                err_msg = (
                    f"Transfer failed: Remote error status {status} after {MAX_RETRY_COUNT} retries"
                )
                self.transfer_failed.emit(err_msg)
                logger.error("%s %s", LogTag.ARQ_ENGINE, err_msg)
                return
            self._retransmit()
            return

        self._ack_timer.stop()
        self._retry_count = 0

        if self._state == TransferState.HANDSHAKING:
            self._set_state(TransferState.TRANSFERRING)
            self._send_current_chunk(new_seq=True)

        elif self._state == TransferState.TRANSFERRING:
            start_offset = self._current_chunk_idx * self._chunk_size
            end_offset = min(start_offset + self._chunk_size, self._file_size)
            chunk_len = end_offset - start_offset
            self._bytes_transferred += chunk_len

            elapsed = max(time.time() - self._start_time, 0.001)
            speed_kbps = (self._bytes_transferred / 1024.0) / elapsed
            remaining_bytes = self._file_size - self._bytes_transferred
            eta_sec = (remaining_bytes / (speed_kbps * 1024.0)) if speed_kbps > 0 else 0.0
            pct = int((self._bytes_transferred / max(self._file_size, 1)) * 100)

            self.progress_updated.emit(
                pct, speed_kbps, eta_sec, self._current_chunk_idx + 1, self._total_chunks
            )

            self._current_chunk_idx += 1
            if self._current_chunk_idx < self._total_chunks:
                self._send_current_chunk(new_seq=True)
            else:
                self._send_file_end(new_seq=True)

        elif self._state == TransferState.FINALIZING:
            elapsed = max(time.time() - self._start_time, 0.001)
            speed_kbps = (self._bytes_transferred / 1024.0) / elapsed
            crc_hex = f"0x{self._file_crc32:08X}"
            fname = self._current_file_path.name if self._current_file_path else "file"
            fpath = str(self._current_file_path) if self._current_file_path else ""
            self._set_state(TransferState.COMPLETED)

            # Persist transfer record into hard drive SQLite database
            self._storage.save_file_transfer(
                filename=fname,
                file_path=fpath,
                direction="TX",
                size_bytes=self._bytes_transferred,
                crc32_hex=crc_hex,
                speed_kbps=round(speed_kbps, 2),
                duration_sec=round(elapsed, 2),
                status="SUCCESS",
            )

            self.transfer_completed.emit(fname, self._bytes_transferred, elapsed, crc_hex)
            logger.info(
                "%s File transfer complete: '%s' in %.2fs (Speed: %.2f KB/s, CRC: %s)",
                LogTag.FILE_SVC,
                fname,
                elapsed,
                speed_kbps,
                crc_hex,
            )

    def _on_nack_received(self, pkt: Packet) -> None:
        """Handles NACK request by retransmitting up to retry limit."""
        if self._state not in (
            TransferState.HANDSHAKING,
            TransferState.TRANSFERRING,
            TransferState.FINALIZING,
        ):
            return

        logger.warning("%s NACK received from remote for seq %d", LogTag.ARQ_ENGINE, pkt.seq)
        self._ack_timer.stop()
        self._retry_count += 1
        if self._retry_count > MAX_RETRY_COUNT:
            self._set_state(TransferState.FAILED)
            err_msg = f"Transfer failed: Max retries ({MAX_RETRY_COUNT}) exceeded on NACK"
            self.transfer_failed.emit(err_msg)
            logger.error("%s %s", LogTag.ARQ_ENGINE, err_msg)
            return

        self._retransmit()

    def _on_ack_timeout(self) -> None:
        """Handles Stop-and-Wait ACK timeout."""
        if self._state not in (
            TransferState.HANDSHAKING,
            TransferState.TRANSFERRING,
            TransferState.FINALIZING,
        ):
            return

        self._retry_count += 1
        if self._retry_count > MAX_RETRY_COUNT:
            self._set_state(TransferState.FAILED)
            err_msg = f"Transfer failed: ACK timeout after {MAX_RETRY_COUNT} retries"
            self.transfer_failed.emit(err_msg)
            logger.error("%s %s", LogTag.ARQ_ENGINE, err_msg)
            return

        logger.warning(
            "%s ACK timeout (retry %d/%d) for state %s",
            LogTag.ARQ_ENGINE,
            self._retry_count,
            MAX_RETRY_COUNT,
            self._state.name,
        )
        self._retransmit()

    def _retransmit(self) -> None:
        """Retransmits current unacknowledged packet using existing sequence number."""
        if self._state == TransferState.HANDSHAKING:
            if self._current_file_path:
                start_payload = FileStartPayload(
                    file_size=self._file_size,
                    chunk_size=self._chunk_size,
                    total_chunks=self._total_chunks,
                    file_crc32=self._file_crc32,
                    filename=self._current_file_path.name,
                )
                self._serial_manager.send_packet(
                    seq=self._seq,
                    opcode=Opcode.TRANSFER_START,
                    payload=start_payload.pack(),
                )
                self._ack_timer.start()

        elif self._state == TransferState.TRANSFERRING:
            self._send_current_chunk(new_seq=False)

        elif self._state == TransferState.FINALIZING:
            self._send_file_end(new_seq=False)
