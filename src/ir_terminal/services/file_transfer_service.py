"""Động cơ truyền nhận file điều tốc vòng hở qua tia hồng ngoại (File Transfer Service).

Module này chịu trách nhiệm:
1. Phát file Open-Loop: Gửi tuần tự HEADER -> DATA (mỗi gói 256 byte) -> END.
2. Điều tốc phát quang (Pacing Delay): Bên gửi sau khi phát 1 gói 256 byte qua tia quang sẽ tự động tạm dừng một khoảng thời gian (ví dụ 148 mili-giây ở 19200) để chip STM32 kịp chép dữ liệu, tránh bị nghẽn hay tràn bộ nhớ của vi điều khiển.
3. Thu nhận và ghi file trực tiếp xuống ổ đĩa: Ghi gói tin nhận được vào file tạm .part tại vị trí tuyệt đối (offset = sequence x 256), chống lệch vị trí file khi rớt gói trên đường truyền quang và tự động đánh dấu PARTIAL nếu bị mất gói giữa chừng.
4. Tương thích tin nhắn chat: Tin nhắn chat được đóng gói giả vờ như một file văn bản mang tên _chat_..., truyền đi y hệt như gửi file thật để vi điều khiển không cần phải sửa code mà vẫn hiểu.
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
    """Trạng thái vòng đời của phiên truyền hoặc nhận file."""

    IDLE = "IDLE"
    SENDING = "SENDING"
    RECEIVING = "RECEIVING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"

    @property
    def is_active(self) -> bool:
        """Kiểm tra xem hệ thống có đang bận truyền hoặc nhận file hay không."""
        return self in (TransferState.SENDING, TransferState.RECEIVING)


def paced_delay_ms(packet_length: int, baudrate: int) -> int:
    """Tính toán thời gian trễ nhịp phát (inter-frame pacing delay) giữa 2 khung liên tiếp trên đường quang.

    Công thức chuẩn WinForm C#:
        delay_ms = ceil(packet_length x 10.0 x 1000 ÷ baudrate) + 10 ms

    Trong đó:
        - packet_length: Tổng độ dài khung trên dây (bao gồm 8 byte header + payload length).
          Ví dụ gói DATA 256 bytes thì packet_length = 264 bytes.
        - 10.0: 10 bit UART vật lý cho mỗi byte truyền (1 start bit + 8 data bits + 1 stop bit).
        - 10 ms (PACED_DELAY_SAFETY_MS): Biên độ trễ an toàn cho STM32 kịp xử lý ngắt.

    Giá trị danh định cho gói DATA 264 bytes:
        - 19200 bps: 148 ms
        - 9600 bps:  285 ms
        - 4800 bps:  560 ms
        - 2400 bps:  1110 ms

    Args:
        packet_length: Tổng số byte của khung truyền trên dây cáp quang.
        baudrate: Tốc độ Baud quang hiện hành của COM1 (bps).

    Returns:
        Khoảng thời gian nghỉ giữa hai khung tính bằng mili-giây (ms).

    Raises:
        ValueError: Nếu baudrate <= 0 hoặc độ dài packet âm.
    """
    if baudrate <= 0:
        raise ValueError(f"Baudrate must be positive, got {baudrate}")
    if packet_length < 0:
        raise ValueError(f"Packet length must be non-negative, got {packet_length}")
    return math.ceil(packet_length * 10.0 * 1000 / baudrate) + PACED_DELAY_SAFETY_MS


def is_chat_envelope_name(file_name: str) -> bool:
    """Kiểm tra tên file thông báo có phải là bao thư tin nhắn chat ảo (Virtual File Chat Envelope) hay không."""
    clean = (file_name or "").strip()
    return clean.startswith("_chat_") and clean.endswith(".txt")


def sanitize_received_name(file_name: str) -> str:
    """Lọc sạch tên file nhận được từ đường quang: loại bỏ ký tự nguy hiểm, đường dẫn thư mục và ký tự điều khiển.

    Args:
        file_name: Tên file mang trong gói tin HEADER.

    Returns:
        Tên file an toàn để lưu xuống ổ cứng.
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
    """Tự động thêm số thứ tự (1), (2)... nếu tên file đã tồn tại trên đĩa để không bị ghi đè mất file cũ."""
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
    """Động cơ phát và thu nhận file qua đường truyền tia quang (không cần tín hiệu báo nhận ACK/NACK)."""

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
        """Khởi tạo động cơ truyền nhận file.

        Args:
            serial_manager: Bộ não điều phối gửi gói tin ra cổng COM.
            chunk_size: Kích thước mỗi gói dữ liệu phát đi (chuẩn STM32 là 256 bytes).
            storage_service: Dịch vụ lưu nhật ký vào ổ cứng.
            transfers_dir: Thư mục chứa các file nhận được.
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
        """Kiểm tra xem hệ thống có đang bận phát hoặc thu nhận file hay không."""
        return self._state.is_active

    @property
    def state(self) -> TransferState:
        """Trạng thái hiện tại của phiên truyền (IDLE, SENDING, RECEIVING, COMPLETED, FAILED, ABORTED)."""
        return self._state

    @property
    def chunk_size(self) -> int:
        """Kích thước gói dữ liệu payload cấu hình (mặc định 256 bytes)."""
        return self._chunk_size

    @property
    def max_file_size_bytes(self) -> int:
        """Giới hạn dung lượng file tối đa cho phép nhận (0 = không giới hạn)."""
        return self._max_file_size_bytes

    @property
    def transfers_dir_path(self) -> Path:
        """Thư mục trên ổ cứng nơi lưu các file đã nhận hoàn tất."""
        return self._transfers_dir

    @property
    def default_transfers_dir_path(self) -> Path:
        """Thư mục nhận file mặc định ban đầu của ứng dụng."""
        return self._default_transfers_dir

    @property
    def auto_save(self) -> bool:
        """Cờ tự động lưu: True nếu cho phép giữ lại file chưa hoàn tất (.part) khi đường quang bị lỗi."""
        return self._auto_save

    @property
    def uses_extended_protocol(self) -> bool:
        """Luôn là False vì giao thức chuẩn WinForm C# và STM32 không dùng chế độ mở rộng."""
        return False

    def get_config(self) -> dict[str, str | int | float | bool]:
        """Lấy toàn bộ thông số cấu hình truyền file hiện tại gửi lên giao diện."""
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
        """Đọc các cài đặt truyền nhận file từ ổ cứng (kích thước tối đa, thư mục lưu, tự động lưu).

        Returns:
            Từ điển chứa các thông số cấu hình truyền file hiện tại.
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
        """Cập nhật và lưu lại giới hạn dung lượng file tối đa cho phép nhận (0 = không giới hạn).

        Args:
            size_bytes: Dung lượng tối đa tính theo byte.

        Returns:
            Dung lượng hiệu dụng sau khi kiểm tra hợp lệ.

        Raises:
            ValueError: Nếu size_bytes là số âm.
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
        """Cập nhật và lưu lại đường dẫn thư mục lưu file nhận được trên máy tính.

        Args:
            dir_path: Đường dẫn thư mục đích.

        Returns:
            Đường dẫn thư mục tuyệt đối đã được xác thực.

        Raises:
            ValueError: Nếu đường dẫn rỗng hoặc không thể tạo được thư mục.
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
        """Lưu lại tùy chọn giữ lại file một phần khi đường truyền quang bị lỗi giữa chừng.

        Args:
            enabled: True để giữ lại file lỗi (.partial), False để xóa bỏ.

        Returns:
            Giá trị cờ đã lưu.
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
        """Áp dụng các thay đổi cấu hình từ màn hình cài đặt và lưu xuống ổ cứng.

        Args:
            max_file_size_bytes: Dung lượng file tối đa mới (0 = không giới hạn).
            transfers_dir: Thư mục nhận file mới.
            auto_save: Tùy chọn giữ lại file một phần.

        Returns:
            Toàn bộ cấu hình sau khi cập nhật.
        """
        if max_file_size_bytes is not None:
            self.set_max_file_size_bytes(int(max_file_size_bytes))
        if transfers_dir is not None and str(transfers_dir).strip():
            self.set_transfers_dir(transfers_dir)
        if auto_save is not None:
            self.set_auto_save(bool(auto_save))
        return self.get_config()

    def start_transfer(self, file_path_str: str) -> None:
        """Bắt đầu quá trình phát file qua tia hồng ngoại: chia file thành các gói 256 byte và gửi gói mở đầu HEADER.

        Args:
            file_path_str: Đường dẫn file trên ổ cứng máy tính cần gửi đi.

        Raises:
            ValueError: Nếu hệ thống đang bận gửi file khác, không tìm thấy file hoặc file vượt quá kích thước cho phép.
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
        """Bắt đầu phát gói tin file ảo trong RAM (dùng cho tin nhắn chat).

        Args:
            virtual_name: Tên file ảo mang siêu dữ liệu báo trong gói HEADER.
            payload: Chuỗi byte nội dung tin nhắn cần gửi.

        Returns:
            True nếu phát thành công gói mở đầu; False nếu hệ thống đang bận.
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
        """Hủy bỏ ngay lập tức phiên phát hoặc nhận file quang học đang diễn ra."""
        if self._state is TransferState.SENDING:
            self._fail_tx("Transfer cancelled by user", status="ABORTED", aborted=True)
            return
        if self._state is TransferState.RECEIVING:
            self._fail_rx("Incoming transfer cancelled by user", status="ABORTED", aborted=True)
            return
        logger.debug("%s Cancel ignored: state=%s", LogTag.FILE_SVC, self._state.name)

    def abort_transfer(self) -> None:
        """Tên gọi tương thích ngược cho hàm cancel_transfer."""
        self.cancel_transfer()

    def _baudrate(self) -> int:
        """Tốc độ Baud quang hiện hành của kênh COM1 dùng để tính khoảng nghỉ an toàn (pacing delay) giữa 2 gói tin."""
        value = getattr(self._serial_manager, "current_baud", DEFAULT_BAUDRATE)
        try:
            baud = int(value)
        except (TypeError, ValueError):
            return DEFAULT_BAUDRATE
        return baud if baud > 0 else DEFAULT_BAUDRATE

    def _set_state(self, new_state: TransferState) -> None:
        """Cập nhật trạng thái phiên truyền và phát tín hiệu cho giao diện hiển thị."""
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
        """Phát khung DATA tiếp theo hoặc kích hoạt phát khung END khi hết dữ liệu.

        Mỗi chunk dữ liệu (tối đa 256 bytes) được đọc từ file và gửi qua cổng nối tiếp COM1.
        Sau khi phát xong 1 chunk, QTimer sẽ được hẹn giờ với khoảng trễ paced_delay_ms()
        để đảm bảo không làm tràn bộ đệm nhận của vi điều khiển STM32.
        """
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
        """Gửi gói tin kết thúc SEQ_END, lưu nhật ký truyền file vào ổ cứng và phát tín hiệu hoàn tất."""
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
        """Tính toán phần trăm tiến độ, tốc độ thực tế (KB/s), thời gian còn lại (ETA) và phát tín hiệu lên thanh tiến trình."""
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
        """Đóng an toàn tệp tin nguồn đang đọc để tránh rò rỉ bộ nhớ."""
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
        """Xử lý khi việc gửi file bị lỗi hoặc người dùng bấm Hủy: dừng bộ hẹn giờ, đóng file và ghi nhận trạng thái FAILED/ABORTED."""
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
        """Tiếp nhận và phân loại khung tin do serial worker gửi lên (gói mở đầu HEADER, gói kết thúc END, hay gói dữ liệu DATA).

        Args:
            pkt: Gói tin khung truyền WinForm do worker giải mã.
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
        """Khởi tạo phiên tiếp nhận file mới khi nhận được khung tiêu đề SEQ_HEADER.

        Giải mã tên file và tổng số lượng packet dự kiến. Nếu là tin nhắn chat ảo
        (_chat_*.txt), phiên nhận sẽ cấp phát bộ nhớ RAM io.BytesIO() để xử lý tức thì.
        Nếu là file vật lý, ứng dụng tạo file tạm .ir_rx_<name>.part trên ổ cứng và
        kích hoạt watchdog timer (3.0s).
        """
        try:
            file_name, total_packets = unpack_header_payload(pkt.data)
        except ValueError as exc:
            logger.warning("%s RX invalid HEADER dropped: %s", LogTag.FILE_SVC, exc)
            return
        if self._max_file_size_bytes and (total_packets * PACKET_SIZE) > self._max_file_size_bytes:
            logger.warning(
                "%s RX rejected file '%s': declared size exceeds %d B limit",
                LogTag.FILE_SVC,
                file_name,
                self._max_file_size_bytes,
            )
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
        """Ghi trực tiếp chunk dữ liệu vào file tạm theo offset tuyệt đối: sequence x 256.

        Cơ chế Seek-and-Write Direct Offset:
            - Dữ liệu được ghi thẳng vào vị trí: offset = sequence * 256.
            - Nếu một khung bị mất do nhiễu quang, vị trí tương ứng trên file sẽ tự động
              chứa các byte 0x00 mà không làm xô lệch bất kỳ byte nào của các chunk sau.
            - Tự động reset bộ đếm thời gian watchdog (3.0s) sau mỗi lần ghi thành công.
        """
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
            actual_bytes = len(self._rx_received) * self._chunk_size
            speed_kbps = (actual_bytes / 1024.0) / elapsed if elapsed > 0 else 0.0
            remaining = max(total - received, 0)
            eta_sec = remaining * self._chunk_size / (self._baudrate() / 10.0)
            self.progress_updated.emit(min(pct, 100), speed_kbps, eta_sec, received, total)

    def _finish_virtual_receive(self) -> None:
        """Hoàn tất việc thu nhận file ảo trong RAM (tin nhắn chat), phát tín hiệu để hiển thị lên khung chat."""
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
        """Hoàn tất quá trình nhận file sau khi nhận được gói tin kết thúc SEQ_END: đổi tên file tạm .part thành file chính thức."""
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
        """Kích hoạt bộ định thời giám sát (watchdog) tự động kết thúc nếu quá 3 giây không có byte nào mới từ tia quang."""
        if self._state is not TransferState.RECEIVING:
            return
        received = len(self._rx_received)
        reason = (
            f"Receive timeout after {RX_INACTIVITY_TIMEOUT_SEC:.1f}s without data "
            f"({received}/{self._rx_total_packets} packets)"
        )
        self._fail_rx(reason, status="FAILED", aborted=False)

    def _fail_rx(self, reason: str, status: str = "FAILED", aborted: bool = False) -> None:
        """Xử lý khi việc nhận file bị gián đoạn: lưu lại file một phần nếu bật AutoSave hoặc dọn sạch file tạm."""
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
        """Đóng và xóa tệp tin tạm thời đang nhận nếu không cần giữ lại."""
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
        """Lưu bản ghi nhật ký truyền nhận file vào tệp lịch sử trên ổ đĩa."""
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
