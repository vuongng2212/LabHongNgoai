# KIẾN TRÚC HỆ THỐNG & QUY CHUẨN PHÁT TRIỂN (CONVENTIONS & BEST PRACTICES)
**Dự án:** IR-OWC Lab Terminal (Desktop Application cho Phòng Thí Nghiệm Truyền Thông Quang Không Dây Hồng Ngoại)  
**Tech-Stack:** Python 3.11+, PySide6 (Qt6), PySerial, PyQtGraph, UV Package Manager  
**Chuẩn lập trình:** Clean Layered Architecture, Event-Driven (Qt Signals/Slots), SOLID / KISS / DRY / YAGNI, Google-Style Docstrings, Strict Typing, TDD.

---

## 1. NGUYÊN TẮC THIẾT KẾ PHẦN MỀM CỐT LÕI (CORE DESIGN PRINCIPLES)

Dự án tuân thủ nghiêm ngặt các nguyên lý kỹ nghệ phần mềm để đảm bảo hệ thống dễ kiểm thử, mở rộng và bàn giao cho đội IoT / khách hàng:

### 1.1. Bộ nguyên lý SOLID
1. **Single Responsibility Principle (SRP - Đơn trách nhiệm):**
   - Mỗi module / class chỉ đảm nhận một lý do duy nhất để thay đổi.
   - `Framer`: Chỉ làm nhiệm vụ parse byte stream thành `Packet` và tính CRC-32; không quan tâm gói tin được gửi qua cổng COM nào hay hiển thị lên UI ra sao.
   - `SerialWorker`: Chỉ quản lý vòng lặp đọc/ghi byte thô từ phần cứng; không can thiệp vào logic nghiệp vụ (chat/file).
   - `FileTransferService`: Chỉ xử lý chia nhỏ file (chunking), reassembly và tính hash; không gọi trực tiếp các hàm giao diện Qt.
   - `HardwareSetupView`: Chỉ xử lý tương tác giao diện màn hình kết nối; không chứa logic kết nối serial trực tiếp.

2. **Open/Closed Principle (OCP - Đóng để sửa, Mở để rộng):**
   - Các tầng nghiệp vụ phụ thuộc vào Abstract Interface (`ITransport`, `IProtocolHandler`).
   - Khi cần đổi từ giao tiếp `SerialTransport` (cổng COM) sang `MockSerialTransport` (kiểm thử) hoặc `SocketTransport` (WiFi/Ethernet sau này), chỉ cần tạo class mới hiện thực interface mà không sửa 1 dòng code nào trong `FileTransferService` hay `ChatService`.

3. **Liskov Substitution Principle (LSP - Thay thế Liskov):**
   - `MockSerialPort` và `HardwareSerialPort` phải có cùng hành vi chuẩn: cùng trả về `bytes`, cùng ném ra `SerialTimeoutException` hoặc `SerialException` đồng nhất khi mất kết nối, đảm bảo test chạy đúng với thực tế phần cứng.

4. **Interface Segregation Principle (ISP - Phân tách Interface):**
   - Không tạo các "God Object" hay Interface khổng lồ chứa tất cả các hàm. Tách biệt các observer/callback thành các protocol riêng: `IFileProgressListener`, `IChatMessageListener`, `ITelemetryUpdateListener`.

5. **Dependency Inversion Principle (DIP - Đảo ngược phụ thuộc):**
   - High-level modules (Services, UI Controllers) không phụ thuộc trực tiếp vào Low-level modules (PySerial C-bindings). Cả hai đều phụ thuộc vào Abstraction (`ITransport`).
   - Việc tiêm phụ thuộc (Dependency Injection / Wiring) được thực hiện tập trung tại `src/ir_terminal/app.py`.

