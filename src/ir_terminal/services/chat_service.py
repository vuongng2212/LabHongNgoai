"""Optical Chat Service managing realtime text messages and frame inspection."""

from __future__ import annotations

import logging
import time
from typing import ClassVar

from PySide6.QtCore import QObject, Signal

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import Opcode
from ir_terminal.protocol.packet import Packet

logger = logging.getLogger(__name__)


class ChatService(QObject):
    """Handles optical realtime messaging and frame inspection."""

    message_received: ClassVar[Signal] = Signal(str, str, bool)  # (text, timestamp, is_outgoing)
    frame_inspected: ClassVar[Signal] = Signal(
        str, int, int, int
    )  # (hex_dump, seq, opcode, length)

    def __init__(self, serial_manager: SerialManager) -> None:
        super().__init__()
        self._serial_manager = serial_manager
        self._seq: int = 0

        self._serial_manager.packet_received.connect(self.handle_packet)

    def send_message(self, text: str) -> None:
        """Encodes and transmits chat message over infrared beam."""
        clean_text = text.strip()
        if not clean_text:
            return

        payload = clean_text.encode("utf-8")
        self._seq = (self._seq + 1) % 256

        self._serial_manager.send_packet(
            seq=self._seq,
            opcode=Opcode.DATA_TEXT,
            payload=payload,
        )

        now_str = time.strftime("%H:%M:%S")
        self.message_received.emit(clean_text, now_str, True)
        self.frame_inspected.emit(
            payload.hex(" ").upper(), self._seq, Opcode.DATA_TEXT, len(payload)
        )
        logger.info("Sent chat message: %s (Seq #%d)", clean_text, self._seq)

    def handle_packet(self, pkt: object) -> None:
        """Processes incoming packet from serial transport."""
        if not isinstance(pkt, Packet):
            return

        if pkt.opcode == Opcode.DATA_TEXT:
            try:
                text = pkt.payload.decode("utf-8")
            except UnicodeDecodeError:
                text = pkt.payload.hex(" ")

            now_str = time.strftime("%H:%M:%S")
            self.message_received.emit(text, now_str, False)
            self.frame_inspected.emit(
                pkt.payload.hex(" ").upper(), pkt.seq, pkt.opcode, len(pkt.payload)
            )
            logger.info("Received optical chat: %s (Seq #%d)", text, pkt.seq)
