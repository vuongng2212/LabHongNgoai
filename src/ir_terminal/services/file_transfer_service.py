"""Stop-and-Wait ARQ optical file transfer service with streaming disk I/O.

The service streams files chunk-by-chunk in both directions:

* TX never loads the whole file into RAM: the CRC-32 is computed with a
  streaming pass and each chunk is read from the open file on demand.
* RX writes chunks to a ``.part`` file inside the receive directory and
  verifies size + CRC-32 before atomically renaming it into place.

Small transfers use the legacy wire payloads (``u32`` size, ``u16`` chunk id).
When a file needs more than 65535 chunks (or a size above ``u32``), the service
automatically switches to the large-transfer payloads (``u64`` size, ``u32``
chunk id), so there is no product-level file-size limit. Cancellation is
symmetric: either side can send ``TRANSFER_PAUSE`` with an ``ABORT`` reason and
both sides release state and temporary files.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
import zlib
from enum import Enum, auto
from pathlib import Path
from typing import BinaryIO, ClassVar

from PySide6.QtCore import QObject, QTimer, Signal

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import (
    DEFAULT_ACK_TIMEOUT_SEC,
    DEFAULT_CHUNK_SIZE,
    MAX_CHUNK_DATA_SIZE,
    MAX_EXTENDED_CHUNK_COUNT,
    MAX_EXTENDED_CHUNK_DATA_SIZE,
    MAX_FILE_SIZE_BYTES,
    MAX_PAYLOAD_SIZE,
    MAX_RETRY_COUNT,
    MAX_V1_CHUNK_COUNT,
    PROTOCOL_MAX_FILE_SIZE_BYTES,
    AckStatus,
    Opcode,
)
from ir_terminal.protocol.file_payloads import (
    AckPayload,
    FileChunkPayload,
    FileChunkPayloadLarge,
    FileEndPayload,
    FileEndPayloadLarge,
    FileStartPayload,
    FileStartPayloadLarge,
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

# 0 means unlimited: the client asked for no application-level file cap in
# order to measure real optical throughput. Positive values remain available
# as an optional operator safety cap.
MIN_FILE_SIZE_BYTES: int = 0
MAX_CONFIGURABLE_FILE_SIZE_BYTES: int = PROTOCOL_MAX_FILE_SIZE_BYTES

# Cancellation reason carried by TRANSFER_PAUSE (ASCII, per PROTOCOL.md).
CANCEL_REASON: bytes = b"ABORT"

# Streaming CRC read size (bytes). Keeps the pre-transfer checksum pass at O(1) RAM.
CRC_STREAM_BUFFER_SIZE: int = 1024 * 1024


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
        # Immutable default used by the Web UI "Reset to default" action. It is
        # resolved once from the injected path (instance-aware) and never
        # changed by set_transfers_dir/load_settings.
        self._default_transfers_dir = self._transfers_dir
        self._max_file_size_bytes = MAX_FILE_SIZE_BYTES

        self._state = TransferState.IDLE

        # TX Session State (streaming: only the current chunk is held in RAM)
        self._tx_active: bool = False
        self._current_file_path: Path | None = None
        self._tx_file: BinaryIO | None = None
        self._file_size: int = 0
        self._file_crc32: int = 0
        self._total_chunks: int = 0
        self._current_chunk_idx: int = 0
        self._seq: int = 0
        self._retry_count: int = 0
        self._use_extended: bool = False

        self._start_time: float = 0.0
        self._last_chunk_time: float = 0.0
        self._bytes_transferred: int = 0

        # RX Session State (streaming: chunks are written straight to a .part file)
        self._rx_filename: str = ""
        self._rx_file_size: int = 0
        self._rx_chunk_size: int = 0
        self._rx_total_chunks: int = 0
        self._rx_file_crc32: int = 0
        self._rx_file_handle: BinaryIO | None = None
        self._rx_temp_path: Path | None = None
        # Directory locked for the current RX session, captured at TRANSFER_START.
        # Both the .part temp file and the final publish resolve against this, so
        # changing the setting mid-transfer cannot move data across filesystems
        # (which would make os.replace fail with EXDEV).
        self._rx_dest_dir: Path | None = None
        self._rx_expected_chunk: int = 0
        self._rx_crc32: int = 0
        self._rx_start_time: float = 0.0
        self._rx_bytes_received: int = 0
        self._rx_received_count: int = 0
        self._rx_extended: bool = False
        # Last accepted RX session IDs: lets a retransmitted START/END (sent
        # because the peer lost our ACK) be re-acknowledged instead of being
        # rejected as a new/busy session.
        self._rx_start_seq: int = -1
        self._rx_end_seq: int = -1
        self._rx_end_status: int = AckStatus.OK

        # TX retransmit caches (identical bytes reused, no repack).
        # Invalidated on every new start_transfer/abort.
        self._cached_start_payload: bytes | None = None
        self._cached_chunk_key: int = -1
        self._cached_chunk_payload: bytes | None = None
        self._cached_chunk_len: int = 0
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
        """Returns the optional operator cap in bytes (0 = unlimited)."""
        return self._max_file_size_bytes

    @property
    def transfers_dir_path(self) -> Path:
        """Returns the directory where received optical files are stored."""
        return self._transfers_dir

    @property
    def default_transfers_dir_path(self) -> Path:
        """Returns the immutable default receive directory (`data/transfers`)."""
        return self._default_transfers_dir

    @property
    def uses_extended_protocol(self) -> bool:
        """Returns True when the active TX session uses the large-file payloads."""
        return self._use_extended

    def get_config(self) -> dict[str, str | int | float]:
        """Returns the current transfer configuration as JSON-serializable dict.

        Returns:
            Dict with max_file_size_bytes (0 = unlimited), max_file_size_mb,
            max_file_size_label, transfers_dir and default_transfers_dir.
        """
        unlimited = self._max_file_size_bytes == 0
        return {
            "max_file_size_bytes": self._max_file_size_bytes,
            "max_file_size_mb": (
                0.0
                if unlimited
                else round(self._max_file_size_bytes / (1024 * 1024), 2)
            ),
            "max_file_size_label": "Unlimited" if unlimited else "",
            "transfers_dir": str(self._transfers_dir),
            "default_transfers_dir": str(self._default_transfers_dir),
        }

    def load_settings(self) -> dict[str, str | int | float]:
        """Loads persisted transfer settings from StorageService into memory.

        Falls back to defaults when keys are missing or invalid. An explicit
        transfers_dir that does not exist is created; a persisted path that is
        unusable (points at a file, offline volume, permission error) is never
        silently swallowed: it logs a warning and keeps the default directory.

        Returns:
            Current configuration dict (same shape as get_config).
        """
        try:
            raw_limit = self._storage.get_setting(
                SETTING_MAX_FILE_SIZE_BYTES, str(MAX_FILE_SIZE_BYTES)
            )
            parsed = int(float(raw_limit))
            if 0 <= parsed <= MAX_CONFIGURABLE_FILE_SIZE_BYTES:
                self._max_file_size_bytes = parsed
        except (TypeError, ValueError):
            pass
        raw_dir = self._storage.get_setting(SETTING_TRANSFERS_DIR, "")
        if raw_dir.strip():
            try:
                candidate = Path(raw_dir).expanduser().resolve()
                candidate.mkdir(parents=True, exist_ok=True)
                if candidate.is_dir():
                    self._transfers_dir = candidate
                else:
                    logger.warning(
                        "%s Persisted transfers_dir is not a directory, keeping default: "
                        "saved=%s default=%s",
                        LogTag.FILE_SVC,
                        raw_dir,
                        self._default_transfers_dir,
                    )
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning(
                    "%s Persisted transfers_dir unusable (%s: %s), keeping default: "
                    "saved=%s default=%s",
                    LogTag.FILE_SVC,
                    type(exc).__name__,
                    exc,
                    raw_dir,
                    self._default_transfers_dir,
                )
        logger.info(
            "%s Transfer config loaded: max=%s dir=%s",
            LogTag.FILE_SVC,
            "unlimited" if self._max_file_size_bytes == 0 else f"{self._max_file_size_bytes}B",
            self._transfers_dir,
        )
        return self.get_config()

    def set_max_file_size_bytes(self, size_bytes: int) -> int:
        """Updates and persists the optional maximum accepted file size.

        Args:
            size_bytes: 0 for unlimited, or a positive cap in bytes. Values
                above the wire-format ceiling are clamped to that ceiling.

        Returns:
            Effective cap in bytes after validation (0 = unlimited).

        Raises:
            ValueError: If size_bytes is negative.
        """
        size = int(size_bytes)
        if size < 0:
            raise ValueError(f"Invalid max file size: {size_bytes}")
        clamped = min(size, MAX_CONFIGURABLE_FILE_SIZE_BYTES) if size else 0
        self._max_file_size_bytes = clamped
        self._storage.save_setting(SETTING_MAX_FILE_SIZE_BYTES, str(clamped))
        logger.info(
            "%s Transfer max file size set: %s",
            LogTag.FILE_SVC,
            "unlimited" if clamped == 0 else f"{clamped}B",
        )
        return clamped

    def set_transfers_dir(self, dir_path: str | Path) -> Path:
        """Updates and persists the default receive directory.

        Args:
            dir_path: Target directory path (created when missing). Accepts a
                manually typed/pasted path; ``~`` is expanded and the result is
                stored as an absolute, resolved path.

        Returns:
            Resolved absolute directory path.

        Raises:
            ValueError: If the path is empty, points at an existing file, or
                cannot be created/accessed (permissions, offline drive, ...).
        """
        if isinstance(dir_path, str) and not dir_path.strip():
            raise ValueError("Transfers directory path is empty")
        candidate = Path(dir_path).expanduser() if isinstance(dir_path, str) else dir_path
        if not str(candidate).strip():
            raise ValueError("Transfers directory path is empty")
        try:
            candidate = candidate.resolve()
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"Invalid transfers directory '{dir_path}': {exc}") from exc
        if candidate.exists() and not candidate.is_dir():
            raise ValueError(f"Not a directory: {candidate}")
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"Cannot create transfers directory {candidate}: {exc}") from exc
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
            max_file_size_bytes: Optional new cap in bytes (0 = unlimited).
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

    # =========================================================================
    # TX session helpers
    # =========================================================================
    def _close_tx_file(self) -> None:
        """Closes the streaming TX file handle without touching session metadata."""
        handle = self._tx_file
        self._tx_file = None
        if handle is not None:
            try:
                handle.close()
            except OSError as exc:
                logger.warning(
                    "%s Could not close TX file handle (%s: %s)",
                    LogTag.FILE_SVC,
                    type(exc).__name__,
                    exc,
                )

    def _reset_tx_session(self) -> None:
        """Releases TX resources and invalidates retransmit caches."""
        self._close_tx_file()
        self._tx_active = False
        self._current_file_path = None
        self._file_size = 0
        self._total_chunks = 0
        self._current_chunk_idx = 0
        self._bytes_transferred = 0
        self._retry_count = 0
        self._use_extended = False
        self._cached_start_payload = None
        self._cached_chunk_key = -1
        self._cached_chunk_payload = None
        self._cached_chunk_len = 0
        self._cached_end_key = -1
        self._cached_end_payload = None

    def _fail_tx(self, reason: str) -> None:
        """Stops an active TX session, persists a FAILED record and emits the error."""
        self._ack_timer.stop()
        self._close_tx_file()
        self._set_state(TransferState.FAILED)
        self._persist_failure(reason)
        self.transfer_failed.emit(reason)
        logger.error("%s TX FAILED: %s", LogTag.ARQ_ENGINE, reason)
        self._reset_tx_session()

    def _compute_file_crc(self, file_path: Path) -> int:
        """Computes whole-file CRC-32 with a bounded-memory streaming read."""
        crc = 0
        with file_path.open("rb") as handle:
            while True:
                block = handle.read(CRC_STREAM_BUFFER_SIZE)
                if not block:
                    break
                crc = zlib.crc32(block, crc)
        return crc & 0xFFFFFFFF

    # =========================================================================
    # RX session helpers
    # =========================================================================
    def _close_rx_file(self, remove_temp: bool) -> None:
        """Closes the RX .part handle and optionally deletes the partial file."""
        handle = self._rx_file_handle
        self._rx_file_handle = None
        if handle is not None:
            try:
                handle.flush()
                handle.close()
            except OSError as exc:
                logger.warning(
                    "%s Could not close RX part file (%s: %s)",
                    LogTag.FILE_SVC,
                    type(exc).__name__,
                    exc,
                )
        if remove_temp and self._rx_temp_path is not None:
            try:
                self._rx_temp_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning(
                    "%s Could not remove RX part file %s (%s: %s)",
                    LogTag.FILE_SVC,
                    self._rx_temp_path,
                    type(exc).__name__,
                    exc,
                )
        if remove_temp:
            self._rx_temp_path = None

    def _reset_rx_session(self, remove_temp: bool = True, keep_end_ack: bool = False) -> None:
        """Releases RX resources and resets per-session counters.

        Args:
            remove_temp: Delete the in-progress ``.part`` file when True.
            keep_end_ack: Preserve the last END seq/status so a retransmitted
                END can be re-ACKed without writing the file twice.
        """
        self._close_rx_file(remove_temp=remove_temp)
        self._rx_filename = ""
        self._rx_file_size = 0
        self._rx_chunk_size = 0
        self._rx_total_chunks = 0
        self._rx_file_crc32 = 0
        self._rx_expected_chunk = 0
        self._rx_crc32 = 0
        self._rx_bytes_received = 0
        self._rx_received_count = 0
        self._rx_extended = False
        self._rx_dest_dir = None
        if not keep_end_ack:
            self._rx_start_seq = -1
            self._rx_end_seq = -1
            self._rx_end_status = AckStatus.OK

    def _fail_rx(
        self,
        reason: str,
        status: int = AckStatus.OUT_OF_ORDER,
        end_seq: int | None = None,
    ) -> None:
        """Rejects/fails an RX session, sends NACK, removes temp data and emits."""
        if end_seq is not None:
            self._rx_end_seq = end_seq
            self._rx_end_status = status
        self._set_state(TransferState.FAILED)
        self._persist_failure(reason)
        self._reset_rx_session(remove_temp=True, keep_end_ack=end_seq is not None)
        self.transfer_failed.emit(reason)

    # =========================================================================
    # TX public flow
    # =========================================================================
    def start_transfer(self, file_path_str: str) -> None:
        """Starts transmitting a file over the optical link (streaming)."""
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

        file_size = file_path.stat().st_size
        if self._max_file_size_bytes and file_size > self._max_file_size_bytes:
            err_msg = (
                f"File exceeds configured limit {self._max_file_size_bytes}B: "
                f"{file_path.name} ({file_size} bytes)"
            )
            logger.error("%s TX start FAILED: %s", LogTag.FILE_SVC, err_msg)
            self.transfer_failed.emit(err_msg)
            return

        planned_chunks = (file_size + self._chunk_size - 1) // self._chunk_size if file_size else 1
        extended = planned_chunks > MAX_V1_CHUNK_COUNT or file_size > 0xFFFFFFFF
        max_chunk_data = (
            MAX_EXTENDED_CHUNK_DATA_SIZE if extended else MAX_CHUNK_DATA_SIZE
        )
        if not (1 <= self._chunk_size <= max_chunk_data):
            err_msg = (
                f"Invalid chunk size {self._chunk_size}B; protocol allows "
                f"1..{max_chunk_data}B (payload max {MAX_PAYLOAD_SIZE}B incl. chunk id)"
            )
            logger.error("%s TX start FAILED: %s", LogTag.FILE_SVC, err_msg)
            self.transfer_failed.emit(err_msg)
            return

        max_chunks = MAX_EXTENDED_CHUNK_COUNT if extended else MAX_V1_CHUNK_COUNT
        if planned_chunks > max_chunks:
            err_msg = (
                f"File requires {planned_chunks} chunks; protocol max is {max_chunks} "
                f"(file={file_size}B, chunk_size={self._chunk_size}B)."
            )
            logger.error("%s TX start FAILED: %s", LogTag.FILE_SVC, err_msg)
            self.transfer_failed.emit(err_msg)
            return

        try:
            file_crc32 = self._compute_file_crc(file_path)
            tx_file = file_path.open("rb")
        except OSError as exc:
            err_msg = f"Failed to read file: {exc}"
            logger.exception(
                "%s TX start FAILED: cannot read %s (%s: %s)",
                LogTag.FILE_SVC,
                file_path,
                type(exc).__name__,
                exc,
            )
            self.transfer_failed.emit(err_msg)
            return

        self._tx_active = True
        self._current_file_path = file_path
        self._tx_file = tx_file
        self._file_size = file_size
        self._file_crc32 = file_crc32
        self._total_chunks = planned_chunks
        self._current_chunk_idx = 0
        self._retry_count = 0
        self._bytes_transferred = 0
        self._use_extended = extended
        self._start_time = time.time()
        self._last_chunk_time = self._start_time
        # Invalidate retransmit caches for the new session
        self._cached_start_payload = None
        self._cached_chunk_key = -1
        self._cached_chunk_payload = None
        self._cached_chunk_len = 0
        self._cached_end_key = -1
        self._cached_end_payload = None

        self._set_state(TransferState.HANDSHAKING)

        start_payload: FileStartPayload | FileStartPayloadLarge
        if extended:
            start_payload = FileStartPayloadLarge(
                file_size=self._file_size,
                chunk_size=self._chunk_size,
                total_chunks=self._total_chunks,
                file_crc32=self._file_crc32,
                filename=file_path.name,
            )
        else:
            start_payload = FileStartPayload(
                file_size=self._file_size,
                chunk_size=self._chunk_size,
                total_chunks=self._total_chunks,
                file_crc32=self._file_crc32,
                filename=file_path.name,
            )
        self._cached_start_payload = start_payload.pack()
        start_opcode = (
            Opcode.TRANSFER_START_LARGE if extended else Opcode.TRANSFER_START
        )

        self._seq = (self._seq + 1) % 256
        self._serial_manager.send_packet(
            seq=self._seq,
            opcode=start_opcode,
            payload=self._cached_start_payload,
        )
        self._ack_timer.start()
        logger.info(
            "%s TX START %s file='%s' size=%dB chunks=%d chunk_size=%dB "
            "file_crc=0x%08X payload_hex=%s timeout=250ms retry=0/5",
            LogTag.FILE_SVC,
            format_frame_summary(self._seq, start_opcode, len(self._cached_start_payload)),
            file_path.name,
            self._file_size,
            self._total_chunks,
            self._chunk_size,
            self._file_crc32,
            format_hex_preview(self._cached_start_payload, max_bytes=24),
        )

    def abort_transfer(self) -> None:
        """Aborts the current transmission or reception and notifies the peer.

        Sender and receiver both release file handles, delete partial data and
        send a ``TRANSFER_PAUSE`` frame carrying the ``ABORT`` reason. The peer
        acknowledges the cancel and tears down its own half-session.
        """
        if self._state == TransferState.RECEIVING:
            rx_file = self._rx_filename
            rx_had = (self._rx_received_count, self._rx_total_chunks, self._rx_bytes_received)
            self._seq = (self._seq + 1) % 256
            self._serial_manager.send_packet(
                seq=self._seq,
                opcode=Opcode.TRANSFER_PAUSE,
                payload=CANCEL_REASON,
            )
            self._set_state(TransferState.ABORTED)
            self._persist_failure("Incoming transfer aborted by user")
            self._reset_rx_session(remove_temp=True)
            self.transfer_failed.emit("Incoming transfer aborted by user")
            logger.info(
                "%s RX aborted by user (cancel sent) %s file='%s' had=%d/%d chunks (%dB)",
                LogTag.FILE_SVC,
                format_frame_summary(self._seq, Opcode.TRANSFER_PAUSE, len(CANCEL_REASON)),
                rx_file,
                rx_had[0],
                rx_had[1],
                rx_had[2],
            )
            return

        if self._state in (
            TransferState.HANDSHAKING,
            TransferState.TRANSFERRING,
            TransferState.FINALIZING,
        ):
            self._ack_timer.stop()
            self._close_tx_file()
            fname = self._current_file_path.name if self._current_file_path else "?"
            sent_bytes = self._bytes_transferred
            chunk_idx = self._current_chunk_idx
            total_chunks = self._total_chunks
            self._seq = (self._seq + 1) % 256
            self._serial_manager.send_packet(
                seq=self._seq,
                opcode=Opcode.TRANSFER_PAUSE,
                payload=CANCEL_REASON,
            )
            self._set_state(TransferState.ABORTED)
            self._persist_failure("Transfer cancelled by user")
            self._reset_tx_session()
            self.transfer_failed.emit("Transfer cancelled by user")
            logger.info(
                "%s TX aborted by user (cancel sent) %s file='%s' sent=%dB chunk=%d/%d",
                LogTag.FILE_SVC,
                format_frame_summary(self._seq, Opcode.TRANSFER_PAUSE, len(CANCEL_REASON)),
                fname,
                sent_bytes,
                chunk_idx,
                total_chunks,
            )

    def _send_current_chunk(self, new_seq: bool = True) -> None:
        """Transmits the current binary chunk read from disk.

        Args:
            new_seq: True to increment the sequence number for a new chunk;
                False to reuse the current sequence number for retransmission.
        """
        # Retransmission of the same chunk reuses the identical packed payload
        # (only the outer frame seq is reused); new chunks read one chunk from
        # the open file, so RAM usage stays O(chunk_size).
        if not new_seq and self._cached_chunk_payload is not None and self._cached_chunk_key == self._current_chunk_idx:
            packed = self._cached_chunk_payload
            chunk_len = self._cached_chunk_len
        else:
            start_offset = self._current_chunk_idx * self._chunk_size
            expected_len = min(
                self._chunk_size, max(self._file_size - start_offset, 0)
            )
            if self._tx_file is None:
                self._fail_tx("TX file handle is closed")
                return
            try:
                self._tx_file.seek(start_offset)
                chunk_data = self._tx_file.read(expected_len)
            except OSError as exc:
                self._fail_tx(f"Failed to read file chunk: {exc}")
                return
            if len(chunk_data) != expected_len:
                self._fail_tx(
                    f"Short read at chunk {self._current_chunk_idx}: "
                    f"{len(chunk_data)}/{expected_len} bytes"
                )
                return

            if self._use_extended:
                packed = FileChunkPayloadLarge(
                    chunk_id=self._current_chunk_idx,
                    chunk_data=chunk_data,
                ).pack()
            else:
                packed = FileChunkPayload(
                    chunk_id=self._current_chunk_idx,
                    chunk_data=chunk_data,
                ).pack()
            self._cached_chunk_key = self._current_chunk_idx
            self._cached_chunk_payload = packed
            self._cached_chunk_len = len(chunk_data)
            chunk_len = len(chunk_data)

        if new_seq:
            self._seq = (self._seq + 1) % 256

        opcode = Opcode.DATA_CHUNK_LARGE if self._use_extended else Opcode.DATA_CHUNK
        self._serial_manager.send_packet(
            seq=self._seq,
            opcode=opcode,
            payload=packed,
        )
        self._ack_timer.start()
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s TX CHUNK %s chunk_id=%d (%d/%d) chunk_bytes=%dB payload_hex=%s %s",
                LogTag.ARQ_ENGINE,
                format_frame_summary(self._seq, opcode, len(packed)),
                self._current_chunk_idx,
                self._current_chunk_idx + 1,
                self._total_chunks,
                chunk_len,
                format_hex_preview(packed, max_bytes=16),
                "RETRY" if not new_seq else f"retry={self._retry_count}/5",
            )

    def _send_file_end(self, new_seq: bool = True) -> None:
        """Transmits the completion verification frame.

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
            if self._use_extended:
                end_payload: FileEndPayload | FileEndPayloadLarge = FileEndPayloadLarge(
                    file_crc32=self._file_crc32,
                    total_bytes_sent=self._bytes_transferred,
                )
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

        opcode = Opcode.TRANSFER_END_LARGE if self._use_extended else Opcode.TRANSFER_END
        self._serial_manager.send_packet(
            seq=self._seq,
            opcode=opcode,
            payload=packed_end,
        )
        self._ack_timer.start()
        logger.info(
            "%s TX END %s file_crc=0x%08X bytes=%dB payload_hex=%s %s",
            LogTag.FILE_SVC,
            format_frame_summary(self._seq, opcode, len(packed_end)),
            self._file_crc32,
            self._bytes_transferred,
            format_hex_preview(packed_end),
            "RETRY" if not new_seq else f"retry={self._retry_count}/5",
        )

    def _persist_failure(self, reason: str) -> None:
        """Persists a FAILED record so lab runs keep an audit trail."""
        try:
            if self._tx_active or self._current_file_path is not None:
                fname = self._current_file_path.name if self._current_file_path else "unknown"
                fpath = str(self._current_file_path) if self._current_file_path else ""
                size = self._file_size
                direction = "TX"
                crc_hex = f"0x{self._file_crc32:08X}"
                start_time = self._start_time
            else:
                fname = self._rx_filename or "unknown"
                fpath = str(self._rx_temp_path) if self._rx_temp_path else ""
                size = self._rx_file_size
                direction = "RX"
                crc_hex = f"0x{self._rx_crc32:08X}" if self._rx_crc32 else ""
                start_time = self._rx_start_time
            rec_id = self._storage.save_file_transfer(
                filename=fname,
                file_path=fpath,
                direction=direction,
                size_bytes=size,
                crc32_hex=crc_hex,
                speed_kbps=0.0,
                duration_sec=round(max(time.time() - (start_time or time.time()), 0.0), 2),
                status="FAILED",
            )
            logger.info(
                "%s Persisted FAILED record #%d: file='%s' state=%s reason=%s",
                LogTag.FILE_SVC,
                rec_id,
                fname,
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

    # =========================================================================
    # Packet dispatch
    # =========================================================================
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
        elif pkt.opcode in (Opcode.TRANSFER_START, Opcode.TRANSFER_START_LARGE):
            self._on_transfer_start_received(pkt)
        elif pkt.opcode in (Opcode.DATA_CHUNK, Opcode.DATA_CHUNK_LARGE):
            self._on_data_chunk_received(pkt)
        elif pkt.opcode in (Opcode.TRANSFER_END, Opcode.TRANSFER_END_LARGE):
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
        """Handles a remote ABORT/PAUSE cancel for either half of the session."""
        reason = pkt.payload.decode("utf-8", errors="replace") if pkt.payload else "PAUSED"

        if self._state == TransferState.RECEIVING:
            rx_file = self._rx_filename
            rx_had = (self._rx_received_count, self._rx_total_chunks, self._rx_bytes_received)
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OK)
            self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.ACK, payload=ack.pack())
            self._set_state(TransferState.ABORTED)
            self._persist_failure(f"Remote aborted transfer: {reason}")
            self._reset_rx_session(remove_temp=True)
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
            return

        if self._state in (
            TransferState.HANDSHAKING,
            TransferState.TRANSFERRING,
            TransferState.FINALIZING,
        ):
            fname = self._current_file_path.name if self._current_file_path else "?"
            sent_bytes = self._bytes_transferred
            self._ack_timer.stop()
            self._close_tx_file()
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OK)
            self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.ACK, payload=ack.pack())
            self._set_state(TransferState.ABORTED)
            self._persist_failure(f"Remote cancelled transfer: {reason}")
            self._reset_tx_session()
            self.transfer_failed.emit(f"Remote cancelled transfer: {reason}")
            logger.info(
                "%s TX remote-abort %s reason=%r file='%s' sent=%dB",
                LogTag.FILE_SVC,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                reason,
                fname,
                sent_bytes,
            )
            return

        logger.debug(
            "%s Ignored TRANSFER_PAUSE in %s: %s",
            LogTag.FILE_SVC,
            self._state.name,
            format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
        )

    def _is_duplicate_start(
        self,
        pkt: Packet,
        start_payload: FileStartPayload | FileStartPayloadLarge,
        extended: bool,
    ) -> bool:
        """Returns True when a START retransmits the currently accepted RX session."""
        return (
            self._state == TransferState.RECEIVING
            and extended == self._rx_extended
            and pkt.seq == self._rx_start_seq
            and start_payload.file_size == self._rx_file_size
            and start_payload.chunk_size == self._rx_chunk_size
            and start_payload.total_chunks == self._rx_total_chunks
            and start_payload.file_crc32 == self._rx_file_crc32
            and start_payload.filename == self._rx_filename
        )

    def _on_transfer_start_received(self, pkt: Packet) -> None:
        """Handles an incoming TRANSFER_START frame by preparing the RX stream and ACKing."""
        extended = pkt.opcode == Opcode.TRANSFER_START_LARGE
        try:
            if extended:
                start_payload: FileStartPayload | FileStartPayloadLarge = (
                    FileStartPayloadLarge.unpack(pkt.payload)
                )
            else:
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
        if self._is_duplicate_start(pkt, start_payload, extended):
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

        if self._max_file_size_bytes and start_payload.file_size > self._max_file_size_bytes:
            err_msg = (
                f"Incoming file exceeds configured limit {self._max_file_size_bytes}B: "
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

        max_chunk_data = (
            MAX_EXTENDED_CHUNK_DATA_SIZE if extended else MAX_CHUNK_DATA_SIZE
        )
        if start_payload.chunk_size == 0 or start_payload.chunk_size > max_chunk_data:
            err_msg = (
                f"Incoming transfer has invalid chunk size: {start_payload.chunk_size} "
                f"(max {max_chunk_data}B data inside {MAX_PAYLOAD_SIZE}B payload)"
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

        max_chunks = MAX_EXTENDED_CHUNK_COUNT if extended else MAX_V1_CHUNK_COUNT
        expected_chunks = (
            (start_payload.file_size + start_payload.chunk_size - 1)
            // start_payload.chunk_size
            if start_payload.file_size
            else 1
        )
        if (
            start_payload.total_chunks == 0
            or start_payload.total_chunks > max_chunks
            or start_payload.total_chunks != expected_chunks
        ):
            err_msg = (
                f"Incoming transfer has invalid total_chunks: {start_payload.total_chunks} "
                f"(expected {expected_chunks}, max {max_chunks})"
            )
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

        # Open the streaming destination: chunks are written straight to disk,
        # never accumulated in a dict, so memory stays O(chunk_size). The target
        # directory is locked for the whole session so a mid-transfer settings
        # change cannot send the final os.replace across filesystems (EXDEV).
        dest_dir = self._transfers_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            fd, temp_name = tempfile.mkstemp(
                prefix=".ir_rx_", suffix=".part", dir=dest_dir
            )
            rx_handle = os.fdopen(fd, "wb")
        except OSError as exc:
            err_msg = f"Cannot create receive file: {exc}"
            logger.exception("%s RX START FAILED: %s", LogTag.FILE_SVC, err_msg)
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OUT_OF_ORDER)
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
        self._rx_file_handle = rx_handle
        self._rx_temp_path = Path(temp_name)
        self._rx_dest_dir = dest_dir
        self._rx_expected_chunk = 0
        self._rx_crc32 = 0
        self._rx_start_time = time.time()
        self._rx_bytes_received = 0
        self._rx_received_count = 0
        self._rx_extended = extended
        self._rx_start_seq = pkt.seq
        self._rx_end_seq = -1
        self._rx_end_status = AckStatus.OK

        self._set_state(TransferState.RECEIVING)
        logger.info(
            "%s RX START accepted %s file='%s' size=%dB chunks=%d chunk_size=%dB "
            "file_crc=0x%08X extended=%s part=%s",
            LogTag.FILE_SVC,
            format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload), pkt.crc32),
            self._rx_filename,
            self._rx_file_size,
            self._rx_total_chunks,
            self._rx_chunk_size,
            self._rx_file_crc32,
            extended,
            self._rx_temp_path,
        )

        ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OK)
        self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.ACK, payload=ack.pack())
        self.progress_updated.emit(0, 0.0, 0.0, 0, max(self._rx_total_chunks, 1))

    def _on_data_chunk_received(self, pkt: Packet) -> None:
        """Handles an incoming DATA_CHUNK by appending it to the streaming RX file."""
        if self._state != TransferState.RECEIVING:
            logger.warning(
                "%s RX CHUNK out-of-state: state=%s | %s",
                LogTag.ARQ_ENGINE,
                self._state.name,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            return

        extended = pkt.opcode == Opcode.DATA_CHUNK_LARGE
        if extended != self._rx_extended:
            logger.warning(
                "%s RX CHUNK opcode/format mismatch: extended=%s session_extended=%s | %s",
                LogTag.ARQ_ENGINE,
                extended,
                self._rx_extended,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OUT_OF_ORDER)
            self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            return

        try:
            if extended:
                chunk_payload: FileChunkPayload | FileChunkPayloadLarge = (
                    FileChunkPayloadLarge.unpack(pkt.payload)
                )
            else:
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

        chunk_id = chunk_payload.chunk_id
        chunk_data = chunk_payload.chunk_data
        if chunk_id >= self._rx_total_chunks:
            logger.error(
                "%s RX CHUNK id=%d out of range (total=%d) | %s data_len=%dB",
                LogTag.ARQ_ENGINE,
                chunk_id,
                self._rx_total_chunks,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                len(chunk_data),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OUT_OF_ORDER)
            self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            return

        if chunk_id < self._rx_expected_chunk:
            # Duplicate retransmission after our ACK was lost: re-ACK, do not
            # append the same bytes twice.
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OK)
            self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.ACK, payload=ack.pack())
            logger.debug(
                "%s RX CHUNK duplicate id=%d (< expected %d) re-ACK | %s",
                LogTag.ARQ_ENGINE,
                chunk_id,
                self._rx_expected_chunk,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            return

        if chunk_id > self._rx_expected_chunk:
            logger.warning(
                "%s RX CHUNK out-of-order id=%d expected=%d | %s",
                LogTag.ARQ_ENGINE,
                chunk_id,
                self._rx_expected_chunk,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OUT_OF_ORDER)
            self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            return

        start_offset = chunk_id * self._rx_chunk_size
        expected_len = min(
            self._rx_chunk_size, max(self._rx_file_size - start_offset, 0)
        )
        if len(chunk_data) != expected_len:
            logger.error(
                "%s RX CHUNK length mismatch id=%d got=%dB expected=%dB | %s",
                LogTag.ARQ_ENGINE,
                chunk_id,
                len(chunk_data),
                expected_len,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.CRC_ERROR)
            self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            return

        if self._rx_file_handle is None:
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OUT_OF_ORDER)
            self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            self._fail_rx("Receive file handle is closed", status=AckStatus.OUT_OF_ORDER)
            return

        try:
            self._rx_file_handle.write(chunk_data)
            # Flush per chunk so the .part file reflects durable progress and
            # a crash never leaves megabytes of unflushed buffered chunks.
            self._rx_file_handle.flush()
        except OSError as exc:
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OUT_OF_ORDER)
            self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            self._fail_rx(f"Failed to write received chunk: {exc}", status=AckStatus.OUT_OF_ORDER)
            return

        self._rx_crc32 = zlib.crc32(chunk_data, self._rx_crc32) & 0xFFFFFFFF
        self._rx_bytes_received += len(chunk_data)
        self._rx_received_count += 1
        self._rx_expected_chunk += 1

        # Respond ACK
        ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OK)
        self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.ACK, payload=ack.pack())

        # Progress calculation
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
            "%s RX CHUNK id=%d (%d/%d) %s data=%dB total_rx=%dB elapsed=%.2fs pct=%d%%",
            LogTag.ARQ_ENGINE,
            chunk_id,
            received_count,
            total,
            format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            len(chunk_data),
            self._rx_bytes_received,
            elapsed,
            pct,
        )

    def _on_transfer_end_received(self, pkt: Packet) -> None:
        """Handles TRANSFER_END: verify size + CRC-32, then atomically publish the file."""
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

        extended = pkt.opcode == Opcode.TRANSFER_END_LARGE
        if extended != self._rx_extended:
            self._fail_rx(
                "TRANSFER_END opcode/format mismatch",
                status=AckStatus.OUT_OF_ORDER,
                end_seq=pkt.seq,
            )
            return

        try:
            if extended:
                end_payload: FileEndPayload | FileEndPayloadLarge = FileEndPayloadLarge.unpack(
                    pkt.payload
                )
            else:
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
            self._fail_rx(
                "Corrupted TRANSFER_END payload",
                status=AckStatus.CRC_ERROR,
                end_seq=pkt.seq,
            )
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

        if self._rx_received_count != self._rx_total_chunks:
            missing = self._rx_expected_chunk
            logger.error(
                "%s RX END missing chunk %d/%d upon TRANSFER_END | %s",
                LogTag.ARQ_ENGINE,
                missing,
                self._rx_total_chunks,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            self._fail_rx(
                f"Missing chunk {missing} upon completion",
                status=AckStatus.OUT_OF_ORDER,
                end_seq=pkt.seq,
            )
            return

        if (
            self._rx_bytes_received != end_payload.total_bytes_sent
            or self._rx_bytes_received != self._rx_file_size
        ):
            logger.error(
                "%s RX END size mismatch: streamed=%dB end_total=%dB start_file_size=%dB | %s",
                LogTag.FILE_SVC,
                self._rx_bytes_received,
                end_payload.total_bytes_sent,
                self._rx_file_size,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            self._fail_rx(
                f"Size mismatch: streamed {self._rx_bytes_received}B, "
                f"END {end_payload.total_bytes_sent}B, START {self._rx_file_size}B",
                status=AckStatus.CRC_ERROR,
                end_seq=pkt.seq,
            )
            return

        # CRC-32 verification (computed incrementally during streaming writes)
        calculated_crc = self._rx_crc32 & 0xFFFFFFFF
        if calculated_crc != end_payload.file_crc32 or calculated_crc != self._rx_file_crc32:
            logger.error(
                "%s RX CRC MISMATCH: calc=0x%08X end_frame=0x%08X start_frame=0x%08X "
                "streamed=%dB expected=%dB | %s",
                LogTag.FILE_SVC,
                calculated_crc,
                end_payload.file_crc32,
                self._rx_file_crc32,
                self._rx_bytes_received,
                self._rx_file_size,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            self._fail_rx(
                f"Checksum mismatch: 0x{calculated_crc:08X} != 0x{end_payload.file_crc32:08X}",
                status=AckStatus.CRC_ERROR,
                end_seq=pkt.seq,
            )
            return

        temp_path = self._rx_temp_path
        rx_filename = self._rx_filename
        rx_size = self._rx_bytes_received
        rx_start_time = self._rx_start_time
        self._close_rx_file(remove_temp=False)
        if temp_path is None:
            self._fail_rx(
                "Receive temp file disappeared before publish",
                status=AckStatus.OUT_OF_ORDER,
                end_seq=pkt.seq,
            )
            return

        safe_name = Path(rx_filename).name or "received_file.bin"
        if safe_name in (".", ".."):
            safe_name = "received_file.bin"
        # Publish into the directory locked at TRANSFER_START, not the current
        # setting, so changing the setting mid-transfer cannot trigger EXDEV.
        dest_dir = self._rx_dest_dir or self._transfers_dir
        dest_path = dest_dir / safe_name
        if dest_path.exists():
            dest_path = dest_dir / f"{dest_path.stem}_{int(time.time())}{dest_path.suffix}"

        try:
            os.replace(temp_path, dest_path)
        except OSError as exc:
            logger.exception(
                "%s RX publish FAILED: temp=%s dest=%s (%s: %s)",
                LogTag.FILE_SVC,
                temp_path,
                dest_path,
                type(exc).__name__,
                exc,
            )
            self._rx_temp_path = temp_path
            self._fail_rx(
                f"Could not save received file: {exc}",
                status=AckStatus.OUT_OF_ORDER,
                end_seq=pkt.seq,
            )
            return
        self._rx_temp_path = None

        # Send ACK
        ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OK)
        self._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.ACK, payload=ack.pack())

        elapsed = max(time.time() - rx_start_time, 0.001)
        speed_kbps = (rx_size / 1024.0) / elapsed
        crc_hex = f"0x{calculated_crc:08X}"

        # Persist to database
        rec_id = self._storage.save_file_transfer(
            filename=dest_path.name,
            file_path=str(dest_path),
            direction="RX",
            size_bytes=rx_size,
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
        self.transfer_completed.emit(dest_path.name, rx_size, elapsed, crc_hex)
        logger.info(
            "%s RX COMPLETE %s file='%s' path=%s size=%dB elapsed=%.2fs "
            "speed=%.2fKB/s crc=%s stored_id=%d",
            LogTag.FILE_SVC,
            format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload), pkt.crc32),
            dest_path.name,
            dest_path,
            rx_size,
            elapsed,
            speed_kbps,
            crc_hex,
            rec_id,
        )
        self._reset_rx_session(remove_temp=False, keep_end_ack=True)

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
                self._fail_tx(
                    f"Transfer failed: Remote error status {status} after "
                    f"{MAX_RETRY_COUNT} retries"
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
            total_chunks = self._total_chunks
            bytes_transferred = self._bytes_transferred
            self._set_state(TransferState.COMPLETED)

            # Persist transfer record into hard drive JSON store
            rec_id = self._storage.save_file_transfer(
                filename=fname,
                file_path=fpath,
                direction="TX",
                size_bytes=bytes_transferred,
                crc32_hex=crc_hex,
                speed_kbps=round(speed_kbps, 2),
                duration_sec=round(elapsed, 2),
                status="SUCCESS",
            )

            self.transfer_completed.emit(fname, bytes_transferred, elapsed, crc_hex)
            logger.info(
                "%s TX COMPLETE file='%s' path=%s size=%dB chunks=%d elapsed=%.2fs "
                "speed=%.2fKB/s crc=%s stored_id=%d final_ack=%s",
                LogTag.FILE_SVC,
                fname,
                fpath,
                bytes_transferred,
                total_chunks,
                elapsed,
                speed_kbps,
                crc_hex,
                rec_id,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            self._reset_tx_session()

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
            self._fail_tx(
                f"Transfer failed: Max retries ({MAX_RETRY_COUNT}) exceeded on NACK"
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
            self._fail_tx(f"Transfer failed: ACK timeout after {MAX_RETRY_COUNT} retries")
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
                if self._use_extended:
                    rebuilt: FileStartPayload | FileStartPayloadLarge = FileStartPayloadLarge(
                        file_size=self._file_size,
                        chunk_size=self._chunk_size,
                        total_chunks=self._total_chunks,
                        file_crc32=self._file_crc32,
                        filename=self._current_file_path.name,
                    )
                else:
                    rebuilt = FileStartPayload(
                        file_size=self._file_size,
                        chunk_size=self._chunk_size,
                        total_chunks=self._total_chunks,
                        file_crc32=self._file_crc32,
                        filename=self._current_file_path.name,
                    )
                self._cached_start_payload = rebuilt.pack()
            if self._cached_start_payload is not None:
                opcode = (
                    Opcode.TRANSFER_START_LARGE
                    if self._use_extended
                    else Opcode.TRANSFER_START
                )
                self._serial_manager.send_packet(
                    seq=self._seq,
                    opcode=opcode,
                    payload=self._cached_start_payload,
                )
                self._ack_timer.start()

        elif self._state == TransferState.TRANSFERRING:
            self._send_current_chunk(new_seq=False)

        elif self._state == TransferState.FINALIZING:
            self._send_file_end(new_seq=False)
