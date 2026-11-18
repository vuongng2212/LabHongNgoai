"""Open-loop paced file streaming for the WinForm C# compatible optical link.

Transmission follows the firmware contract in ``docs/WFF.txt``: the sender
announces a file with a HEADER frame, streams 256-byte DATA frames at a paced
interval and closes with an END frame. No acknowledgement, retry or checksum is
part of the wire protocol.

The receiver writes every DATA frame at its nominal offset
``sequence * PACKET_SIZE`` into a ``.part`` staging file, so a lost frame only
leaves a zero-filled gap instead of shifting the rest of the file. RAM usage
stays flat regardless of the transferred size because payloads go straight to
disk.
"""

from __future__ import annotations

import io
import logging
import math
import os
import re
import time
from enum import Enum
from pathlib import Path
from typing import BinaryIO, ClassVar

from PySide6.QtCore import QObject, QTimer, Signal

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import (
    DEFAULT_BAUDRATE,
    HEADER_OVERHEAD,
    MAX_CONFIGURABLE_FILE_SIZE_BYTES,
    MAX_FILE_SIZE_BYTES,
    PACED_DELAY_SAFETY_MS,
    PACKET_SIZE,
    RX_INACTIVITY_TIMEOUT_SEC,
    SEQ_END,
    SEQ_HEADER,
)
from ir_terminal.protocol.framer import pack_header_payload, unpack_header_payload
from ir_terminal.protocol.packet import WinFormPacket
from ir_terminal.services.storage_service import StorageService
from ir_terminal.utils.logger import LogTag
from ir_terminal.utils.path_helpers import get_transfers_dir

logger = logging.getLogger(__name__)

SETTING_MAX_FILE_SIZE_BYTES = "max_file_size_bytes"

SETTING_TRANSFERS_DIR = "transfers_dir"

SETTING_AUTO_SAVE = "auto_save"

_RX_FLUSH_INTERVAL: int = 16

_UNSAFE_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

_MAX_NAME_LENGTH: int = 200


class TransferState(Enum):
    """Lifecycle state of the open-loop file streaming engine."""

    IDLE = "IDLE"
    SENDING = "SENDING"
    RECEIVING = "RECEIVING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"

    @property
    def is_active(self) -> bool:
        """Returns True while a transmission or reception is in progress."""
        return self in (TransferState.SENDING, TransferState.RECEIVING)


def paced_delay_ms(packet_length: int, baudrate: int) -> int:
    """Returns the WinForm C# inter-frame delay for one wire frame.

    Args:
        packet_length: Total frame length in bytes, including the 8-byte header.
        baudrate: Optical data baudrate in bits per second.

    Returns:
        Delay in milliseconds: ``ceil(length * 10000 / baud) + 10``.

    Raises:
        ValueError: If ``baudrate`` is not positive or length is negative.
    """
    if baudrate <= 0:
        raise ValueError(f"Baudrate must be positive, got {baudrate}")
    if packet_length < 0:
        raise ValueError(f"Packet length must be non-negative, got {packet_length}")
    return math.ceil(packet_length * 10.0 * 1000 / baudrate) + PACED_DELAY_SAFETY_MS


def is_chat_envelope_name(file_name: str) -> bool:
    """Returns True if the announced file name matches the chat envelope pattern.

    Args:
        file_name: File name carried by the incoming HEADER frame.

    Returns:
        True if the name has the ``_chat_`` prefix and ``.txt`` extension.
    """
    clean = (file_name or "").strip()
    return clean.startswith("_chat_") and clean.endswith(".txt")


def sanitize_received_name(file_name: str) -> str:
    """Reduces an announced file name to a safe base name.

    Args:
        file_name: Name carried by the HEADER frame.

    Returns:
        Base name without separators or control characters.
    """
    normalized = (file_name or "").replace("\\", "/")
    base = normalized.rsplit("/", 1)[-1].strip()
    base = _UNSAFE_NAME_CHARS.sub("_", base)
    base = base.strip(" .")
    if not base:
        base = "received.bin"
    if len(base) > _MAX_NAME_LENGTH:
        base = base[-_MAX_NAME_LENGTH:]
    return base


def _unique_path(path: Path) -> Path:
    """Returns ``path`` or a numbered variant that does not exist yet."""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    index = 1
    while True:
        candidate = path.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate
        index += 1


