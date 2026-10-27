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
from ir_terminal.utils.logger import LogTag, format_frame_summary, format_hex_preview, opcode_name

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
            logger.debug("%s Chat send skipped: empty/whitespace message", LogTag.CHAT_SVC)
            return

        encoded = clean_text.encode("utf-8")
        truncated = False
        original_len = len(encoded)
        if len(encoded) > MAX_PAYLOAD_SIZE:
            truncated = True
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

        try:
            self._serial_manager.send_packet(
                seq=self._seq,
                opcode=Opcode.DATA_TEXT,
                payload=payload,
            )
        except Exception:
            logger.exception(
                "%s Chat TX FAILED %s text=%r hex=%s",
                LogTag.CHAT_SVC,
                format_frame_summary(self._seq, Opcode.DATA_TEXT, len(payload)),
                clean_text[:80],
                format_hex_preview(payload, max_bytes=16),
            )
            raise

        now_str = time.strftime("%Y-%m-%d %H:%M:%S")

        if truncated:
            self.message_truncated.emit(
                f"Message truncated {original_len}->{len(payload)}B (max {MAX_PAYLOAD_SIZE}B)",
                original_len,
            )
            logger.warning(
                "%s Chat message truncated %d->%dB (max %dB) seq=%d preview=%r",
                LogTag.CHAT_SVC,
                original_len,
                len(payload),
                MAX_PAYLOAD_SIZE,
                self._seq,
                clean_text[:80],
            )

        msg_id = self._storage.save_chat_message(
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
            "%s Chat TX %s (%s) sender=%s stored_id=%d text=%r hex=%s",
            LogTag.CHAT_SVC,
            format_frame_summary(self._seq, Opcode.DATA_TEXT, len(payload)),
            opcode_name(Opcode.DATA_TEXT),
            self._local_name,
            msg_id,
            clean_text[:120],
            format_hex_preview(payload, max_bytes=16),
        )

    def handle_packet(self, pkt: object) -> None:
        """Processes incoming packet from serial transport and saves to disk."""
        if not isinstance(pkt, Packet):
            logger.debug(
                "%s Chat ignored non-Packet: type=%s",
                LogTag.CHAT_SVC,
                type(pkt).__name__,
            )
            return

        if pkt.opcode == Opcode.DATA_TEXT:
            try:
                text = pkt.payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                logger.warning(
                    "%s Chat RX undecodable UTF-8 %s (%s: %s) hex=%s — showing hex",
                    LogTag.CHAT_SVC,
                    format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload), pkt.crc32),
                    type(exc).__name__,
                    exc,
                    format_hex_preview(pkt.payload, max_bytes=16),
                )
                text = pkt.payload.hex(" ")

            now_str = time.strftime("%Y-%m-%d %H:%M:%S")

            msg_id = self._storage.save_chat_message(
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
                "%s Chat RX %s crc=0x%08X sender=%s stored_id=%d text=%r hex=%s",
                LogTag.CHAT_SVC,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                pkt.crc32 & 0xFFFFFFFF,
                self._remote_name,
                msg_id,
                text[:120],
                format_hex_preview(pkt.payload, max_bytes=16),
            )
        else:
            logger.debug(
                "%s Chat ignored opcode %s %s",
                LogTag.CHAT_SVC,
                opcode_name(pkt.opcode),
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload), pkt.crc32),
            )
