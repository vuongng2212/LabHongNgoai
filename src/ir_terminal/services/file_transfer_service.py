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
    DEFAULT_CHUNK_SIZE,
    MAX_CHUNK_COUNT,
    MAX_CHUNK_DATA_SIZE,
    MAX_FILE_SIZE_BYTES,
    MAX_PAYLOAD_SIZE,
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
from ir_terminal.utils.logger import (
    LogTag,
    ack_status_name,
    format_frame_summary,
    format_hex_preview,
    opcode_name,
)
from ir_terminal.utils.path_helpers import get_transfers_dir

logger = logging.getLogger(__name__)

# Persistent settings keys for dynamic transfer configuration (via StorageService).
SETTING_MAX_FILE_SIZE_BYTES = "max_file_size_bytes"
SETTING_TRANSFERS_DIR = "transfers_dir"

# User-configurable bounds for max file size (1MB..100MB, default 10MB).
MIN_FILE_SIZE_BYTES: int = 1 * 1024 * 1024
MAX_CONFIGURABLE_FILE_SIZE_BYTES: int = 100 * 1024 * 1024


class TransferState(Enum):
    """File transfer state machine states."""

    IDLE = auto()
    HANDSHAKING = auto()
    TRANSFERRING = auto()
    FINALIZING = auto()
    RECEIVING = auto()
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
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        storage_service: StorageService | None = None,
        transfers_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self._serial_manager = serial_manager
        self._chunk_size = chunk_size
        self._storage = storage_service or StorageService()
        self._transfers_dir = transfers_dir or get_transfers_dir()
        self._max_file_size_bytes = MAX_FILE_SIZE_BYTES

        self._state = TransferState.IDLE

        # TX Session State
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

        # RX Session State
        self._rx_filename: str = ""
        self._rx_file_size: int = 0
        self._rx_chunk_size: int = 0
        self._rx_total_chunks: int = 0
        self._rx_file_crc32: int = 0
        self._rx_received_chunks: dict[int, bytes] = {}
        self._rx_start_time: float = 0.0
        self._rx_bytes_received: int = 0
        self._rx_received_count: int = 0
        # Last accepted RX session IDs: lets a retransmitted START/END (sent
        # because the peer lost our ACK) be re-acknowledged instead of being
        # rejected as a new/busy session.
        self._rx_start_seq: int = -1
        self._rx_end_seq: int = -1
        self._rx_end_status: int = AckStatus.OK

        # TX retransmit caches (refactor: identical bytes reused, no repack).
        # Invalidated on every new start_transfer/abort.
        self._cached_start_payload: bytes | None = None
        self._cached_chunk_key: int = -1
        self._cached_chunk_payload: bytes | None = None
        self._cached_end_key: int = -1
        self._cached_end_payload: bytes | None = None

        # ARQ Timeout Timer
        self._ack_timer = QTimer(self)
        self._ack_timer.setSingleShot(True)
        self._ack_timer.setInterval(int(DEFAULT_ACK_TIMEOUT_SEC * 1000))
        self._ack_timer.timeout.connect(self._on_ack_timeout)

        # Connect packet reception
        self._serial_manager.packet_received.connect(self._handle_packet)

        # Persisted user settings override injected defaults when present;
        # explicit transfers_dir acts as the fallback default (tests/dual-mock
        # use fresh storage, so injected paths are preserved there).
        self.load_settings()

    @property
    def max_file_size_bytes(self) -> int:
        """Returns the configured maximum accepted file size in bytes."""
        return self._max_file_size_bytes

    @property
    def transfers_dir_path(self) -> Path:
        """Returns the directory where received optical files are stored."""
        return self._transfers_dir

    def get_config(self) -> dict[str, str | int | float]:
        """Returns the current transfer configuration as JSON-serializable dict.

        Returns:
            Dict with max_file_size_bytes, max_file_size_mb, transfers_dir.
        """
        return {
            "max_file_size_bytes": self._max_file_size_bytes,
            "max_file_size_mb": round(self._max_file_size_bytes / (1024 * 1024), 2),
            "transfers_dir": str(self._transfers_dir),
        }

    def load_settings(self) -> dict[str, str | int | float]:
        """Loads persisted transfer settings from StorageService into memory.

        Falls back to defaults when keys are missing or invalid. An explicit
        transfers_dir that does not exist is created; invalid values are
        ignored without raising.

        Returns:
            Current configuration dict (same shape as get_config).
        """
        try:
            raw_limit = self._storage.get_setting(
                SETTING_MAX_FILE_SIZE_BYTES, str(MAX_FILE_SIZE_BYTES)
            )
            parsed = int(float(raw_limit))
            if MIN_FILE_SIZE_BYTES <= parsed <= MAX_CONFIGURABLE_FILE_SIZE_BYTES:
                self._max_file_size_bytes = parsed
        except (TypeError, ValueError):
            pass
        try:
            raw_dir = self._storage.get_setting(SETTING_TRANSFERS_DIR, "")
            if raw_dir.strip():
                candidate = Path(raw_dir).expanduser()
                candidate.mkdir(parents=True, exist_ok=True)
                if candidate.is_dir():
                    self._transfers_dir = candidate
        except (OSError, ValueError):
            pass
        logger.info(
            "%s Transfer config loaded: max=%dB dir=%s",
            LogTag.FILE_SVC,
            self._max_file_size_bytes,
            self._transfers_dir,
        )
        return self.get_config()

    def set_max_file_size_bytes(self, size_bytes: int) -> int:
        """Updates and persists the maximum accepted file size.

        Args:
            size_bytes: New limit in bytes (clamped to 1MB..100MB).

        Returns:
            Effective limit in bytes after clamping.

        Raises:
            ValueError: If size_bytes is not a positive integer.
        """
        size = int(size_bytes)
        if size <= 0:
            raise ValueError(f"Invalid max file size: {size_bytes}")
        clamped = max(MIN_FILE_SIZE_BYTES, min(size, MAX_CONFIGURABLE_FILE_SIZE_BYTES))
        self._max_file_size_bytes = clamped
        self._storage.save_setting(SETTING_MAX_FILE_SIZE_BYTES, str(clamped))
        logger.info("%s Transfer max file size set: %dB", LogTag.FILE_SVC, clamped)
        return clamped

    def set_transfers_dir(self, dir_path: str | Path) -> Path:
        """Updates and persists the default receive directory.

        Args:
            dir_path: Target directory path (created when missing).

        Returns:
            Resolved directory path.

        Raises:
            ValueError: If the path is empty or cannot be created.
        """
        candidate = Path(dir_path).expanduser() if isinstance(dir_path, str) else dir_path
        if not str(candidate).strip():
            raise ValueError("Transfers directory path is empty")
        candidate.mkdir(parents=True, exist_ok=True)
        if not candidate.is_dir():
            raise ValueError(f"Not a directory: {candidate}")
        self._transfers_dir = candidate
        self._storage.save_setting(SETTING_TRANSFERS_DIR, str(candidate))
        logger.info("%s Transfer receive dir set: %s", LogTag.FILE_SVC, candidate)
        return candidate

    def update_config(
        self,
        max_file_size_bytes: int | None = None,
        transfers_dir: str | Path | None = None,
    ) -> dict[str, str | int | float]:
        """Applies a partial configuration update and persists valid fields.

        Args:
            max_file_size_bytes: Optional new size limit in bytes.
            transfers_dir: Optional new receive directory.

        Returns:
            Current configuration dict after update.
        """
        if max_file_size_bytes is not None:
            self.set_max_file_size_bytes(int(max_file_size_bytes))
        if transfers_dir is not None and str(transfers_dir).strip():
            self.set_transfers_dir(transfers_dir)
        return self.get_config()

    @property
    def state(self) -> TransferState:
        return self._state

    def _set_state(self, new_state: TransferState) -> None:
        old_state = self._state
        self._state = new_state
        self.state_changed.emit(new_state.name)
        elapsed = max(time.time() - self._start_time, 0.0) if self._start_time else 0.0
        logger.info(
            "%s ARQ state %s -> %s (elapsed=%.2fs chunk=%d/%d retry=%d/5 seq=%d)",
            LogTag.ARQ_ENGINE,
            old_state.name,
            new_state.name,
            elapsed,
            self._current_chunk_idx,
            self._total_chunks,
            self._retry_count,
            self._seq,
        )

    def start_transfer(self, file_path_str: str) -> None:
        """Starts transmitting a file over optical link."""
        if self._state not in (
            TransferState.IDLE,
            TransferState.COMPLETED,
            TransferState.FAILED,
            TransferState.ABORTED,
        ):
            logger.warning(
                "%s TX start rejected: busy in %s (file=%s chunk=%d/%d)",
                LogTag.FILE_SVC,
                self._state.name,
                file_path_str,
                self._current_chunk_idx,
                self._total_chunks,
            )
            return

        file_path = Path(file_path_str)
        if not file_path.exists() or not file_path.is_file():
            err_msg = f"File not found: {file_path_str}"
            logger.error(
                "%s TX start FAILED: %s (state=%s)",
                LogTag.FILE_SVC,
                err_msg,
                self._state.name,
            )
            self.transfer_failed.emit(err_msg)
            return

        oversize = file_path.stat().st_size
        if oversize > self._max_file_size_bytes:
            err_msg = (
                f"File exceeds {self._max_file_size_bytes // (1024 * 1024)}MB limit: "
                f"{file_path.name} ({oversize} bytes)"
            )
            logger.error(
                "%s TX start FAILED: %s (limit=%dB)",
                LogTag.FILE_SVC,
                err_msg,
                self._max_file_size_bytes,
            )
            self.transfer_failed.emit(err_msg)
            return

        if not (1 <= self._chunk_size <= MAX_CHUNK_DATA_SIZE):
            err_msg = (
                f"Invalid chunk size {self._chunk_size}B; protocol allows "
                f"1..{MAX_CHUNK_DATA_SIZE}B (payload max {MAX_PAYLOAD_SIZE}B incl. chunk_id)"
            )
            logger.error("%s TX start FAILED: %s", LogTag.FILE_SVC, err_msg)
            self.transfer_failed.emit(err_msg)
            return

        planned_chunks = (
            (oversize + self._chunk_size - 1) // self._chunk_size if oversize > 0 else 1
        )
        if planned_chunks > MAX_CHUNK_COUNT:
            err_msg = (
                f"File requires {planned_chunks} chunks; protocol max is {MAX_CHUNK_COUNT} "
                f"(file={oversize}B, chunk_size={self._chunk_size}B). Use a larger chunk size "
                "or split the file."
            )
            logger.error("%s TX start FAILED: %s", LogTag.FILE_SVC, err_msg)
            self.transfer_failed.emit(err_msg)
            return

        try:
            self._file_data = file_path.read_bytes()
        except Exception as e:
            err_msg = f"Failed to read file: {e}"
            logger.exception(
                "%s TX start FAILED: cannot read %s (%s: %s)",
                LogTag.FILE_SVC,
                file_path,
                type(e).__name__,
                e,
            )
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
        # Invalidate retransmit caches for the new session
        self._cached_start_payload = None
        self._cached_chunk_key = -1
        self._cached_chunk_payload = None
        self._cached_end_key = -1
        self._cached_end_payload = None

        self._set_state(TransferState.HANDSHAKING)

        # Send FileStartPayload
        start_payload = FileStartPayload(
            file_size=self._file_size,
            chunk_size=self._chunk_size,
            total_chunks=self._total_chunks,
            file_crc32=self._file_crc32,
            filename=file_path.name,
        )
        self._cached_start_payload = start_payload.pack()

        self._seq = (self._seq + 1) % 256
        self._serial_manager.send_packet(
            seq=self._seq,
            opcode=Opcode.TRANSFER_START,
            payload=self._cached_start_payload,
        )
        self._ack_timer.start()
        logger.info(
            "%s TX START %s file='%s' size=%dB chunks=%d chunk_size=%dB "
            "file_crc=0x%08X payload_hex=%s timeout=250ms retry=0/5",
            LogTag.FILE_SVC,
            format_frame_summary(self._seq, Opcode.TRANSFER_START, len(self._cached_start_payload)),
            file_path.name,
            self._file_size,
            self._total_chunks,
            self._chunk_size,
            self._file_crc32,
            format_hex_preview(self._cached_start_payload, max_bytes=24),
        )

    def abort_transfer(self) -> None:
        """Aborts current optical file transmission or reception."""
        if self._state == TransferState.RECEIVING:
            self._set_state(TransferState.ABORTED)
            self._rx_received_chunks.clear()
            self._rx_received_count = 0
            self._rx_bytes_received = 0
            self._rx_start_seq = -1
            self._rx_end_seq = -1
            self._rx_end_status = AckStatus.OK
            self.transfer_failed.emit("Incoming transfer aborted by user")
            logger.info(
                "%s RX aborted by user: file='%s' had=%d/%d chunks (%dB)",
                LogTag.FILE_SVC,
                self._rx_filename,
                self._rx_received_count,
                self._rx_total_chunks,
                self._rx_bytes_received,
            )
            return

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
            fname = self._current_file_path.name if self._current_file_path else "?"
            self._set_state(TransferState.ABORTED)
            # Invalidate caches so a later session cannot reuse stale bytes
            self._cached_start_payload = None
            self._cached_chunk_key = -1
            self._cached_chunk_payload = None
            self._cached_end_key = -1
            self._cached_end_payload = None
            self.transfer_failed.emit("Transfer cancelled by user")
            logger.info(
                "%s TX aborted by user %s file='%s' sent=%dB chunk=%d/%d",
                LogTag.FILE_SVC,
                format_frame_summary(self._seq, Opcode.TRANSFER_PAUSE, len(b"ABORT")),
                fname,
                self._bytes_transferred,
                self._current_chunk_idx,
                self._total_chunks,
            )

    def _send_current_chunk(self, new_seq: bool = True) -> None:
        """Transmits current binary chunk.

        Args:
            new_seq: True to increment sequence number for a new chunk;
                False to reuse current sequence number for retransmission.
        """
        # Retransmission of the same chunk reuses the identical packed payload
        # (only the outer frame seq is reused); new chunks slice+pack once.
        if not new_seq and self._cached_chunk_payload is not None and self._cached_chunk_key == self._current_chunk_idx:
            packed = self._cached_chunk_payload
            start_offset = self._current_chunk_idx * self._chunk_size
            end_offset = min(start_offset + self._chunk_size, self._file_size)
            chunk_len = end_offset - start_offset
        else:
            start_offset = self._current_chunk_idx * self._chunk_size
            end_offset = min(start_offset + self._chunk_size, self._file_size)
            chunk_data = self._file_data[start_offset:end_offset]

            chunk_payload = FileChunkPayload(
                chunk_id=self._current_chunk_idx,
                chunk_data=chunk_data,
            )
            packed = chunk_payload.pack()
            self._cached_chunk_key = self._current_chunk_idx
            self._cached_chunk_payload = packed
            chunk_len = len(chunk_data)

        if new_seq:
            self._seq = (self._seq + 1) % 256

        self._serial_manager.send_packet(
            seq=self._seq,
            opcode=Opcode.DATA_CHUNK,
            payload=packed,
        )
        self._ack_timer.start()
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s TX CHUNK %s chunk_id=%d (%d/%d) chunk_bytes=%dB payload_hex=%s %s",
                LogTag.ARQ_ENGINE,
                format_frame_summary(self._seq, Opcode.DATA_CHUNK, len(packed)),
                self._current_chunk_idx,
                self._current_chunk_idx + 1,
                self._total_chunks,
                chunk_len,
                format_hex_preview(packed, max_bytes=16),
                "RETRY" if not new_seq else f"retry={self._retry_count}/5",
            )

    def _send_file_end(self, new_seq: bool = True) -> None:
        """Transmits completion verification frame.

        Args:
            new_seq: True to increment sequence number; False for retransmission.
        """
        self._set_state(TransferState.FINALIZING)
        # TRANSFER_END payload depends only on (crc, bytes_transferred), both
        # frozen once FINALIZING is entered, so retransmits reuse packed bytes.
        packed_end: bytes
        if (
            not new_seq
            and self._cached_end_payload is not None
            and self._cached_end_key == self._bytes_transferred
        ):
            packed_end = self._cached_end_payload
        else:
            end_payload = FileEndPayload(
                file_crc32=self._file_crc32,
                total_bytes_sent=self._bytes_transferred,
            )
            packed_end = end_payload.pack()
            self._cached_end_key = self._bytes_transferred
            self._cached_end_payload = packed_end
        if new_seq:
            self._seq = (self._seq + 1) % 256

        self._serial_manager.send_packet(
            seq=self._seq,
            opcode=Opcode.TRANSFER_END,
            payload=packed_end,
        )
        self._ack_timer.start()
        logger.info(
            "%s TX END %s file_crc=0x%08X bytes=%dB payload_hex=%s %s",
            LogTag.FILE_SVC,
            format_frame_summary(self._seq, Opcode.TRANSFER_END, len(packed_end)),
            self._file_crc32,
            self._bytes_transferred,
            format_hex_preview(packed_end),
            "RETRY" if not new_seq else f"retry={self._retry_count}/5",
        )

    def _persist_failure(self, reason: str) -> None:
        """Persists a FAILED record so lab runs keep an audit trail."""
        try:
            fname = self._current_file_path.name if self._current_file_path else self._rx_filename
            fpath = str(self._current_file_path) if self._current_file_path else ""
            size = self._file_size if self._file_size else self._rx_file_size
            rec_id = self._storage.save_file_transfer(
                filename=fname or "unknown",
                file_path=fpath,
                direction="TX" if self._file_size else "RX",
                size_bytes=size,
                crc32_hex=f"0x{self._file_crc32:08X}" if self._file_size else "",
                speed_kbps=0.0,
                duration_sec=round(max(time.time() - (self._start_time or time.time()), 0.0), 2),
                status="FAILED",
            )
            logger.info(
                "%s Persisted FAILED record #%d: file='%s' state=%s reason=%s",
                LogTag.FILE_SVC,
                rec_id,
                fname or "unknown",
                self._state.name,
                reason,
            )
        except Exception as exc:
            logger.warning(
                "%s Could not persist FAILED record (%s: %s) reason=%s",
                LogTag.FILE_SVC,
                type(exc).__name__,
                exc,
                reason,
            )

    def _handle_packet(self, pkt: object) -> None:
        """Processes incoming ACK/NACK frames or incoming file transfer packets (RX)."""
        if not isinstance(pkt, Packet):
            logger.debug(
                "%s Ignored non-Packet: type=%s state=%s",
                LogTag.ARQ_ENGINE,
                type(pkt).__name__,
                self._state.name,
            )
            return

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s RX dispatch %s (%s) state=%s crc=0x%08X",
                LogTag.ARQ_ENGINE,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                opcode_name(pkt.opcode),
                self._state.name,
                pkt.crc32 & 0xFFFFFFFF,
            )
        if pkt.opcode == Opcode.ACK:
            self._on_ack_received(pkt)
        elif pkt.opcode == Opcode.NACK:
            self._on_nack_received(pkt)
        elif pkt.opcode == Opcode.TRANSFER_START:
            self._on_transfer_start_received(pkt)
        elif pkt.opcode == Opcode.DATA_CHUNK:
            self._on_data_chunk_received(pkt)
        elif pkt.opcode == Opcode.TRANSFER_END:
            self._on_transfer_end_received(pkt)
        elif pkt.opcode == Opcode.TRANSFER_PAUSE:
            self._on_transfer_pause_received(pkt)
        else:
            logger.debug(
                "%s Ignored opcode %s in state %s",
                LogTag.ARQ_ENGINE,
                opcode_name(pkt.opcode),
                self._state.name,
            )

    def _on_transfer_pause_received(self, pkt: Packet) -> None:
        """Handles remote ABORT/PAUSE: stops RX session and ACKs the peer."""
        if self._state != TransferState.RECEIVING:
            logger.debug(
                "%s Ignored TRANSFER_PAUSE in %s: %s",
                LogTag.FILE_SVC,
                self._state.name,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            return
        reason = pkt.payload.decode("utf-8", errors="replace") if pkt.payload else "PAUSED"
        rx_file = self._rx_filename
        rx_had = (self._rx_received_count, self._rx_total_chunks, self._rx_bytes_received)
        self._rx_received_chunks.clear()
        self._rx_received_count = 0
        self._rx_bytes_received = 0
        ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OK)
        self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.ACK, payload=ack.pack())
        self._set_state(TransferState.ABORTED)
        self._persist_failure(f"Remote aborted transfer: {reason}")
        self.transfer_failed.emit(f"Remote aborted transfer: {reason}")
        logger.info(
            "%s RX remote-abort %s reason=%r file='%s' had=%d/%d chunks (%dB)",
            LogTag.FILE_SVC,
            format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            reason,
            rx_file,
            rx_had[0],
            rx_had[1],
            rx_had[2],
        )

    def _is_duplicate_start(self, pkt: Packet, start_payload: FileStartPayload) -> bool:
        """Returns True when a START retransmits the currently accepted RX session."""
        return (
            self._state == TransferState.RECEIVING
            and pkt.seq == self._rx_start_seq
            and start_payload.file_size == self._rx_file_size
            and start_payload.chunk_size == self._rx_chunk_size
            and start_payload.total_chunks == self._rx_total_chunks
            and start_payload.file_crc32 == self._rx_file_crc32
            and start_payload.filename == self._rx_filename
        )

    def _on_transfer_start_received(self, pkt: Packet) -> None:
        """Handles incoming TRANSFER_START frame by preparing receiver buffer and acknowledging."""
        try:
            start_payload = FileStartPayload.unpack(pkt.payload)
        except Exception as e:
            logger.exception(
                "%s RX START parse FAILED %s (%s: %s) hex=%s",
                LogTag.FILE_SVC,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload), pkt.crc32),
                type(e).__name__,
                e,
                format_hex_preview(pkt.payload, max_bytes=24),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.CRC_ERROR)
            self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            return

        # A retransmitted START means our previous ACK was lost. Re-ACK the
        # already-active session instead of NACKing it as "busy"; otherwise the
        # sender burns all 5 retries and fails even though the link is healthy.
        if self._is_duplicate_start(pkt, start_payload):
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OK)
            self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.ACK, payload=ack.pack())
            logger.info(
                "%s RX START duplicate re-ACK %s file='%s' size=%dB",
                LogTag.ARQ_ENGINE,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                self._rx_filename,
                self._rx_file_size,
            )
            return

        # Half-duplex guard: never clobber an ongoing outgoing session. NACK tells
        # the sender to back off and retry after its timeout.
        if self._state in (
            TransferState.HANDSHAKING,
            TransferState.TRANSFERRING,
            TransferState.FINALIZING,
            TransferState.RECEIVING,
        ):
            logger.warning(
                "%s RX START rejected: busy in %s | %s file='%s' size=%dB",
                LogTag.ARQ_ENGINE,
                self._state.name,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                start_payload.filename,
                start_payload.file_size,
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OUT_OF_ORDER)
            self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            return

        if start_payload.file_size > self._max_file_size_bytes:
            err_msg = (
                f"Incoming file exceeds {self._max_file_size_bytes // (1024 * 1024)}MB limit: "
                f"{start_payload.filename} ({start_payload.file_size} bytes)"
            )
            logger.error(
                "%s RX START rejected: %s | %s",
                LogTag.FILE_SVC,
                err_msg,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OUT_OF_ORDER)
            self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            self._set_state(TransferState.FAILED)
            self._persist_failure(err_msg)
            self.transfer_failed.emit(err_msg)
            return

        if start_payload.chunk_size == 0 or start_payload.chunk_size > MAX_CHUNK_DATA_SIZE:
            err_msg = (
                f"Incoming transfer has invalid chunk size: {start_payload.chunk_size} "
                f"(max {MAX_CHUNK_DATA_SIZE}B data inside {MAX_PAYLOAD_SIZE}B payload)"
            )
            logger.error(
                "%s RX START rejected: %s | %s payload_hex=%s",
                LogTag.FILE_SVC,
                err_msg,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                format_hex_preview(pkt.payload, max_bytes=24),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.CRC_ERROR)
            self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            self._set_state(TransferState.FAILED)
            self._persist_failure(err_msg)
            self.transfer_failed.emit(err_msg)
            return

        if start_payload.total_chunks == 0 or start_payload.total_chunks > 65535:
            err_msg = f"Incoming transfer has invalid total_chunks: {start_payload.total_chunks}"
            logger.error(
                "%s RX START rejected: %s | %s",
                LogTag.FILE_SVC,
                err_msg,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.CRC_ERROR)
            self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            self._set_state(TransferState.FAILED)
            self._persist_failure(err_msg)
            self.transfer_failed.emit(err_msg)
            return

        self._rx_filename = start_payload.filename
        self._rx_file_size = start_payload.file_size
        self._rx_chunk_size = start_payload.chunk_size
        self._rx_total_chunks = start_payload.total_chunks
        self._rx_file_crc32 = start_payload.file_crc32
        self._rx_received_chunks = {}
        self._rx_start_time = time.time()
        self._rx_bytes_received = 0
        self._rx_received_count = 0
        self._rx_start_seq = pkt.seq
        self._rx_end_seq = -1
        self._rx_end_status = AckStatus.OK

        self._set_state(TransferState.RECEIVING)
        logger.info(
            "%s RX START accepted %s file='%s' size=%dB chunks=%d chunk_size=%dB "
            "file_crc=0x%08X payload_hex=%s",
            LogTag.FILE_SVC,
            format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload), pkt.crc32),
            self._rx_filename,
            self._rx_file_size,
            self._rx_total_chunks,
            self._rx_chunk_size,
            self._rx_file_crc32,
            format_hex_preview(pkt.payload, max_bytes=24),
        )

        ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OK)
        self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.ACK, payload=ack.pack())
        self.progress_updated.emit(0, 0.0, 0.0, 0, max(self._rx_total_chunks, 1))

    def _on_data_chunk_received(self, pkt: Packet) -> None:
        """Handles incoming DATA_CHUNK frame by recording chunk bytes and acknowledging."""
        if self._state != TransferState.RECEIVING:
            logger.warning(
                "%s RX CHUNK out-of-state: state=%s | %s",
                LogTag.ARQ_ENGINE,
                self._state.name,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            return

        try:
            chunk_payload = FileChunkPayload.unpack(pkt.payload)
        except Exception as e:
            logger.exception(
                "%s RX CHUNK parse FAILED %s (%s: %s) hex=%s",
                LogTag.ARQ_ENGINE,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                type(e).__name__,
                e,
                format_hex_preview(pkt.payload, max_bytes=16),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.CRC_ERROR)
            self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            return

        if chunk_payload.chunk_id >= self._rx_total_chunks:
            logger.error(
                "%s RX CHUNK id=%d out of range (total=%d) | %s data_len=%dB",
                LogTag.ARQ_ENGINE,
                chunk_payload.chunk_id,
                self._rx_total_chunks,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                len(chunk_payload.chunk_data),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OUT_OF_ORDER)
            self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            return

        previous = self._rx_received_chunks.get(chunk_payload.chunk_id)
        is_duplicate = previous is not None
        if previous is not None:
            self._rx_bytes_received -= len(previous)
        else:
            self._rx_received_count += 1
        self._rx_received_chunks[chunk_payload.chunk_id] = chunk_payload.chunk_data
        self._rx_bytes_received += len(chunk_payload.chunk_data)

        # Respond ACK
        ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OK)
        self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.ACK, payload=ack.pack())

        # Progress calculation (same formula; counter replaces len(dict))
        now = time.time()
        elapsed = max(now - self._rx_start_time, 0.001)
        speed_kbps = (self._rx_bytes_received / 1024.0) / elapsed
        received_count = self._rx_received_count
        total = max(self._rx_total_chunks, 1)
        pct = min(int((received_count / total) * 100), 100)
        rem_chunks = max(total - received_count, 0)
        eta_sec = rem_chunks * (elapsed / max(received_count, 1))

        self.progress_updated.emit(pct, speed_kbps, eta_sec, received_count, total)
        logger.debug(
            "%s RX CHUNK id=%d (%d/%d) %s data=%dB total_rx=%dB elapsed=%.2fs %s",
            LogTag.ARQ_ENGINE,
            chunk_payload.chunk_id,
            received_count,
            total,
            format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            len(chunk_payload.chunk_data),
            self._rx_bytes_received,
            elapsed,
            "DUPLICATE-overwrite" if is_duplicate else f"pct={pct}%",
        )

    def _on_transfer_end_received(self, pkt: Packet) -> None:
        """Handles incoming TRANSFER_END frame by verifying integrity, saving file, and acknowledging."""
        # If our final ACK was lost, the sender retransmits the identical END
        # with the same sequence number. Re-ACK the completed session (without
        # writing the file or emitting transfer_completed again), or re-NACK a
        # previously failed verify, so the sender does not exhaust its retries.
        if pkt.seq == self._rx_end_seq and self._state in (
            TransferState.COMPLETED,
            TransferState.FAILED,
        ):
            status = (
                AckStatus.OK
                if self._state == TransferState.COMPLETED
                else self._rx_end_status
            )
            opcode = Opcode.ACK if status == AckStatus.OK else Opcode.NACK
            ack = AckPayload(acked_seq=pkt.seq, status=status)
            self._serial_manager.send_packet(seq=pkt.seq, opcode=opcode, payload=ack.pack())
            logger.info(
                "%s RX END duplicate re-%s %s status=%s(%d)",
                LogTag.ARQ_ENGINE,
                "ACK" if opcode == Opcode.ACK else "NACK",
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                ack_status_name(status),
                status,
            )
            return

        if self._state != TransferState.RECEIVING:
            logger.debug(
                "%s Ignored TRANSFER_END in %s: %s",
                LogTag.FILE_SVC,
                self._state.name,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            return

        try:
            end_payload = FileEndPayload.unpack(pkt.payload)
        except Exception as e:
            logger.exception(
                "%s RX END parse FAILED %s (%s: %s) hex=%s",
                LogTag.FILE_SVC,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                type(e).__name__,
                e,
                format_hex_preview(pkt.payload),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.CRC_ERROR)
            self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            self._rx_end_seq = pkt.seq
            self._rx_end_status = AckStatus.CRC_ERROR
            self._set_state(TransferState.FAILED)
            self._persist_failure("Corrupted TRANSFER_END payload")
            self.transfer_failed.emit("Corrupted TRANSFER_END payload")
            return

        logger.info(
            "%s RX END verify %s claimed_crc=0x%08X claimed_bytes=%d file='%s' "
            "have=%d/%d chunks (%dB)",
            LogTag.FILE_SVC,
            format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload), pkt.crc32),
            end_payload.file_crc32,
            end_payload.total_bytes_sent,
            self._rx_filename,
            self._rx_received_count,
            self._rx_total_chunks,
            self._rx_bytes_received,
        )

        # Assemble chunks sequentially
        assembled_data = bytearray()
        for idx in range(self._rx_total_chunks):
            if idx not in self._rx_received_chunks:
                logger.error(
                    "%s RX END missing chunk %d/%d upon TRANSFER_END | %s",
                    LogTag.ARQ_ENGINE,
                    idx,
                    self._rx_total_chunks,
                    format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                )
                ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OUT_OF_ORDER)
                self._serial_manager.send_packet(
                    seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack()
                )
                self._rx_end_seq = pkt.seq
                self._rx_end_status = AckStatus.OUT_OF_ORDER
                self._set_state(TransferState.FAILED)
                self._persist_failure(f"Missing chunk {idx} upon completion")
                self.transfer_failed.emit(f"Missing chunk {idx} upon completion")
                return
            assembled_data.extend(self._rx_received_chunks[idx])

        # Verify both declared byte counts (START file_size and END total_bytes)
        # before trusting the checksum: CRC cannot catch a sender that sent a
        # short file and then declared/checksummed the short length consistently.
        if (
            len(assembled_data) != end_payload.total_bytes_sent
            or len(assembled_data) != self._rx_file_size
        ):
            logger.error(
                "%s RX END size mismatch: assembled=%dB end_total=%dB start_file_size=%dB | %s",
                LogTag.FILE_SVC,
                len(assembled_data),
                end_payload.total_bytes_sent,
                self._rx_file_size,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.CRC_ERROR)
            self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            self._rx_end_seq = pkt.seq
            self._rx_end_status = AckStatus.CRC_ERROR
            self._set_state(TransferState.FAILED)
            self._persist_failure(
                f"Size mismatch: assembled {len(assembled_data)}B, "
                f"END {end_payload.total_bytes_sent}B, START {self._rx_file_size}B"
            )
            self.transfer_failed.emit(
                f"Size mismatch: assembled {len(assembled_data)}B, "
                f"END {end_payload.total_bytes_sent}B"
            )
            return

        # CRC-32 verification
        calculated_crc = zlib.crc32(assembled_data) & 0xFFFFFFFF
        if calculated_crc != end_payload.file_crc32 or calculated_crc != self._rx_file_crc32:
            logger.error(
                "%s RX CRC MISMATCH: calc=0x%08X end_frame=0x%08X start_frame=0x%08X "
                "assembled=%dB expected=%dB | %s",
                LogTag.FILE_SVC,
                calculated_crc,
                end_payload.file_crc32,
                self._rx_file_crc32,
                len(assembled_data),
                self._rx_file_size,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.CRC_ERROR)
            self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            self._rx_end_seq = pkt.seq
            self._rx_end_status = AckStatus.CRC_ERROR
            self._set_state(TransferState.FAILED)
            self._persist_failure(
                f"Checksum mismatch: 0x{calculated_crc:08X} != 0x{end_payload.file_crc32:08X}"
            )
            self.transfer_failed.emit(
                f"Checksum mismatch: 0x{calculated_crc:08X} != 0x{end_payload.file_crc32:08X}"
            )
            return

        # Save to disk
        self._transfers_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(self._rx_filename).name or "received_file.bin"
        dest_path = self._transfers_dir / safe_name
        if dest_path.exists():
            dest_path = (
                self._transfers_dir / f"{dest_path.stem}_{int(time.time())}{dest_path.suffix}"
            )

        dest_path.write_bytes(assembled_data)
        logger.debug(
            "%s RX saved bytes to disk: path=%s size=%dB",
            LogTag.FILE_SVC,
            dest_path,
            len(assembled_data),
        )

        # Send ACK
        ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OK)
        self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.ACK, payload=ack.pack())

        elapsed = max(time.time() - self._rx_start_time, 0.001)
        speed_kbps = (len(assembled_data) / 1024.0) / elapsed
        crc_hex = f"0x{calculated_crc:08X}"

        # Persist to database
        rec_id = self._storage.save_file_transfer(
            filename=dest_path.name,
            file_path=str(dest_path),
            direction="RX",
            size_bytes=len(assembled_data),
            crc32_hex=crc_hex,
            speed_kbps=speed_kbps,
            duration_sec=elapsed,
            status="SUCCESS",
        )

        self._rx_end_seq = pkt.seq
        self._rx_end_status = AckStatus.OK
        self._set_state(TransferState.COMPLETED)
        self.progress_updated.emit(
            100, speed_kbps, 0.0, self._rx_total_chunks, self._rx_total_chunks
        )
        self.transfer_completed.emit(dest_path.name, len(assembled_data), elapsed, crc_hex)
        logger.info(
            "%s RX COMPLETE %s file='%s' path=%s size=%dB elapsed=%.2fs "
            "speed=%.2fKB/s crc=%s stored_id=%d",
            LogTag.FILE_SVC,
            format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload), pkt.crc32),
            dest_path.name,
            dest_path,
            len(assembled_data),
            elapsed,
            speed_kbps,
            crc_hex,
            rec_id,
        )

    def _on_ack_received(self, pkt: Packet) -> None:
        """Handles successful ACK with sequence and status verification."""
        if self._state not in (
            TransferState.HANDSHAKING,
            TransferState.TRANSFERRING,
            TransferState.FINALIZING,
        ):
            logger.debug(
                "%s Ignored ACK in %s: %s",
                LogTag.ARQ_ENGINE,
                self._state.name,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload), pkt.crc32),
            )
            return

        try:
            ack_info = AckPayload.unpack(pkt.payload)
            acked_seq = ack_info.acked_seq
            status = ack_info.status
        except Exception as exc:
            logger.warning(
                "%s ACK payload undecodable %s (%s: %s) hex=%s — assuming seq/OK",
                LogTag.ARQ_ENGINE,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                type(exc).__name__,
                exc,
                format_hex_preview(pkt.payload),
            )
            acked_seq = pkt.seq
            status = AckStatus.OK

        if acked_seq != self._seq:
            logger.debug(
                "%s Stale ACK ignored: expected seq=%d got acked_seq=%d status=%s(%d) "
                "state=%s frame=%s",
                LogTag.ARQ_ENGINE,
                self._seq,
                acked_seq,
                ack_status_name(status),
                status,
                self._state.name,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            return

        if status != AckStatus.OK:
            logger.warning(
                "%s ACK error status: acked_seq=%d status=%s(%d) state=%s retry=%d/5 "
                "frame=%s payload_hex=%s",
                LogTag.ARQ_ENGINE,
                acked_seq,
                ack_status_name(status),
                status,
                self._state.name,
                self._retry_count + 1,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                format_hex_preview(pkt.payload),
            )
            self._ack_timer.stop()
            self._retry_count += 1
            if self._retry_count > MAX_RETRY_COUNT:
                self._set_state(TransferState.FAILED)
                err_msg = (
                    f"Transfer failed: Remote error status {status} after {MAX_RETRY_COUNT} retries"
                )
                self._persist_failure(err_msg)
                self.transfer_failed.emit(err_msg)
                logger.error(
                    "%s TX FAILED %s: %s",
                    LogTag.ARQ_ENGINE,
                    format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                    err_msg,
                )
                return
            self._retransmit()
            return

        self._ack_timer.stop()
        self._retry_count = 0
        logger.debug(
            "%s ACK ok: acked_seq=%d state=%s chunk=%d/%d frame=%s",
            LogTag.ARQ_ENGINE,
            acked_seq,
            self._state.name,
            self._current_chunk_idx,
            self._total_chunks,
            format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
        )

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

            # Persist transfer record into hard drive JSON store
            rec_id = self._storage.save_file_transfer(
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
                "%s TX COMPLETE file='%s' path=%s size=%dB chunks=%d elapsed=%.2fs "
                "speed=%.2fKB/s crc=%s stored_id=%d final_ack=%s",
                LogTag.FILE_SVC,
                fname,
                fpath,
                self._bytes_transferred,
                self._total_chunks,
                elapsed,
                speed_kbps,
                crc_hex,
                rec_id,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )

    def _on_nack_received(self, pkt: Packet) -> None:
        """Handles NACK request by retransmitting up to retry limit."""
        if self._state not in (
            TransferState.HANDSHAKING,
            TransferState.TRANSFERRING,
            TransferState.FINALIZING,
        ):
            logger.debug(
                "%s Ignored NACK in %s: %s",
                LogTag.ARQ_ENGINE,
                self._state.name,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            return

        try:
            nack_info = AckPayload.unpack(pkt.payload)
            nack_detail = f"acked_seq={nack_info.acked_seq} status={ack_status_name(nack_info.status)}({nack_info.status})"
        except Exception:
            logger.warning(
                "%s NACK payload undecodable %s — ignored pending timeout",
                LogTag.ARQ_ENGINE,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            return
        # A late NACK for an older frame must not retransmit the current one or
        # consume its retry budget.
        if nack_info.acked_seq != self._seq:
            logger.warning(
                "%s Stale NACK ignored: expected seq=%d got acked_seq=%d state=%s frame=%s",
                LogTag.ARQ_ENGINE,
                self._seq,
                nack_info.acked_seq,
                self._state.name,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            return
        logger.warning(
            "%s NACK %s state=%s retry=%d/5 seq=%d %s payload_hex=%s",
            LogTag.ARQ_ENGINE,
            format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            self._state.name,
            self._retry_count + 1,
            self._seq,
            nack_detail,
            format_hex_preview(pkt.payload),
        )
        self._ack_timer.stop()
        self._retry_count += 1
        if self._retry_count > MAX_RETRY_COUNT:
            self._set_state(TransferState.FAILED)
            err_msg = f"Transfer failed: Max retries ({MAX_RETRY_COUNT}) exceeded on NACK"
            self._persist_failure(err_msg)
            self.transfer_failed.emit(err_msg)
            logger.error(
                "%s TX FAILED on NACK %s: %s (chunk=%d/%d seq=%d)",
                LogTag.ARQ_ENGINE,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                err_msg,
                self._current_chunk_idx,
                self._total_chunks,
                self._seq,
            )
            return

        self._retransmit()

    def _on_ack_timeout(self) -> None:
        """Handles Stop-and-Wait ACK timeout."""
        if self._state not in (
            TransferState.HANDSHAKING,
            TransferState.TRANSFERRING,
            TransferState.FINALIZING,
        ):
            logger.debug(
                "%s Ignored ACK timeout in %s",
                LogTag.ARQ_ENGINE,
                self._state.name,
            )
            return

        self._retry_count += 1
        if self._retry_count > MAX_RETRY_COUNT:
            self._set_state(TransferState.FAILED)
            err_msg = f"Transfer failed: ACK timeout after {MAX_RETRY_COUNT} retries"
            self._persist_failure(err_msg)
            self.transfer_failed.emit(err_msg)
            logger.error(
                "%s TX FAILED timeout state=%s chunk=%d/%d seq=%d retry=%d/5: %s",
                LogTag.ARQ_ENGINE,
                self._state.name,
                self._current_chunk_idx,
                self._total_chunks,
                self._seq,
                self._retry_count,
                err_msg,
            )
            return

        logger.warning(
            "%s ACK timeout 250ms (retry %d/%d) state=%s chunk=%d/%d seq=%d sent=%dB",
            LogTag.ARQ_ENGINE,
            self._retry_count,
            MAX_RETRY_COUNT,
            self._state.name,
            self._current_chunk_idx,
            self._total_chunks,
            self._seq,
            self._bytes_transferred,
        )
        self._retransmit()

    def _retransmit(self) -> None:
        """Retransmits current unacknowledged packet using existing sequence number."""
        logger.info(
            "%s Retransmit state=%s retry=%d/5 seq=%d chunk=%d/%d",
            LogTag.ARQ_ENGINE,
            self._state.name,
            self._retry_count,
            self._seq,
            self._current_chunk_idx,
            self._total_chunks,
        )
        if self._state == TransferState.HANDSHAKING:
            # Reuse the packed START bytes from start_transfer (identical content).
            if self._cached_start_payload is None and self._current_file_path:
                start_payload = FileStartPayload(
                    file_size=self._file_size,
                    chunk_size=self._chunk_size,
                    total_chunks=self._total_chunks,
                    file_crc32=self._file_crc32,
                    filename=self._current_file_path.name,
                )
                self._cached_start_payload = start_payload.pack()
            if self._cached_start_payload is not None:
                self._serial_manager.send_packet(
                    seq=self._seq,
                    opcode=Opcode.TRANSFER_START,
                    payload=self._cached_start_payload,
                )
                self._ack_timer.start()

        elif self._state == TransferState.TRANSFERRING:
            self._send_current_chunk(new_seq=False)

        elif self._state == TransferState.FINALIZING:
            self._send_file_end(new_seq=False)
