"""Hằng số định dạng khung truyền (Wire-Format Constants) cho giao thức quang IR-OWC.

Tương thích 100% với firmware chuẩn STM32F407 và ứng dụng WinForm C# (docs/WFF.txt).
Cấu trúc khung truyền nhị phân cố định 8 byte tiêu đề (Header Overhead):
    [0xAA][0x55][sequence: uint32 LE (4B)][length: uint16 LE (2B)][payload: length bytes]

Quy ước trường sequence:
    - 0xFFFFFFFF (4294967295): Gói HEADER thông báo phiên truyền file mới và siêu dữ liệu.
    - 0xFFFFFFFE (4294967294): Gói END báo hiệu kết thúc phiên truyền dữ liệu.
    - 0 đến N-1: Chỉ số thứ tự chunk dữ liệu (DATA chunk index) bắt đầu từ 0.
"""

from __future__ import annotations

# Byte đồng bộ khung truyền (Sync Magic) cố định 2 byte: 0xAA 0x55
SYNC_MAGIC: bytes = b"\xAA\x55"
SYNC_BYTE_1: int = 0xAA
SYNC_BYTE_2: int = 0x55

# Mã định danh sequence đặc biệt
SEQ_HEADER: int = 0xFFFFFFFF
SEQ_END: int = 0xFFFFFFFE
SEQ_CHECKPOINT_ACK: int = 0xFFFFFFFD
SEQ_CHECKPOINT_PROBE: int = 0xFFFFFFFC
SEQ_RESUME_REQUEST: int = 0xFFFFFFF9

CHECKPOINT_WINDOW_CHUNKS: int = 16
CHECKPOINT_QUIESCENT_MS: int = 20
CHECKPOINT_ACK_TIMEOUT_MS: int = 600
CHECKPOINT_MAX_PROBE_RETRIES: int = 2

# Kích thước chunk dữ liệu tiêu chuẩn (256 bytes payload / frame)
PACKET_SIZE: int = 256

# Độ dài tiêu đề khung cố định trên dây (2B Sync + 4B Sequence + 2B Length = 8 bytes)
HEADER_OVERHEAD: int = 8

# Giới hạn kích thước payload tối đa theo trường length uint16
MAX_HEADER_PAYLOAD_SIZE: int = 65535

# Bảng tra cứu mã lệnh ASCII đổi Baudrate quang gửi qua COM2 (kết thúc bằng '\r')
BAUD_COMMANDS: dict[int, str] = {
    19200: "00",
    9600: "01",
    4800: "10",
    2400: "11",
}
BAUD_CMD_MAP: dict[int, str] = BAUD_COMMANDS

# Danh sách các tốc độ Baud quang được hỗ trợ giữa 2 trạm qua MCP2122 / TFDU4101
OPTICAL_BAUDRATES: tuple[int, ...] = (2400, 4800, 9600, 19200)
SUPPORTED_BAUDRATES: tuple[int, ...] = OPTICAL_BAUDRATES
AVAILABLE_BAUDRATES: tuple[int, ...] = SUPPORTED_BAUDRATES

# Tốc độ Baud quang mặc định khi khởi động ứng dụng
DEFAULT_OPTICAL_BAUDRATE: int = 19200

# Tốc độ Baud cố định của cổng điều khiển STM32 (COM2)
CONTROL_BAUDRATE: int = 115200
PHYSICAL_BAUDRATE: int = CONTROL_BAUDRATE
DEFAULT_BAUDRATE: int = DEFAULT_OPTICAL_BAUDRATE

# Danh sách baudrate tham chiếu phần cứng MCU
MCU_BAUDRATES: tuple[int, ...] = (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200)

# Ký tự kết thúc lệnh điều khiển baudrate qua COM2 (Carriage Return '\r' = 0x0D)
BAUD_COMMAND_TERMINATOR: bytes = b"\r"

# Thời gian chờ an toàn (ms) sau khi gửi lệnh đổi baud qua COM2 để STM32 ổn định phần cứng
BAUD_SWITCH_GUARD_MS: int = 50

# Biên độ trễ an toàn (ms) cộng thêm vào công thức Open-Loop Paced Delay: ceil(len x 10 x 1000 ÷ baud) + 10 ms
PACED_DELAY_SAFETY_MS: int = 10

# Thời gian watchdog (giây) tự động kết thúc phiên nhận nếu không có dữ liệu mới
RX_INACTIVITY_TIMEOUT_SEC: float = 3.0

# Cấu hình dung lượng file (0 = không giới hạn kích thước)
MAX_FILE_SIZE_BYTES: int = 0
MAX_CONFIGURABLE_FILE_SIZE_BYTES: int = 0xFFFFFFFF

# Dung lượng bộ đệm vòng (Ring Buffer) nhận UART
RING_BUFFER_CAPACITY: int = 65536
RX_COMPACT_THRESHOLD: int = 4096

# Tham số socket TCP mô phỏng liên kết quang nội bộ (Loopback Broker)
SOCKET_RECV_CHUNK: int = 4096
SOCKET_CONNECT_TIMEOUT_SEC: float = 3.0
SOCKET_READ_TIMEOUT_SEC: float = 0.02

# Chu kỳ định thời thăm dò cổng nối tiếp (ms)
MOCK_POLL_MS: int = 2
HW_POLL_MS: int = 10

# Thời gian chờ tối đa khi đóng ứng dụng (ms)
SHUTDOWN_WAIT_MS: int = 3000

# Cửa sổ thời gian tính toán viễn trắc tức thời (giây)
TELEMETRY_WINDOW_SEC: float = 3.0
