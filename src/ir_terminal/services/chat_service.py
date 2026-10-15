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
    message_truncated: ClassVar[Signal] = Signal(str, int)  # (reason, original_bytes)

    def __init__(
        self,
        serial_manager: SerialManager,
        storage_service: StorageService | None = None,
        local_name: str = "Local Station Alpha",
        remote_name: str = "Remote Station Bravo",
    ) -> None:
        super().__init__()
        self._serial_manager = serial_manager
        self._storage = storage_service or StorageService()
        self._seq: int = 0
        self._local_name = local_name
        self._remote_name = remote_name
        self._seen_rx_seq: set[int] = set()

        self._serial_manager.packet_received.connect(self.handle_packet)

    def send_message(self, text: str) -> None:
        """Encodes, transmits, and persists chat message over infrared beam."""
        clean_text = text.strip()
        if not clean_text:
            return

        encoded = clean_text.encode("utf-8")
        truncated = False
        original_len = len(encoded)
        if len(encoded) > MAX_PAYLOAD_SIZE:
            truncated = True
            # Safely truncate without splitting multi-byte UTF-8 character
            # sequences. Byte-identical to encoded[:MAX].decode(errors="ignore")
            # without the re-encode: if the first byte past the budget is a
            # continuation byte (10xxxxxx), the trailing char is split, so walk
            # back over its in-budget continuation bytes and drop the lead too.
            # Otherwise [:MAX] already ends on a character boundary.
            cut = MAX_PAYLOAD_SIZE
            if (encoded[cut] & 0xC0) == 0x80:
                while cut > 0 and (encoded[cut - 1] & 0xC0) == 0x80:
                    cut -= 1
                cut -= 1  # drop the split character's lead byte
            payload = encoded[:cut]
            clean_text = payload.decode("utf-8")
        else:
            payload = encoded

        self._seq = (self._seq + 1) % 256

        self._serial_manager.send_packet(
            seq=self._seq,
            opcode=Opcode.DATA_TEXT,
            payload=payload,
        )

        now_str = time.strftime("%Y-%m-%d %H:%M:%S")

        if truncated:
            self.message_truncated.emit(
                f"Message truncated {original_len}->{len(payload)}B (max {MAX_PAYLOAD_SIZE}B)",
                original_len,
            )
            logger.warning(
                "%s Chat message truncated %d->%dB (max %dB)",
                LogTag.CHAT_SVC,
                original_len,
                len(payload),
                MAX_PAYLOAD_SIZE,
            )

            # Save to durable hard drive JSON store
        self._storage.save_chat_message(
            sender=self._local_name,
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

            now_str = time.strftime("%Y-%m-%d %H:%M:%S")

            # Save to durable hard drive JSON store
            self._storage.save_chat_message(
                sender=self._remote_name,
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