### 1.2. KISS - DRY - YAGNI
- **KISS (Keep It Simple, Stupid):** Giữ giao thức Stop-and-Wait ARQ đơn giản, rõ ràng, không cố cài đặt Sliding Window phức tạp khi đặc tính kênh truyền quang hồng ngoại trong phòng lab là Half-Duplex và tốc độ 115.2 kbps.
- **DRY (Don't Repeat Yourself):** Toàn bộ magic numbers, byte delimiters (SOF `0xAA55`, EOF `0x55AA`), Opcode (`0x01` DATA, `0x02` ACK, `0x05` PING) chỉ được định nghĩa một lần duy nhất tại `protocol/constants.py`.
- **YAGNI (You Aren't Gonna Need It):** Không viết sẵn các tính năng vượt scope như mã hóa AES-256, nén file zip phức tạp, multi-hop mesh routing khi chưa có trong yêu cầu MVP 3 tuần.

---

## 2. QUY CHUẨN COMMENT & TÀI LIỆU HÓA CODE (CODE COMMENTS & DOCSTRINGS)

Ngôn ngữ sử dụng trong code, docstring, commit và tài liệu kỹ thuật: **100% Tiếng Anh (English)**.

### 2.1. Chuẩn Docstring: Google Python Style Guide (PEP 257)
Mọi Module, Class, và Public Method bắt buộc phải có docstring theo định dạng Google Style:

```python
def parse_frame(raw_buffer: bytes) -> tuple[Packet | None, bytes]:
    """Extracts and validates the first valid protocol frame from the raw buffer.

    Scans for the Start-of-Frame (SOF: 0xAA55) delimiter, extracts the packet
    header, validates payload length, checks the CRC-32 checksum against corruption,
    and strips the parsed bytes from the buffer.

    Args:
        raw_buffer: Byte sequence accumulated from the serial receiver ring buffer.

    Returns:
        A tuple containing:
            - Packet instance if a valid, verified frame was parsed; otherwise None.
            - Remaining unprocessed bytes in the buffer.

    Raises:
        FrameCorruptedError: If CRC-32 mismatch is detected for a complete frame.
    """
```

### 2.2. Quy tắc Comment cấu trúc nhị phân và phần cứng (Hardware Protocol Comments)
Khi thao tác với `struct.pack`, `struct.unpack`, hoặc toán tử bitwise (`<<`, `>>`, `&`, `|`), **BẮT BUỘC** phải có inline comment ghi rõ:
1. Byte Offset (Vị trí byte).
2. Tên trường (Field Name).
3. Kiểu dữ liệu & Endianness (uint8, uint16-LE, uint32-LE).
4. Ý nghĩa vật lý / Đơn vị.

```python
# PACKET HEADER STRUCTURE (Total: 6 bytes)
# Offset 0-1: Start of Frame (SOF) -> 0xAA55 (uint16, Big-Endian magic)
# Offset 2  : Sequence Number      -> 0x00..0xFF (uint8)
# Offset 3  : Opcode / Command     -> 0x01=DATA, 0x02=ACK, 0x05=PING (uint8)
# Offset 4-5: Payload Length (N)   -> 0..1024 bytes (uint16, Little-Endian)
header_bytes = struct.pack("<HBBH", SOF_MAGIC, seq_id, opcode, payload_len)
```

### 2.3. Nguyên tắc "Self-Documenting Code" vs "Why-Comment"
- **Không comment những thứ hiển nhiên:**
  * ❌ `# Increment index: i = i + 1`
  * ❌ `# Check if port is open: if port.is_open:`
- **Bắt buộc comment giải thích lý do kỹ thuật (The "WHY" & Hardware constraints):**
  * ✅ `# Sleep 5ms to allow MCP2122 baud rate generator PLL to settle after baud switch.`
  * ✅ `# Limit chunk size to 256 bytes to prevent STM32 USB-CDC internal ring buffer overflow.`
  * ✅ `# Invert carrier sense threshold due to TFDU4101 active-low RXD output.`

---

## 3. CẤU TRÚC THƯ MỤC DỰ ÁN CHUẨN (PROJECT DIRECTORY STRUCTURE)

```text
LabHongNgoai/
├── .venv/                         # Virtualenv quản lý bởi uv
├── docs/                          # Tài liệu kiến trúc, specs, runbook
│   ├── ARCHITECTURE_AND_SPECS.md
│   ├── CONVENTIONS_AND_ARCHITECTURE.md
│   ├── IOT_INTEGRATION_RUNBOOK.md
│   └── ROADMAP_AND_MVP_PLAN.md
├── src/
│   └── ir_terminal/
│       ├── __init__.py
│       ├── __main__.py            # Entry point chạy app: `uv run python -m ir_terminal`
│       ├── app.py                 # QApplication lifecycle & dependency injection container
│       │
│       ├── core/                  # [TẦNG CORE] Quản lý I/O & Threading
│       │   ├── __init__.py
│       │   ├── interfaces.py      # Abstract Base Classes (ITransport, ISerialWorker)
│       │   ├── serial_worker.py   # QObject Worker chạy trên QThread riêng (Non-blocking I/O)
│       │   ├── serial_manager.py  # Quản lý vòng đời kết nối COM (Connect, Disconnect, Scan)
│       │   ├── ring_buffer.py     # Bộ đệm vòng byte stream chống tràn
│       │   └── mock_serial.py     # Giả lập Serial Port (Simulate drop rate, latency, LOS blockage)
│       │
│       ├── protocol/              # [TẦNG GIAO THỨC] Định nghĩa & Đóng gói Frame
│       │   ├── __init__.py
│       │   ├── constants.py       # SOF, EOF, OPCODES, TIMEOUTS, ERROR_CODES
│       │   ├── framer.py          # Byte stuffing, SOF/EOF delimiter parser, CRC-32
│       │   ├── packet.py          # Immutable Dataclasses (Packet, Header)
│       │   └── state_machine.py   # Stop-and-Wait ARQ & Auto-Pause State Machine
│       │
│       ├── services/              # [TẦNG DỊCH VỤ] Nghiệp vụ ứng dụng
│       │   ├── __init__.py
│       │   ├── file_service.py    # Chunking, reassembly, SHA-256 verifier
│       │   ├── chat_service.py    # Message queuing, Half-Duplex carrier sense
│       │   └── telemetry_service.py # Tính toán Throughput (KB/s), Loss Rate, RTT
│       │
│       ├── models/                # [TẦNG DỮ LIỆU] Immutable Domain Entities & Schemas
│       │   ├── __init__.py
│       │   ├── serial_info.py     # Port info, Baudrate, Parity config
│       │   ├── transfer_state.py  # Trạng thái truyền (IDLE, TRANSMITTING, PAUSED, COMPLETED)
│       │   ├── chat_message.py    # Text message schema, ACK status
│       │   └── telemetry_data.py  # Metric samples cho đồ thị
│       │
│       ├── ui/                    # [TẦNG GIAO DIỆN] PySide6 Widgets (Apple Light Precision)
│       │   ├── __init__.py
│       │   ├── main_window.py     # Cửa sổ chính (Sidebar navigation + QStackedWidget)
│       │   ├── styles/
│       │   │   ├── __init__.py
│       │   │   ├── theme_apple.qss# Bảng màu Apple Light Precision
│       │   │   └── palette.py     # Mã màu hex chuẩn hóa (Cobalt Blue #2563EB, Slate Gray)
│       │   ├── views/             # 4 Màn hình chuyên biệt
│       │   │   ├── hardware_view.py   # View 1: Kết nối & Căn chỉnh tia
│       │   │   ├── file_view.py       # View 2: Truyền nhận file/ảnh
│       │   │   ├── chat_view.py       # View 3: Trò chuyện 2 chiều
│       │   │   └── telemetry_view.py  # View 4: Đồ thị & Xuất CSV
│       │   └── components/        # UI Widgets tái sử dụng
│       │       ├── status_badge.py    # Badge LOS/ACK
│       │       ├── progress_card.py   # Card thanh tiến độ
│       │       ├── metric_card.py     # Card KPI số liệu lớn
│       │       ├── chat_bubble.py     # Bong bóng chat kèm 2 dấu tích ACK
│       │       └── hex_drawer.py      # Ngăn kéo xem log hex ẩn/hiện
│       │
│       └── utils/                 # [TIỆN ÍCH]
│           ├── __init__.py
│           ├── logger.py          # Rotating file logger + Qt Signal log sink
│           └── path_helpers.py    # Resolve đường dẫn asset an toàn cho PyInstaller
│
├── tests/                         # [KIỂM THỬ TỰ ĐỘNG] pytest + pytest-qt
│   ├── conftest.py                # Fixtures (Mock Serial, QTest app)
│   ├── unit/
│   │   ├── test_framer.py         # Test đóng gói/giải mã Frame & CRC-32
│   │   ├── test_file_chunker.py   # Test chia nhỏ & ghép file SHA-256
│   │   └── test_state_machine.py  # Test chuyển trạng thái ARQ & Timeout Retry
│   └── integration/
│       ├── test_serial_pipeline.py # Test luồng truyền nhận qua Virtual COM loopback
│       └── test_ui_events.py      # Test phản hồi nút bấm & tiến độ giao diện
│
├── pyproject.toml                 # Cấu hình uv, dependencies, ruff, mypy, pytest
├── README.md
└── start.sh                       # Script chạy nhanh
```

---

## 4. NGUYÊN TẮC LUỒNG THREADING & NON-BLOCKING HARDWARE I/O

### Quy tắc bất biến (Inviolable Rules):
1. **Không bao giờ block Main Thread (UI Thread):**
   - Mọi thao tác I/O (`serial.read()`, `serial.write()`), tính toán nặng (băm SHA-256 file lớn), hoặc chờ timeout **phải chạy trên Worker Thread**.
2. **Worker-Object Pattern với `moveToThread(QThread)`:**
   - Không bao giờ kế thừa trực tiếp từ `QThread` để override `run()` chứa business logic.
   - Luôn tạo class `SerialWorker(QObject)`, chuyển sang thread riêng bằng `worker.moveToThread(thread)` và điều khiển qua Qt Signals/Slots.
3. **Giao tiếp liên luồng an toàn (Thread-Safe Inter-thread Communication):**
   - Dữ liệu giữa Worker Thread và UI Thread được truyền qua Qt Signal với Type định danh rõ ràng (`Signal(bytes)`, `Signal(Packet)`, `Signal(TransferState)`).
   - Tuyệt đối không truy cập trực tiếp widget của UI từ Worker Thread.

---

## 5. QUY CHUẨN KIỂM SOÁT KIỂU TĨNH & CHẤT LƯỢNG MÃ NGUỒN

1. **Strict Typing (Không ngoại lệ):**
   - Cấm `Any`, cấm `type: ignore` (nếu bắt buộc phải dùng do thư viện C, phải comment lý do rõ ràng).
   - Toàn bộ hàm, tham số, biến phải có Type Hint rõ ràng (`int | str`, `list[Packet]`, `Callable[[bytes], None]`).
   - Sử dụng `@dataclass(frozen=True)` cho các object truyền dữ liệu để đảm bảo tính bất biến (Immutability).
2. **Linting & Formatting (Ruff + Mypy):**
   - Enforce line length: **100 characters**.
   - Mypy chạy ở mức `strict = true` (`disallow_untyped_defs = true`, `no_implicit_optional = true`).
3. **Structured Logging:**
   - Định dạng chuẩn: `[%(asctime)s] [%(levelname)s] [%(name)s:%(lineno)d] %(message)s`
   - Mọi gói tin truyền nhận đều phải log kèm `seq_id`, `opcode`, `len`, `crc` để phục vụ đối chiếu nhị phân với firmware STM32 của đội IoT.

---

## 6. CHIẾN LƯỢC TEST & GIẢ LẬP PHẦN CỨNG (TESTING & HARDWARE MOCKING)

Vì ứng dụng cần giao tiếp với vi điều khiển STM32 nhưng trong 2 tuần đầu nhóm phần mềm phải làm việc độc lập:
1. **Mock Serial (`MockSerialPort`):**
   - Giả lập `serial.Serial` trong `tests/` với các kịch bản thực tế:
     - Tỷ lệ mất gói ngẫu nhiên (Loss Rate 1% - 10%).
     - Độ trễ phản hồi (Latency RTT 20ms - 100ms).
     - Giả lập chắn tia quang học (LOS Blockage) để test tính năng Auto-Pause & Resume.
2. **Integration Test (Virtual COM Loopback):**
   - Sử dụng cặp cổng COM ảo (`socat` trên Linux / `com0com` trên Windows) để chạy 2 instance phần mềm đối xứng (Peer-to-Peer) kiểm thử truyền file và chat thực tế.

---

## 7. CẤU HÌNH CÔNG CỤ QUẢN LÝ `uv` (TOOLCHAIN COMMANDS)

* **Đồng bộ môi trường:** `uv sync`
* **Chạy ứng dụng:** `uv run python -m ir_terminal`
* **Chạy kiểm thử:** `uv run pytest tests/ -v`
* **Kiểm tra Lint & Format:** `uv run ruff check . && uv run ruff format .`
* **Kiểm tra Type:** `uv run mypy src/ tests/`
* **Đóng gói file thực thi `.exe`:**
  ```bash
  uv run pyinstaller --noconfirm --onedir --windowed --name "IR_OWC_Lab_Terminal" src/ir_terminal/__main__.py
  ```
