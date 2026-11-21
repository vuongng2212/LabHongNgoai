"""Dịch vụ nhắn tin quang học thời gian thực qua cơ chế bao thư file ảo (Virtual File Chat Envelope).

Tin nhắn chat được đóng gói giả vờ như một file văn bản mang tên _chat_..., truyền đi y hệt như gửi file thật để vi điều khiển STM32 không cần phải sửa code firmware mà vẫn hiểu:
1. Giao thức phần cứng chuẩn WinForm C# và STM32 vốn chỉ hỗ trợ các gói truyền file (HEADER, DATA, END).
2. Khi người dùng gửi tin nhắn, hệ thống đóng gói nội dung vào bộ nhớ RAM thành file ảo:
    _chat_<YYYYMMDD_HHMMSS>_<TênTrạm>.txt
3. Phía nhận phát hiện tên file bắt đầu bằng '_chat_' sẽ xử lý trực tiếp trong RAM và hiển thị lên khung chat ngay lập tức (< 100 ms) mà không cần ghi file tạm xuống ổ cứng.
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
    """Dịch vụ nhắn tin qua tia sáng hồng ngoại sử dụng cơ chế bao thư file ảo."""

    message_received: ClassVar[Signal] = Signal(str, str, bool)

    def __init__(
        self,
        serial_manager: SerialManager,
        storage_service: StorageService | None = None,
        local_name: str = "Local Station Alpha",
        remote_name: str = "Remote Station Bravo",
        file_transfer_service: FileTransferService | None = None,
    ) -> None:
        """Khởi tạo dịch vụ nhắn tin qua tia quang.

        Args:
            serial_manager: Bộ não điều phối kết nối cổng COM.
            storage_service: Dịch vụ lưu trữ lịch sử tin nhắn vào ổ cứng.
            local_name: Tên hiển thị của trạm cục bộ (ví dụ: 'Trạm A').
            remote_name: Tên hiển thị của trạm đối tác (ví dụ: 'Trạm B').
            file_transfer_service: Động cơ truyền file dùng để bơm gói tin ảo.
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
        """Trả về đối tượng FileTransferService đang kết nối."""
        return self._file_service

    def set_file_transfer_service(self, file_service: FileTransferService) -> None:
        """Gắn động cơ truyền file và đăng ký nhận sự kiện khi nhận xong file ảo trong RAM.

        Args:
            file_service: Thể hiện FileTransferService dùng để truyền nhận file ảo.
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
        """Tin nhắn chat được đóng gói giả vờ như một file văn bản mang tên _chat_..., truyền đi y hệt như gửi file thật để vi điều khiển không cần phải sửa code mà vẫn hiểu.

        Tạo tên file theo định dạng: _chat_<timestamp>_<station>.txt
        Kích hoạt FileTransferService gửi phiên file in-memory với trình tự: HEADER -> DATA -> END.

        Args:
            text: Nội dung văn bản người dùng nhập từ giao diện chat.

        Returns:
            True nếu phiên truyền được kích hoạt thành công; False nếu kênh truyền đang bận.
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
        """Xử lý khi đã nhận trọn vẹn file tin nhắn ảo từ đường quang: giải mã UTF-8 và hiển thị lên màn hình chat.

        Args:
            virtual_name: Tên file ảo mang siêu dữ liệu định dạng _chat_...
            payload_bytes: Dòng byte nội dung tin nhắn lưu tạm trong RAM.
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
        """Bóc tách tên trạm gửi từ tên file ảo _chat_<thời gian>_<tên trạm>.txt.

        Args:
            virtual_name: Tên file ảo nhận được.

        Returns:
            Tên trạm gửi bóc tách được hoặc tên trạm đối tác mặc định.
        """
        clean = virtual_name.strip()
        if clean.endswith(".txt"):
            clean = clean[:-4]
        parts = clean.split("_")
        if len(parts) >= 5 and parts[4]:
            return "_".join(parts[4:])
        return self._remote_name

    def handle_packet(self, pkt: object) -> None:
        """Bỏ qua việc nhận trực tiếp gói tin thô vì toàn bộ luồng chat đã được gom gói qua FileTransferService.

        Args:
            pkt: Gói tin do serial worker phát ra.
        """
        logger.debug("%s Direct chat frame ignored: type=%s", LogTag.CHAT_SVC, type(pkt).__name__)