class FileTransferService(QObject):
    """Streams and receives files over the optical channel without ARQ."""

    progress_updated: ClassVar[Signal] = Signal(int, float, float, int, int)

    transfer_completed: ClassVar[Signal] = Signal(str, int, float, str)

    virtual_transfer_completed: ClassVar[Signal] = Signal(str, bytes)

    transfer_failed: ClassVar[Signal] = Signal(str)

    state_changed: ClassVar[Signal] = Signal(str)

    def __init__(
        self,
        serial_manager: SerialManager,
        chunk_size: int = PACKET_SIZE,
        storage_service: StorageService | None = None,
        transfers_dir: Path | None = None,
    ) -> None:
        """Initializes the streaming engine.

        Args:
            serial_manager: Coordinator used to transmit framed packets.
            chunk_size: DATA payload size; the firmware contract uses 256 bytes.
            storage_service: Optional persistent storage repository.
            transfers_dir: Optional receive directory override.
        """
        super().__init__()
        if not 1 <= chunk_size <= PACKET_SIZE:
            raise ValueError(f"Chunk size must be within 1..{PACKET_SIZE}, got {chunk_size}")
        self._serial_manager = serial_manager
        self._chunk_size = int(chunk_size)
        self._storage = storage_service or StorageService()
        self._transfers_dir = transfers_dir or get_transfers_dir()
        self._default_transfers_dir = self._transfers_dir
        self._max_file_size_bytes = MAX_FILE_SIZE_BYTES
        self._auto_save = True
        self._state = TransferState.IDLE

        self._tx_file: BinaryIO | None = None
        self._tx_path: Path | None = None
        self._tx_name = ""
        self._tx_is_virtual = False
        self._tx_total_packets = 0
        self._tx_next_index = 0
        self._tx_size_bytes = 0
        self._tx_bytes_sent = 0
        self._tx_started_at = 0.0
        self._tx_timer = QTimer(self)
        self._tx_timer.setSingleShot(True)
        self._tx_timer.timeout.connect(self._send_next_chunk)

        self._rx_name = ""
        self._rx_is_chat = False
        self._rx_total_packets = 0
        self._rx_received: set[int] = set()
        self._rx_handle: BinaryIO | None = None
        self._rx_part_path: Path | None = None
        self._rx_highest_end = 0
        self._rx_write_count = 0
        self._rx_started_at = 0.0
        self._rx_timer = QTimer(self)
        self._rx_timer.setSingleShot(True)
        self._rx_timer.setInterval(int(RX_INACTIVITY_TIMEOUT_SEC * 1000))
        self._rx_timer.timeout.connect(self._on_rx_timeout)

        self._serial_manager.packet_received.connect(self._handle_packet)
        self.load_settings()

    @property
    def is_active(self) -> bool:
        """Returns True while a transmission or reception is in progress."""
        return self._state.is_active

    @property
    def state(self) -> TransferState:
        """Returns the current streaming engine state."""
        return self._state

    @property
    def chunk_size(self) -> int:
        """Returns the configured DATA payload size in bytes."""
        return self._chunk_size

    @property
    def max_file_size_bytes(self) -> int:
        """Returns the optional operator cap in bytes (0 means unlimited)."""
        return self._max_file_size_bytes

    @property
    def transfers_dir_path(self) -> Path:
        """Returns the directory where received files are finalized."""
        return self._transfers_dir

    @property
    def default_transfers_dir_path(self) -> Path:
        """Returns the immutable default receive directory."""
        return self._default_transfers_dir

    @property
    def auto_save(self) -> bool:
        """Returns True when incomplete receptions are kept on disk."""
        return self._auto_save

    @property
    def uses_extended_protocol(self) -> bool:
        """Returns False because the WinForm wire format has no large-file mode."""
        return False

    def get_config(self) -> dict[str, str | int | float | bool]:
        """Returns the current transfer configuration.

        Returns:
            JSON-serializable mapping with the size cap, receive directory and
            AutoSave flag.
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
            "auto_save": self._auto_save,
        }

    def load_settings(self) -> dict[str, str | int | float | bool]:
        """Loads persisted transfer settings, falling back to safe defaults.

        Returns:
            Current configuration mapping.
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

        raw_auto_save = self._storage.get_setting(SETTING_AUTO_SAVE, "1")
        self._auto_save = raw_auto_save.strip().lower() not in ("0", "false", "no", "off")

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
                    "%s Persisted transfers_dir unusable (%s: %s), keeping default: saved=%s",
                    LogTag.FILE_SVC,
                    type(exc).__name__,
                    exc,
                    raw_dir,
                )
        logger.info(
            "%s Transfer config loaded: max=%s dir=%s auto_save=%s paced_chunk=%dB",
            LogTag.FILE_SVC,
            "unlimited" if self._max_file_size_bytes == 0 else f"{self._max_file_size_bytes}B",
            self._transfers_dir,
            self._auto_save,
            self._chunk_size,
        )
        return self.get_config()

    def set_max_file_size_bytes(self, size_bytes: int) -> int:
        """Updates and persists the optional maximum accepted file size.

        Args:
            size_bytes: 0 for unlimited or a positive cap in bytes.

        Returns:
            Effective cap after validation.

        Raises:
            ValueError: If ``size_bytes`` is negative.
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
        """Updates and persists the receive directory.

        Args:
            dir_path: Target directory path, created when missing.

        Returns:
            Resolved absolute directory path.

        Raises:
            ValueError: If the path is empty, not a directory or not creatable.
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
        self._transfers_dir = candidate
        self._storage.save_setting(SETTING_TRANSFERS_DIR, str(candidate))
        logger.info("%s Transfer receive dir set: %s", LogTag.FILE_SVC, candidate)
        return candidate

    def set_auto_save(self, enabled: bool) -> bool:
        """Persists the AutoSave flag used when a reception does not complete.

        Args:
            enabled: True to keep partial files, False to discard them.

        Returns:
            Stored flag value.
        """
        self._auto_save = bool(enabled)
        self._storage.save_setting(SETTING_AUTO_SAVE, "1" if self._auto_save else "0")
        logger.info("%s AutoSave set: %s", LogTag.FILE_SVC, self._auto_save)
        return self._auto_save

    def update_config(
        self,
        max_file_size_bytes: int | None = None,
        transfers_dir: str | Path | None = None,
        auto_save: bool | None = None,
    ) -> dict[str, str | int | float | bool]:
        """Applies a partial configuration update and persists valid fields.

        Args:
            max_file_size_bytes: Optional new cap in bytes (0 means unlimited).
            transfers_dir: Optional new receive directory.
            auto_save: Optional AutoSave flag.

        Returns:
            Current configuration mapping after the update.
        """
        if max_file_size_bytes is not None:
            self.set_max_file_size_bytes(int(max_file_size_bytes))
        if transfers_dir is not None and str(transfers_dir).strip():
            self.set_transfers_dir(transfers_dir)
        if auto_save is not None:
            self.set_auto_save(bool(auto_save))
        return self.get_config()

    def start_transfer(self, file_path_str: str) -> None:
        """Starts an open-loop paced transmission of a local file.

        Args:
            file_path_str: Path of the file to stream.

        Raises:
            ValueError: If a transfer is active, the file is missing, or the
                file exceeds the configured size cap.
        """
        if self._state.is_active:
            raise ValueError(f"A transfer is already active: {self._state.name}")
        path = Path(file_path_str).expanduser()
        if not path.is_file():
            raise ValueError(f"File not found: {file_path_str}")
        try:
            size = path.stat().st_size
            handle = path.open("rb")
        except OSError as exc:
            raise ValueError(f"Cannot open file {file_path_str}: {exc}") from exc
        if self._max_file_size_bytes and size > self._max_file_size_bytes:
            handle.close()
            raise ValueError(
                f"File size {size}B exceeds configured cap {self._max_file_size_bytes}B"
            )
        self._tx_is_virtual = False
        self._tx_name = path.name
        self._tx_file = handle
        self._tx_path = path
        self._tx_size_bytes = size
        self._tx_total_packets = (size + self._chunk_size - 1) // self._chunk_size
        self._tx_next_index = 0
        self._tx_bytes_sent = 0
        self._tx_started_at = time.monotonic()
        self._set_state(TransferState.SENDING)
        self.progress_updated.emit(0, 0.0, 0.0, 0, self._tx_total_packets)

        header_payload = pack_header_payload(path.name, self._tx_total_packets)
        try:
            self._serial_manager.send_packet(SEQ_HEADER, header_payload)
        except Exception as exc:
            self._fail_tx(f"Could not send file header: {exc}")
            return
        logger.info(
            "%s TX start: file=%s size=%dB packets=%d chunk=%dB baud=%d header=%dB",
            LogTag.FILE_SVC,
            path.name,
            size,
            self._tx_total_packets,
            self._chunk_size,
            self._baudrate(),
            len(header_payload),
        )
        self._tx_timer.start(paced_delay_ms(HEADER_OVERHEAD + len(header_payload), self._baudrate()))

    def start_virtual_transfer(self, virtual_name: str, payload: bytes) -> bool:
        """Starts an open-loop paced transmission of an in-memory virtual payload.

        Args:
            virtual_name: Virtual file envelope name announced in the HEADER.
            payload: Raw bytes to stream.

        Returns:
            True if the transmission started successfully, False if busy or failed.
        """
        if self._state.is_active:
            logger.warning(
                "%s Virtual transfer rejected: transfer already active (%s)",
                LogTag.FILE_SVC,
                self._state.name,
            )
            return False
        clean_name = sanitize_received_name(virtual_name)
        size = len(payload)
        self._tx_is_virtual = True
        self._tx_name = clean_name
        self._tx_file = io.BytesIO(payload)
        self._tx_path = None
        self._tx_size_bytes = size
        self._tx_total_packets = (size + self._chunk_size - 1) // self._chunk_size if size else 0
        self._tx_next_index = 0
        self._tx_bytes_sent = 0
        self._tx_started_at = time.monotonic()
        self._set_state(TransferState.SENDING)

        header_payload = pack_header_payload(clean_name, self._tx_total_packets)
        try:
            self._serial_manager.send_packet(SEQ_HEADER, header_payload)
        except Exception as exc:
            self._fail_tx(f"Could not send virtual file header: {exc}")
            return False
        logger.info(
            "%s Virtual TX start: file=%s size=%dB packets=%d chunk=%dB baud=%d header=%dB",
            LogTag.FILE_SVC,
            clean_name,
            size,
            self._tx_total_packets,
            self._chunk_size,
            self._baudrate(),
            len(header_payload),
        )
        self._tx_timer.start(
            paced_delay_ms(HEADER_OVERHEAD + len(header_payload), self._baudrate())
        )
        return True

    def cancel_transfer(self) -> None:
        """Cancels the active transmission or reception immediately."""
        if self._state is TransferState.SENDING:
            self._fail_tx("Transfer cancelled by user", status="ABORTED", aborted=True)
            return
        if self._state is TransferState.RECEIVING:
            self._fail_rx("Incoming transfer cancelled by user", status="ABORTED", aborted=True)
            return
        logger.debug("%s Cancel ignored: state=%s", LogTag.FILE_SVC, self._state.name)

    def abort_transfer(self) -> None:
        """Backward-compatible alias of :meth:`cancel_transfer`."""
        self.cancel_transfer()

    def _baudrate(self) -> int:
        """Returns the active optical baudrate, falling back to the default."""
        value = getattr(self._serial_manager, "current_baud", DEFAULT_BAUDRATE)
        try:
            baud = int(value)
        except (TypeError, ValueError):
            return DEFAULT_BAUDRATE
        return baud if baud > 0 else DEFAULT_BAUDRATE

    def _set_state(self, new_state: TransferState) -> None:
        """Stores a new state and announces it to the UI."""
        previous = self._state
        self._state = new_state
        if previous is not new_state:
            logger.info(
                "%s Stream state %s -> %s",
                LogTag.FILE_SVC,
                previous.name,
                new_state.name,
            )
        self.state_changed.emit(new_state.name)

    def _send_next_chunk(self) -> None:
        """Sends the next DATA frame or the terminating END frame."""
        if self._state is not TransferState.SENDING:
            return
        if self._tx_file is None:
            self._fail_tx("Transmission file handle is closed")
            return
        if self._tx_next_index >= self._tx_total_packets:
            self._send_end()
            return
        sequence = self._tx_next_index
        try:
            self._tx_file.seek(sequence * self._chunk_size)
            payload = self._tx_file.read(self._chunk_size)
        except OSError as exc:
            self._fail_tx(f"File read failed at packet {sequence}: {exc}")
            return
        if not payload:
            self._fail_tx(f"File ended before packet {sequence}")
            return
        try:
            self._serial_manager.send_packet(sequence, payload)
        except Exception as exc:
            self._fail_tx(f"Could not send packet {sequence}: {exc}")
            return
        self._tx_next_index += 1
        self._tx_bytes_sent += len(payload)
        if not self._tx_is_virtual:
            self._emit_progress()
        if self._tx_next_index >= self._tx_total_packets:
            self._tx_timer.start(
                paced_delay_ms(HEADER_OVERHEAD + len(payload), self._baudrate())
            )
            return
        self._tx_timer.start(paced_delay_ms(HEADER_OVERHEAD + len(payload), self._baudrate()))

    def _send_end(self) -> None:
        """Sends the END frame, persists the run and reports completion."""
        try:
            self._serial_manager.send_packet(SEQ_END, b"")
        except Exception as exc:
            self._fail_tx(f"Could not send end packet: {exc}")
            return
        elapsed = max(time.monotonic() - self._tx_started_at, 0.0)
        filename = (
            self._tx_name
            if self._tx_is_virtual
            else (self._tx_path.name if self._tx_path else "unknown")
        )
        file_path = str(self._tx_path) if self._tx_path else ""
        size_bytes = self._tx_size_bytes
        speed_kbps = (self._tx_bytes_sent / 1024.0) / elapsed if elapsed > 0 else 0.0
        is_virtual = self._tx_is_virtual
        self._close_tx_file()
        self._set_state(TransferState.COMPLETED)
        if not is_virtual:
            self.progress_updated.emit(
                100, speed_kbps, 0.0, self._tx_total_packets, self._tx_total_packets
            )
            self._persist_record(
                filename=filename,
                file_path=file_path,
                direction="TX",
                size_bytes=size_bytes,
                speed_kbps=speed_kbps,
                duration_sec=elapsed,
                status="SUCCESS",
            )
            logger.info(
                "%s TX complete: file=%s sent=%dB/%dB packets=%d elapsed=%.2fs speed=%.1fKB/s",
                LogTag.FILE_SVC,
                filename,
                self._tx_bytes_sent,
                size_bytes,
                self._tx_total_packets,
                elapsed,
                speed_kbps,
            )
            self.transfer_completed.emit(filename, size_bytes, elapsed, "")
        else:
            logger.info(
                "%s Virtual TX complete: name=%s sent=%dB packets=%d elapsed=%.2fs",
                LogTag.FILE_SVC,
                filename,
                self._tx_bytes_sent,
                self._tx_total_packets,
                elapsed,
            )

    def _emit_progress(self) -> None:
        """Emits the current transmission progress snapshot."""
        total = self._tx_total_packets
        elapsed = max(time.monotonic() - self._tx_started_at, 0.0)
        pct = int(self._tx_next_index * 100 / total) if total else 0
        speed_kbps = (self._tx_bytes_sent / 1024.0) / elapsed if elapsed > 0 else 0.0
        remaining_bytes = max(self._tx_size_bytes - self._tx_bytes_sent, 0)
        eta_sec = remaining_bytes / (self._baudrate() / 10.0)
        self.progress_updated.emit(
            min(pct, 100), speed_kbps, eta_sec, self._tx_next_index, total
        )

    def _close_tx_file(self) -> None:
        """Closes the transmission file handle if it is open."""
        handle = self._tx_file
        self._tx_file = None
        self._tx_is_virtual = False
        self._tx_name = ""
        if handle is not None:
            try:
                handle.close()
            except OSError as exc:
                logger.warning("%s TX file close warning: %s", LogTag.FILE_SVC, exc)

    def _fail_tx(self, reason: str, status: str = "FAILED", aborted: bool = False) -> None:
        """Aborts a transmission, releases the file and notifies listeners."""
        self._tx_timer.stop()
        filename = (
            self._tx_name
            if self._tx_is_virtual
            else (self._tx_path.name if self._tx_path else "unknown")
        )
        file_path = str(self._tx_path) if self._tx_path else ""
        elapsed = (
            max(time.monotonic() - self._tx_started_at, 0.0) if self._tx_started_at else 0.0
        )
        speed_kbps = (self._tx_bytes_sent / 1024.0) / elapsed if elapsed > 0 else 0.0
        is_virtual = self._tx_is_virtual
        self._close_tx_file()
        self._set_state(TransferState.ABORTED if aborted else TransferState.FAILED)
        if not is_virtual:
            self._persist_record(
                filename=filename,
                file_path=file_path,
                direction="TX",
                size_bytes=self._tx_size_bytes,
                speed_kbps=speed_kbps,
                duration_sec=elapsed,
                status=status,
            )
            self.transfer_failed.emit(reason)
        logger.warning(
            "%s TX failed: file=%s sent=%dB/%dB packets=%d/%d reason=%s",
            LogTag.FILE_SVC,
            filename,
            self._tx_bytes_sent,
            self._tx_size_bytes,
            self._tx_next_index,
            self._tx_total_packets,
            reason,
        )

    def _handle_packet(self, pkt: object) -> None:
        """Routes one decoded WinForm frame to the RX engine.

        Args:
            pkt: Frame emitted by the serial worker.
        """
        if not isinstance(pkt, WinFormPacket):
            logger.debug(
                "%s Ignored non-WinFormPacket: type=%s", LogTag.FILE_SVC, type(pkt).__name__
            )
            return
        if pkt.is_header:
            self._begin_receive(pkt)
            return
        if pkt.is_end:
            if self._rx_is_chat:
                self._finish_virtual_receive()
            else:
                self._finish_receive()
            return
        self._write_chunk(pkt)

    def _begin_receive(self, pkt: WinFormPacket) -> None:
        """Opens a staging buffer or file for an incoming transfer."""
        try:
            file_name, total_packets = unpack_header_payload(pkt.data)
        except ValueError as exc:
            logger.warning("%s RX invalid HEADER dropped: %s", LogTag.FILE_SVC, exc)
            return
        if self._state is TransferState.RECEIVING:
            logger.warning(
                "%s RX restart: previous transfer '%s' replaced", LogTag.FILE_SVC, self._rx_name
            )
            self._discard_rx(keep_partial=False)
        self._rx_is_chat = is_chat_envelope_name(file_name)
        safe_name = file_name.strip() if self._rx_is_chat else sanitize_received_name(file_name)
        self._rx_name = safe_name
        self._rx_total_packets = total_packets
        self._rx_received = set()
        self._rx_highest_end = 0
        self._rx_write_count = 0
        self._rx_started_at = time.monotonic()
        if self._rx_is_chat:
            self._rx_handle = io.BytesIO()
            self._rx_part_path = None
        else:
            self._transfers_dir.mkdir(parents=True, exist_ok=True)
            part_path = self._transfers_dir / f".ir_rx_{safe_name}.part"
            try:
                self._rx_handle = part_path.open("w+b")
            except OSError as exc:
                logger.warning(
                    "%s RX cannot open staging file %s: %s", LogTag.FILE_SVC, part_path, exc
                )
                self._rx_handle = None
                return
            self._rx_part_path = part_path
        self._set_state(TransferState.RECEIVING)
        self._rx_timer.start()
        if not self._rx_is_chat:
            self.progress_updated.emit(0, 0.0, 0.0, 0, total_packets)
            logger.info(
                "%s RX start: file=%s packets=%d staging=%s",
                LogTag.FILE_SVC,
                safe_name,
                total_packets,
                self._rx_part_path,
            )
        else:
            logger.info(
                "%s Chat envelope RX start: file=%s packets=%d (in-memory)",
                LogTag.FILE_SVC,
                safe_name,
                total_packets,
            )

    def _write_chunk(self, pkt: WinFormPacket) -> None:
        """Writes one DATA frame at its nominal offset in the staging buffer."""
        if self._state is not TransferState.RECEIVING or self._rx_handle is None:
            logger.debug(
                "%s RX chunk ignored without active HEADER: seq=0x%08X",
                LogTag.FILE_SVC,
                pkt.sequence,
            )
            return
        sequence = pkt.sequence
        if sequence >= self._rx_total_packets:
            logger.warning(
                "%s RX chunk out of range: seq=%d total=%d",
                LogTag.FILE_SVC,
                sequence,
                self._rx_total_packets,
            )
            return
        offset = sequence * self._chunk_size
        try:
            self._rx_handle.seek(offset)
            self._rx_handle.write(pkt.data)
        except OSError as exc:
            self._fail_rx(f"Could not write packet {sequence}: {exc}")
            return
        self._rx_received.add(sequence)
        self._rx_highest_end = max(self._rx_highest_end, offset + len(pkt.data))
        self._rx_write_count += 1
        if not self._rx_is_chat and self._rx_write_count % _RX_FLUSH_INTERVAL == 0:
            try:
                self._rx_handle.flush()
            except OSError as exc:
                logger.warning("%s RX flush warning: %s", LogTag.FILE_SVC, exc)
        self._rx_timer.start()
        if not self._rx_is_chat:
            total = self._rx_total_packets
            received = len(self._rx_received)
            pct = int(received * 100 / total) if total else 0
            elapsed = max(time.monotonic() - self._rx_started_at, 0.0)
            speed_kbps = (self._rx_highest_end / 1024.0) / elapsed if elapsed > 0 else 0.0
            remaining = max(total - received, 0)
            eta_sec = remaining * self._chunk_size / (self._baudrate() / 10.0)
            self.progress_updated.emit(min(pct, 100), speed_kbps, eta_sec, received, total)

    def _finish_virtual_receive(self) -> None:
        """Finalizes an in-memory virtual envelope reception."""
        if self._state is not TransferState.RECEIVING:
            return
        self._rx_timer.stop()
        payload_bytes = b""
        if isinstance(self._rx_handle, io.BytesIO):
            self._rx_handle.seek(0)
            payload_bytes = self._rx_handle.getvalue()[: self._rx_highest_end]
            self._rx_handle.close()
        self._rx_handle = None
        file_name = self._rx_name
        self._rx_is_chat = False
        self._rx_name = ""
        self._set_state(TransferState.COMPLETED)
        logger.info(
            "%s Virtual RX complete: name=%s len=%dB",
            LogTag.FILE_SVC,
            file_name,
            len(payload_bytes),
        )
        self.virtual_transfer_completed.emit(file_name, payload_bytes)

    def _finish_receive(self) -> None:
        """Finalizes a received file after the END frame arrives."""
        if self._state is not TransferState.RECEIVING:
            logger.debug("%s RX end ignored: state=%s", LogTag.FILE_SVC, self._state.name)
            return
        self._rx_timer.stop()
        received = len(self._rx_received)
        total = self._rx_total_packets
        missing = max(total - received, 0)
        elapsed = max(time.monotonic() - self._rx_started_at, 0.0)
        part_path = self._rx_part_path
        name = self._rx_name or "received.bin"
        destination = _unique_path(self._transfers_dir / name)
        try:
            if self._rx_handle is not None:
                self._rx_handle.truncate(self._rx_highest_end)
                self._rx_handle.flush()
                self._rx_handle.close()
            self._rx_handle = None
            if part_path is None:
                raise OSError("staging file path missing")
            os.replace(part_path, destination)
        except OSError as exc:
            self._fail_rx(f"Could not finalize received file: {exc}")
            return
        size_bytes = self._rx_highest_end
        speed_kbps = (size_bytes / 1024.0) / elapsed if elapsed > 0 else 0.0
        self._rx_part_path = None
        if missing:
            self._set_state(TransferState.FAILED)
            self._persist_record(
                filename=destination.name,
                file_path=str(destination),
                direction="RX",
                size_bytes=size_bytes,
                speed_kbps=speed_kbps,
                duration_sec=elapsed,
                status="PARTIAL",
            )
            reason = (
                f"Received {received}/{total} packets; {missing} lost to optical loss. "
                f"Saved partial file: {destination}"
            )
            logger.warning("%s RX partial: %s", LogTag.FILE_SVC, reason)
            self.transfer_failed.emit(reason)
            return
        self._set_state(TransferState.COMPLETED)
        self.progress_updated.emit(100, speed_kbps, 0.0, total, total)
        self._persist_record(
            filename=destination.name,
            file_path=str(destination),
            direction="RX",
            size_bytes=size_bytes,
            speed_kbps=speed_kbps,
            duration_sec=elapsed,
            status="SUCCESS",
        )
        logger.info(
            "%s RX complete: file=%s size=%dB packets=%d elapsed=%.2fs speed=%.1fKB/s",
            LogTag.FILE_SVC,
            destination.name,
            size_bytes,
            total,
            elapsed,
            speed_kbps,
        )
        self.transfer_completed.emit(destination.name, size_bytes, elapsed, "")

    def _on_rx_timeout(self) -> None:
        """Applies the inactivity watchdog when END never arrives."""
        if self._state is not TransferState.RECEIVING:
            return
        received = len(self._rx_received)
        reason = (
            f"Receive timeout after {RX_INACTIVITY_TIMEOUT_SEC:.1f}s without data "
            f"({received}/{self._rx_total_packets} packets)"
        )
        self._fail_rx(reason, status="FAILED", aborted=False)

    def _fail_rx(self, reason: str, status: str = "FAILED", aborted: bool = False) -> None:
        """Aborts a reception, optionally keeping the partial file."""
        self._rx_timer.stop()
        if self._rx_is_chat:
            self._discard_rx(keep_partial=False)
            self._set_state(TransferState.ABORTED if aborted else TransferState.FAILED)
            logger.warning("%s Chat envelope RX failed: %s", LogTag.FILE_SVC, reason)
            return
        name = self._rx_name or "received.bin"
        elapsed = (
            max(time.monotonic() - self._rx_started_at, 0.0) if self._rx_started_at else 0.0
        )
        saved_path: Path | None = None
        size_bytes = self._rx_highest_end
        if self._auto_save and size_bytes > 0 and self._rx_handle is not None:
            destination = _unique_path(self._transfers_dir / f"{name}.partial")
            try:
                self._rx_handle.truncate(self._rx_highest_end)
                self._rx_handle.flush()
                self._rx_handle.close()
                self._rx_handle = None
                if self._rx_part_path is not None:
                    os.replace(self._rx_part_path, destination)
                    saved_path = destination
                self._rx_part_path = None
            except OSError as exc:
                logger.warning("%s RX partial save failed: %s", LogTag.FILE_SVC, exc)
        self._discard_rx(keep_partial=False)
        self._set_state(TransferState.ABORTED if aborted else TransferState.FAILED)
        speed_kbps = (size_bytes / 1024.0) / elapsed if elapsed > 0 else 0.0
        self._persist_record(
            filename=saved_path.name if saved_path else name,
            file_path=str(saved_path) if saved_path else "",
            direction="RX",
            size_bytes=size_bytes,
            speed_kbps=speed_kbps,
            duration_sec=elapsed,
            status=status,
        )
        if saved_path is not None:
            reason = f"{reason}. Partial data kept at {saved_path}"
        logger.warning("%s RX failed: %s", LogTag.FILE_SVC, reason)
        self.transfer_failed.emit(reason)

    def _discard_rx(self, keep_partial: bool) -> None:
        """Closes and removes the staging file unless it must be kept."""
        handle = self._rx_handle
        self._rx_handle = None
        self._rx_is_chat = False
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
        part_path = self._rx_part_path
        self._rx_part_path = None
        if keep_partial or part_path is None:
            return
        try:
            part_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("%s RX staging cleanup failed: %s", LogTag.FILE_SVC, exc)

    def _persist_record(
        self,
        filename: str,
        file_path: str,
        direction: str,
        size_bytes: int,
        speed_kbps: float,
        duration_sec: float,
        status: str,
    ) -> None:
        """Persists one transfer audit record with a flat-memory write."""
        try:
            record_id = self._storage.save_file_transfer(
                filename=filename,
                file_path=file_path,
                direction=direction,
                size_bytes=size_bytes,
                crc32_hex="",
                speed_kbps=round(speed_kbps, 2),
                duration_sec=round(duration_sec, 2),
                status=status,
            )
            logger.debug(
                "%s Persisted transfer record #%d: %s %s status=%s",
                LogTag.STORAGE,
                record_id,
                direction,
                filename,
                status,
            )
        except Exception as exc:
            logger.warning(
                "%s Could not persist transfer record (%s: %s) file=%s status=%s",
                LogTag.FILE_SVC,
                type(exc).__name__,
                exc,
                filename,
                status,
            )
