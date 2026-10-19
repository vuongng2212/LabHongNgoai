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
import time
from pathlib import Path
from typing import BinaryIO, ClassVar

from PySide6.QtCore import QObject, QTimer, Signal

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import (
    DEFAULT_ACK_TIMEOUT_SEC,
    DEFAULT_CHUNK_SIZE,
    MAX_FILE_SIZE_BYTES,
    PROTOCOL_MAX_FILE_SIZE_BYTES,
    AckStatus,
    Opcode,
)
from ir_terminal.protocol.file_payloads import FileStartPayload, FileStartPayloadLarge
from ir_terminal.protocol.packet import Packet
from ir_terminal.services.file_transfer.file_transfer_types import TransferState as TransferState
from ir_terminal.services.file_transfer.integrity_verifier import IntegrityVerifier
from ir_terminal.services.file_transfer.rx_manager import RxManager
from ir_terminal.services.file_transfer.state_coordinator import StateCoordinator
from ir_terminal.services.file_transfer.tx_manager import TxManager
from ir_terminal.services.storage_service import StorageService
from ir_terminal.utils.logger import (
    LogTag,
    format_frame_summary,
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

        self._state_coordinator = StateCoordinator()

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
        self._tx_manager = TxManager(self)
        self._rx_manager = RxManager(self)
        self.load_settings()

    # Manager methods remain behind the façade so callers and Qt signal wiring
    # keep the same API while transfer behavior lives in focused collaborators.
    def start_transfer(self, file_path_str: str) -> None:
        """Starts a file transfer through the TX manager."""
        self._tx_manager.start_transfer(file_path_str)

    def _close_tx_file(self) -> None:
        self._tx_manager._close_tx_file()

    def _reset_tx_session(self) -> None:
        self._tx_manager._reset_tx_session()

    def _fail_tx(self, reason: str) -> None:
        self._tx_manager._fail_tx(reason)

    def _send_current_chunk(self, new_seq: bool = True) -> None:
        self._tx_manager._send_current_chunk(new_seq)

    def _send_file_end(self, new_seq: bool = True) -> None:
        self._tx_manager._send_file_end(new_seq)

    def _on_ack_received(self, pkt: Packet) -> None:
        self._tx_manager._on_ack_received(pkt)

    def _on_nack_received(self, pkt: Packet) -> None:
        self._tx_manager._on_nack_received(pkt)

    def _on_ack_timeout(self) -> None:
        self._tx_manager._on_ack_timeout()

    def _retransmit(self) -> None:
        self._tx_manager._retransmit()

    def _close_rx_file(self, remove_temp: bool) -> None:
        self._rx_manager._close_rx_file(remove_temp)

    def _reset_rx_session(self, remove_temp: bool = True, keep_end_ack: bool = False) -> None:
        self._rx_manager._reset_rx_session(remove_temp, keep_end_ack)

    def _fail_rx(
        self,
        reason: str,
        status: int = AckStatus.OUT_OF_ORDER,
        end_seq: int | None = None,
    ) -> None:
        self._rx_manager._fail_rx(reason, status, end_seq)

    def _on_transfer_pause_received(self, pkt: Packet) -> None:
        self._rx_manager._on_transfer_pause_received(pkt)

    def _is_duplicate_start(
        self,
        pkt: Packet,
        start_payload: FileStartPayload | FileStartPayloadLarge,
        extended: bool,
    ) -> bool:
        return self._rx_manager._is_duplicate_start(pkt, start_payload, extended)

    def _on_transfer_start_received(self, pkt: Packet) -> None:
        self._rx_manager._on_transfer_start_received(pkt)

    def _on_data_chunk_received(self, pkt: Packet) -> None:
        self._rx_manager._on_data_chunk_received(pkt)

    def _on_transfer_end_received(self, pkt: Packet) -> None:
        self._rx_manager._on_transfer_end_received(pkt)

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
                0.0 if unlimited else round(self._max_file_size_bytes / (1024 * 1024), 2)
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
        return self._state_coordinator.state

    @property
    def _state(self) -> TransferState:
        """Compatibility view of the state retained for existing integrations."""
        return self._state_coordinator.state

    @_state.setter
    def _state(self, new_state: TransferState) -> None:
        """Keeps legacy internal state assignment synchronized with the coordinator."""
        self._state_coordinator.transition(new_state)

    def _set_state(self, new_state: TransferState) -> None:
        old_state = self._state_coordinator.transition(new_state)
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

    def _compute_file_crc(self, file_path: Path) -> int:
        """Computes whole-file CRC-32 with a bounded-memory streaming read."""
        return IntegrityVerifier.file_crc32(file_path, CRC_STREAM_BUFFER_SIZE)

    # =========================================================================
    # RX session helpers
    # =========================================================================

    # =========================================================================
    # TX public flow
    # =========================================================================

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
