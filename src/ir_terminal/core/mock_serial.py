"""Trình giả lập hai cổng nối tiếp COM1/COM2 trong bộ nhớ phục vụ kiểm thử (Mock Serial).

Mô phỏng kênh dữ liệu quang COM1 và kênh điều khiển STM32 COM2 trong cùng một đối tượng transport.
Hỗ trợ tái tạo trễ đường truyền (simulated latency), rớt gói do nhiễu (loss rate), che khuất tầm nhìn (LOS blockage)
và tự động phản hồi lặp vòng (auto-respond echo) để kiểm thử toàn diện mà không cần thiết bị phần cứng thực tế.
"""

from __future__ import annotations

import logging
import random
import threading
import time

from ir_terminal.core.interfaces import ITransport
from ir_terminal.core.ring_buffer import ByteRingBuffer
from ir_terminal.protocol.constants import (
    BAUD_COMMANDS,
    CONTROL_BAUDRATE,
    DEFAULT_BAUDRATE,
    MOCK_POLL_MS,
    RING_BUFFER_CAPACITY,
)
from ir_terminal.protocol.framer import pack_packet, parse_stream
from ir_terminal.protocol.packet import WinFormPacket
from ir_terminal.utils.logger import LogTag, format_hex_preview

logger = logging.getLogger(__name__)


