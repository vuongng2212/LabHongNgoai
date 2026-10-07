"""Optical Chat Service managing realtime text messages, frame inspection, and storage."""

from __future__ import annotations

import logging
import time
from typing import ClassVar

from PySide6.QtCore import QObject, Signal

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import MAX_PAYLOAD_SIZE, Opcode
from ir_terminal.protocol.packet import Packet
from ir_terminal.services.storage_service import StorageService
from ir_terminal.utils.logger import LogTag

logger = logging.getLogger(__name__)


class ChatService(QObject):
    """Handles optical realtime messaging, frame inspection, and persistent storage."""

    message_received: ClassVar[Signal] = Signal(str, str, bool)  # (text, timestamp, is_outgoing)
    frame_inspected: ClassVar[Signal] = Signal(
        str, int, int, int
    )  # (hex_dump, seq, opcode, length)

    def __init__(
        self,
        serial_manager: SerialManager,
        storage_service: StorageService | None = None,
    ) -> None:
        super().__init__()
        self._serial_manager = serial_manager
        self._storage = storage_service or StorageService()
        self._seq: int = 0

        self._serial_manager.packet_received.connect(self.handle_packet)

    def send_message(self, text: str) -> None:
        """Encodes, transmits, and persists chat message over infrared beam."""
        clean_text = text.strip()
        if not clean_text:
            return

        encoded = clean_text.encode("utf-8")
        if len(encoded) > MAX_PAYLOAD_SIZE:
            # Safely truncate without splitting multi-byte UTF-8 character sequences
            clean_text = encoded[:MAX_PAYLOAD_SIZE].decode("utf-8", errors="ignore")
            payload = clean_text.encode("utf-8")
        else:
            payload = encoded

        self._seq = (self._seq + 1) % 256

        self._serial_manager.send_packet(
            seq=self._seq,
            opcode=Opcode.DATA_TEXT,
            payload=payload,
        )

        now_str = time.strftime("%H:%M:%S")

        # Save to durable hard drive SQLite DB
        self._storage.save_chat_message(
            sender="Local Station Alpha",
            text=clean_text,
            timestamp=now_str,
            seq=self._seq,
            is_outgoing=True,
        )

        self.message_received.emit(clean_text, now_str, True)
        self.frame_inspected.emit(
            payload.hex(" ").upper(), self._seq, Opcode.DATA_TEXT, len(payload)
        )
        logger.info(
            "%s Sent chat: '%s' (Seq #%d, %d bytes)",
            LogTag.CHAT_SVC,
            clean_text,
            self._seq,
            len(payload),
        )

    def handle_packet(self, pkt: object) -> None:
        """Processes incoming packet from serial transport and saves to disk."""
        if not isinstance(pkt, Packet):
            return

        if pkt.opcode == Opcode.DATA_TEXT:
            try:
                text = pkt.payload.decode("utf-8")
            except UnicodeDecodeError:
                text = pkt.payload.hex(" ")

            now_str = time.strftime("%H:%M:%S")

            # Save to durable hard drive SQLite DB
            self._storage.save_chat_message(
                sender="Remote Station Bravo",
                text=text,
                timestamp=now_str,
                seq=pkt.seq,
                is_outgoing=False,
            )

            self.message_received.emit(text, now_str, False)
            self.frame_inspected.emit(
                pkt.payload.hex(" ").upper(), pkt.seq, pkt.opcode, len(pkt.payload)
            )
            logger.info(
                "%s Received optical chat: '%s' (Seq #%d, %d bytes)",
                LogTag.CHAT_SVC,
                text,
                pkt.seq,
                len(pkt.payload),
            )
