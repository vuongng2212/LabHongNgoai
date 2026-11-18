"""Frozen optical chat service retained for UI compatibility.

The WinForm C# firmware contract has no text-message opcode: every frame is a
file HEADER, DATA chunk or END marker. The chat surface is therefore disabled;
the class keeps its signals so existing UI wiring can stay connected without
emitting misleading traffic.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from PySide6.QtCore import QObject, Signal

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.services.storage_service import StorageService
from ir_terminal.utils.logger import LogTag

logger = logging.getLogger(__name__)

CHAT_DISABLED_REASON = (
    "Chat is disabled: the WinForm firmware protocol only carries file transfers"
)


class ChatService(QObject):
    """Disabled chat surface that keeps the legacy signal contract."""

    message_received: ClassVar[Signal] = Signal(str, str, bool)

    frame_inspected: ClassVar[Signal] = Signal(str, int, int, int)

    message_truncated: ClassVar[Signal] = Signal(str, int)

    def __init__(
        self,
        serial_manager: SerialManager,
        storage_service: StorageService | None = None,
        local_name: str = "Local Station Alpha",
        remote_name: str = "Remote Station Bravo",
    ) -> None:
        """Initializes the frozen chat surface.

        Args:
            serial_manager: Active serial coordinator (kept for compatibility).
            storage_service: Optional storage repository (kept for compatibility).
            local_name: Local station label retained for stored history.
            remote_name: Remote station label retained for stored history.
        """
        super().__init__()
        self._serial_manager = serial_manager
        self._storage = storage_service or StorageService()
        self._local_name = local_name
        self._remote_name = remote_name
        self._seq = 0
        logger.info("%s Chat service frozen: %s", LogTag.CHAT_SVC, CHAT_DISABLED_REASON)

    def send_message(self, text: str) -> None:
        """Rejects every outgoing chat message.

        Args:
            text: Message text supplied by the UI.

        Raises:
            RuntimeError: Always, because the wire protocol has no chat opcode.
        """
        logger.warning(
            "%s Chat send rejected: len=%dB preview=%r",
            LogTag.CHAT_SVC,
            len((text or "").encode("utf-8")),
            (text or "")[:80],
        )
        raise RuntimeError(CHAT_DISABLED_REASON)

    def handle_packet(self, pkt: object) -> None:
        """Ignores incoming frames because chat is not part of the protocol.

        Args:
            pkt: Frame emitted by the serial worker.
        """
        logger.debug("%s Chat frame ignored: type=%s", LogTag.CHAT_SVC, type(pkt).__name__)