class MockSerialPort(ITransport):
    """Mô phỏng kênh dữ liệu COM1 và kênh điều khiển COM2 phục vụ kiểm thử đơn vị và tích hợp."""

    def __init__(
        self,
        data_port: str = "MOCK_COM1",
        ctrl_port: str | None = "MOCK_COM2",
        baudrate: int = DEFAULT_BAUDRATE,
        ctrl_baudrate: int = CONTROL_BAUDRATE,
        simulated_latency_ms: float = 30.0,
        simulated_loss_rate: float = 0.0,
        auto_respond: bool = True,
    ) -> None:
        """Khởi tạo tầng truyền tải giả lập hai cổng COM ảo.

        Args:
            data_port: Tên định danh cổng dữ liệu ảo COM1.
            ctrl_port: Tên định danh cổng điều khiển ảo COM2 (hoặc None nếu không dùng).
            baudrate: Tốc độ Baud quang học mô phỏng cho COM1.
            ctrl_baudrate: Tốc độ Baud vi điều khiển mô phỏng cho COM2 (115200 bps).
            simulated_latency_ms: Độ trễ phản hồi lặp vòng giả lập tính bằng mili-giây (ms).
            simulated_loss_rate: Xác suất rớt khung truyền ngẫu nhiên trong khoảng 0.0 đến 1.0.
            auto_respond: True để tự động phản hồi lại khung truyền trọn vẹn trên kênh dữ liệu.
        """
        self._data_port = data_port
        self._ctrl_port = ctrl_port or ""
        self._baudrate = int(baudrate)
        self._ctrl_baudrate = int(ctrl_baudrate)
        self._latency_s = simulated_latency_ms / 1000.0
        self._loss_rate = simulated_loss_rate
        self._auto_respond = auto_respond
        self._is_open = False
        self._rx_buffer = ByteRingBuffer(capacity=RING_BUFFER_CAPACITY)
        self._tx_accumulation = bytearray()
        self._simulated_optical_baud = int(baudrate)
        self._los_blocked = False
        self._pending: list[tuple[float, bytes]] = []
        self._dropped_by_loss: int = 0
        self._dropped_by_los: int = 0
        self._baud_commands: list[tuple[int, str]] = []
        self._lock = threading.Lock()

    @property
    def data_port(self) -> str:
        """Tên định danh cổng dữ liệu ảo COM1."""
        return self._data_port

    @property
    def ctrl_port(self) -> str:
        """Tên định danh cổng điều khiển ảo COM2 (hoặc chuỗi rỗng nếu không dùng)."""
        return self._ctrl_port

    @property
    def tx_port(self) -> str:
        """Tên cổng phát dữ liệu ảo."""
        return self._data_port

    @property
    def rx_port(self) -> str:
        """Tên cổng thu dữ liệu ảo."""
        return self._data_port

    @property
    def baudrate(self) -> int:
        """Tốc độ Baud quang học mô phỏng hiện hành."""
        return self._baudrate

    @property
    def control_baudrate(self) -> int:
        """Tốc độ Baud điều khiển vi điều khiển mô phỏng (cố định 115200 bps)."""
        return self._ctrl_baudrate

    @property
    def has_control_port(self) -> bool:
        """Kiểm tra kênh điều khiển ảo COM2 có được cấu hình hay không."""
        return bool(self._ctrl_port)

    @property
    def poll_interval_ms(self) -> int:
        """Chu kỳ định thời thăm dò nhanh của transport mô phỏng trong RAM (mặc định 2 ms)."""
        return MOCK_POLL_MS

    @property
    def sent_baud_commands(self) -> list[tuple[int, str]]:
        """Danh sách toàn bộ các lệnh đổi Baud đã ghi nhận dưới dạng tuple (baud, code)."""
        with self._lock:
            return list(self._baud_commands)

    @property
    def last_baud_command(self) -> tuple[int, str] | None:
        """Lệnh đổi Baud gần nhất đã ghi nhận được, hoặc None nếu chưa có."""
        with self._lock:
            return self._baud_commands[-1] if self._baud_commands else None

    def open(self) -> None:
        """Mở transport mô phỏng và dọn sạch toàn bộ các hàng đợi dữ liệu."""
        self._is_open = True
        self._rx_buffer.clear()
        self._tx_accumulation = bytearray()
        with self._lock:
            self._pending = []
        logger.info(
            "%s Mock open: data=%s ctrl=%s baud=%d ctrl_baud=%d latency=%.1fms "
            "loss=%.3f auto_respond=%s",
            LogTag.HW_SERIAL,
            self._data_port,
            self._ctrl_port or "<none>",
            self._baudrate,
            self._ctrl_baudrate,
            self._latency_s * 1000.0,
            self._loss_rate,
            self._auto_respond,
        )

    def close(self) -> None:
        """Đóng transport mô phỏng và giải phóng hàng đợi phản hồi."""
        was_open = self._is_open
        self._is_open = False
        self._rx_buffer.clear()
        self._tx_accumulation = bytearray()
        with self._lock:
            pending = len(self._pending)
            self._pending = []
        logger.info(
            "%s Mock closed: data=%s was_open=%s dropped_pending=%d loss_drops=%d "
            "los_drops=%d",
            LogTag.HW_SERIAL,
            self._data_port,
            was_open,
            pending,
            self._dropped_by_loss,
            self._dropped_by_los,
        )

    @property
    def is_open(self) -> bool:
        """Kiểm tra transport mô phỏng hiện có đang mở hay không."""
        return self._is_open

    def set_los_blocked(self, blocked: bool) -> None:
        """Mô phỏng tình huống bị che khuất tia sáng vật lý (Line-Of-Sight blockage).

        Args:
            blocked: True để chặn và loại bỏ toàn bộ dữ liệu phát, False để khôi phục liên kết quang.
        """
        self._los_blocked = blocked
        logger.warning(
            "%s Mock LOS %s: data=%s",
            LogTag.OPTICAL_PHY,
            "BLOCKED" if blocked else "CLEARED",
            self._data_port,
        )

    def write(self, data: bytes) -> int:
        """Ghi dữ liệu byte vào kênh dữ liệu COM1 mô phỏng.

        Args:
            data: Dòng byte thô do ứng dụng phát ra.

        Returns:
            Số byte đã tiếp nhận thành công.

        Raises:
            RuntimeError: Nếu transport chưa được mở.
        """
        if not self._is_open:
            raise RuntimeError("Cannot write to closed MockSerialPort")
        if self._los_blocked:
            with self._lock:
                self._dropped_by_los += 1
            return len(data)
        if self._loss_rate > 0 and random.random() < self._loss_rate:
            with self._lock:
                self._dropped_by_loss += 1
            return len(data)
        self._tx_accumulation.extend(data)
        packets, remaining = parse_stream(self._tx_accumulation)
        self._tx_accumulation = remaining
        if self._auto_respond:
            for packet in packets:
                self._schedule_echo(pack_packet(packet.sequence, packet.data))
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s Mock TX: data=%s len=%dB frames=%d hex=%s",
                LogTag.HW_SERIAL,
                self._data_port,
                len(data),
                len(packets),
                format_hex_preview(data),
            )
        return len(data)

    def _schedule_echo(self, frame: bytes) -> None:
        """Đưa khung phản hồi lặp vòng vào hàng đợi chờ phát theo độ trễ mô phỏng."""
        if self._latency_s <= 0:
            self._rx_buffer.write(frame)
            return
        due = time.monotonic() + self._latency_s
        with self._lock:
            self._pending.append((due, frame))

    def _deliver_due(self) -> None:
        """Chuyển các khung phản hồi đã đến hạn trễ vào bộ đệm nhận RX."""
        now = time.monotonic()
        due_frames: list[bytes] = []
        with self._lock:
            if not self._pending:
                return
            remaining: list[tuple[float, bytes]] = []
            for due, frame in self._pending:
                if due <= now:
                    due_frames.append(frame)
                else:
                    remaining.append((due, frame))
            self._pending = remaining
        for frame in due_frames:
            self._rx_buffer.write(frame)

    def read(self, size: int | None = None) -> bytes:
        """Đọc dữ liệu từ bộ đệm nhận COM1 mô phỏng.

        Args:
            size: Số byte cần đọc, hoặc None để đọc toàn bộ dữ liệu có sẵn.

        Returns:
            Chuỗi byte nhị phân trích xuất được.
        """
        if not self._is_open:
            return b""
        self._deliver_due()
        return self._rx_buffer.read(max_bytes=size)

    @property
    def in_waiting(self) -> int:
        """Số byte hiện có sẵn trong bộ đệm nhận COM1 mô phỏng."""
        if not self._is_open:
            return 0
        self._deliver_due()
        return len(self._rx_buffer)

    def flush(self) -> None:
        """Xả dữ liệu phát mô phỏng (no-op)."""

    def send_baud_command(self, baud: int) -> str:
        """Ghi nhận lệnh chuyển đổi tốc độ Baud qua COM2 và cập nhật tốc độ mô phỏng.

        Args:
            baud: Tốc độ Baud quang học đích (2400, 4800, 9600, 19200 bps).

        Returns:
            Mã lệnh 2 ký tự ASCII tương ứng ("00", "01", "10", "11").

        Raises:
            ValueError: Nếu tốc độ Baud không được hỗ trợ.
            RuntimeError: Nếu transport hoặc kênh điều khiển chưa được mở.
        """
        code = BAUD_COMMANDS.get(int(baud))
        if code is None:
            raise ValueError(f"Unsupported optical baudrate {baud}")
        if not self._is_open or not self._ctrl_port:
            raise RuntimeError("Control channel is not open for baud commands")
        with self._lock:
            self._baud_commands.append((int(baud), code))
        logger.info(
            "%s Mock COM2 baud command: baud=%d code=%r data=%s ctrl=%s",
            LogTag.BAUD_CTRL,
            baud,
            code,
            self._data_port,
            self._ctrl_port or "<none>",
        )
        return code

    def reconfigure_data_baud(self, baud: int) -> None:
        """Cập nhật tốc độ Baud của kênh dữ liệu quang COM1 mô phỏng.

        Args:
            baud: Tốc độ Baud mới cần áp dụng.

        Raises:
            ValueError: Nếu tốc độ Baud không được hỗ trợ.
        """
        target = int(baud)
        if target not in BAUD_COMMANDS:
            raise ValueError(f"Unsupported optical baudrate {target}")
        previous = self._baudrate
        self._baudrate = target
        self._simulated_optical_baud = target
        logger.info(
            "%s Mock COM1 baudrate: %d -> %d bps",
            LogTag.BAUD_CTRL,
            previous,
            target,
        )

    @property
    def simulated_optical_baud(self) -> int:
        """Tốc độ Baud quang học hiện đang được mô phỏng."""
        return self._simulated_optical_baud

    @property
    def dropped_by_loss(self) -> int:
        """Tổng số khung truyền bị rớt do mô phỏng nhiễu đường truyền."""
        with self._lock:
            return self._dropped_by_loss

    @property
    def dropped_by_los(self) -> int:
        """Tổng số khung truyền bị rớt do mô phỏng che khuất tia sáng (LOS)."""
        with self._lock:
            return self._dropped_by_los

    def flush_due(self, force: bool = False) -> int:
        """Đẩy ngay lập tức các khung phản hồi lặp vòng phục vụ kiểm thử đơn định.

        Args:
            force: True để phân phối ngay toàn bộ khung mà không cần chờ hết thời gian trễ.

        Returns:
            Số lượng khung đã được phân phối vào bộ đệm nhận.
        """
        due_frames: list[bytes] = []
        with self._lock:
            if force:
                due_frames = [frame for _, frame in self._pending]
                self._pending = []
            else:
                now = time.monotonic()
                remaining: list[tuple[float, bytes]] = []
                for due, frame in self._pending:
                    if due <= now:
                        due_frames.append(frame)
                    else:
                        remaining.append((due, frame))
                self._pending = remaining
        for frame in due_frames:
            self._rx_buffer.write(frame)
        return len(due_frames)

    def inject_rx_data(self, data: bytes) -> None:
        """Bơm trực tiếp dòng byte thô vào bộ đệm nhận COM1 mô phỏng.

        Args:
            data: Chuỗi byte nhị phân cần nạp.
        """
        self._rx_buffer.write(data)

    def inject_packet(self, packet: WinFormPacket) -> None:
        """Đóng gói và bơm một đối tượng WinFormPacket vào bộ đệm nhận COM1 mô phỏng.

        Args:
            packet: Gói tin cần tuần tự hóa và nạp vào bộ đệm.
        """
        self._rx_buffer.write(pack_packet(packet.sequence, packet.data))
