"""Optical chat service utilizing virtual file envelopes over the WinForm wire protocol.

The WinForm C# firmware contract only understands file transfers (HEADER, DATA, END).
To enable real-time messaging without modifying the 8-byte wire overhead or STM32
forwarding logic, outgoing text messages are packed in-memory into a virtual file
envelope named ``_chat_<timestamp>_<station>.txt``.

Incoming envelopes with the ``_chat_`` prefix are demultiplexed in RAM by the
file transfer service and delivered directly to this service without touching disk.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import ClassVar

from PySide6.QtCore import QObject, Signal

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.services.file_transfer_service import FileTransferService, is_chat_envelope_name
from ir_terminal.services.storage_service import StorageService
from ir_terminal.utils.logger import LogTag

logger = logging.getLogger(__name__)


class ChatService(QObject):
    """Chat service streaming messages through virtual file envelopes."""

    message_received: ClassVar[Signal] = Signal(str, str, bool)

    frame_inspected: ClassVar[Signal] = Signal(str, int, int, int)

    message_truncated: ClassVar[Signal] = Signal(str, int)

    def __init__(
        self,
        serial_manager: SerialManager,
        storage_service: StorageService | None = None,
        local_name: str = "Local Station Alpha",
        remote_name: str = "Remote Station Bravo",
        file_transfer_service: FileTransferService | None = None,
    ) -> None:
        """Initializes the chat service wired through virtual file envelopes.

        Args:
            serial_manager: Active serial coordinator.
            storage_service: Optional persistent storage repository.
            local_name: Local station label used for outgoing metadata and history.
            remote_name: Remote station label used for incoming history.
            file_transfer_service: Optional FileTransferService instance.
        """
        super().__init__()
        self._serial_manager = serial_manager
        self._storage = storage_service or StorageService()
        self._local_name = local_name
        self._station_name = local_name
        self._remote_name = remote_name
        self._file_service: FileTransferService | None = None
        self._seq = 0
        if file_transfer_service is not None:
            self.set_file_transfer_service(file_transfer_service)
        logger.info(
            "%s Chat service active (virtual file envelopes): local=%s remote=%s",
            LogTag.CHAT_SVC,
            self._local_name,
            self._remote_name,
        )

    @property
    def file_transfer_service(self) -> FileTransferService | None:
        """Returns the configured FileTransferService instance."""
        return self._file_service

    def set_file_transfer_service(self, file_service: FileTransferService) -> None:
        """Attaches the file transfer service and connects completion signals.

        Args:
            file_service: Active FileTransferService used for virtual streaming.
        """
        if self._file_service is file_service:
            return
        if self._file_service is not None:
            try:
                self._file_service.virtual_transfer_completed.disconnect(
                    self._on_virtual_transfer_completed
                )
            except (RuntimeError, TypeError):
                pass
        self._file_service = file_service
        self._file_service.virtual_transfer_completed.connect(
            self._on_virtual_transfer_completed
        )

    def send_message(self, text: str) -> bool:
        """Encapsulates and transmits a text message via a virtual file envelope.

        Args:
            text: Message text supplied by the UI.

        Returns:
            True if the transfer was initiated; False if channel is busy or invalid.
        """
        clean_text = (text or "").strip()
        if not clean_text:
            logger.debug("%s Chat send rejected: empty message", LogTag.CHAT_SVC)
            return False
        if self._file_service is None or self._file_service.is_active:
            logger.warning(
                "%s Chat send rejected: optical channel busy or service missing",
                LogTag.CHAT_SVC,
            )
            return False
        timestamp_envelope = datetime.now().strftime("%Y%m%d_%H%M%S")
        station_slug = re.sub(r"[^A-Za-z0-9_-]", "_", self._station_name).strip("_") or "Station"
        envelope_name = f"_chat_{timestamp_envelope}_{station_slug}.txt"
        payload = text.encode("utf-8")
        if not self._file_service.start_virtual_transfer(envelope_name, payload):
            return False
        timestamp_str = datetime.now().strftime("%H:%M:%S")
        self._seq += 1
        self._storage.save_chat_message(
            sender=self._local_name,
            text=text,
            timestamp=timestamp_str,
            seq=self._seq,
            is_outgoing=True,
        )
        self.message_received.emit(text, timestamp_str, True)
        logger.info(
            "%s Chat sent: len=%dB envelope=%s seq=%d",
            LogTag.CHAT_SVC,
            len(payload),
            envelope_name,
            self._seq,
        )
        return True

    def _on_virtual_transfer_completed(self, virtual_name: str, payload_bytes: bytes) -> None:
        """Handles completed reception of an in-memory virtual envelope.

        Args:
            virtual_name: Envelope name carrying metadata.
            payload_bytes: In-memory byte payload.
        """
        if not is_chat_envelope_name(virtual_name):
            return
        text = payload_bytes.decode("utf-8", errors="replace")
        timestamp_str = datetime.now().strftime("%H:%M:%S")
        sender = self._extract_sender(virtual_name)
        self._seq += 1
        self._storage.save_chat_message(
            sender=sender,
            text=text,
            timestamp=timestamp_str,
            seq=self._seq,
            is_outgoing=False,
        )
        self.message_received.emit(text, timestamp_str, False)
        logger.info(
            "%s Chat received from %s: len=%dB seq=%d",
            LogTag.CHAT_SVC,
            sender,
            len(payload_bytes),
            self._seq,
        )

    def _extract_sender(self, virtual_name: str) -> str:
        """Extracts the sender station slug from a chat envelope name.

        Args:
            virtual_name: Declared envelope name.

        Returns:
            Extracted sender station name or default remote station name.
        """
        clean = virtual_name.strip()
        if clean.endswith(".txt"):
            clean = clean[:-4]
        parts = clean.split("_")
        if len(parts) >= 5 and parts[4]:
            return "_".join(parts[4:])
        return self._remote_name

    def handle_packet(self, pkt: object) -> None:
        """Ignores direct raw frames; chat reception is routed through FileTransferService.

        Args:
            pkt: Frame emitted by the serial worker.
        """
        logger.debug("%s Direct chat frame ignored: type=%s", LogTag.CHAT_SVC, type(pkt).__name__)
