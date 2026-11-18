# KIẾN TRÚC HỆ THỐNG & QUY CHUẨN PHÁT TRIỂN

**Dự án:** IR-OWC Lab Terminal — Desktop PC ↔ STM32F407 + MCP2122 + TFDU4101.
**Giao thức:** tương thích WinForm C# (`docs/WFF.txt`).
**Tech stack:** Python 3.11+, PySide6 (Qt6), PySerial, QWebEngine, `uv`.
**Chuẩn code:** phân tầng rõ ràng, Qt Signals/Slots, SOLID / KISS / DRY / YAGNI,
Google-style docstring, strict typing, TDD.

Wire format và pacing: xem `docs/PROTOCOL.md`.

---

## 1. NGUYÊN TẮC THIẾT KẾ

### 1.1. SOLID

1. **Single Responsibility**
   - `Framer`: đóng/mở khung byte và giữ phần đuôi chưa đủ dữ liệu; không biết
     cổng COM hay UI.
   - `SerialWorker`: vòng lặp đọc/ghi byte trên worker thread; không chứa logic
     file.
   - `FileTransferService`: chia file, gửi theo nhịp, ghi file nhận; không gọi
     widget.
   - `BackendBridge`: chuyển slot Qt thành lệnh service và phát signal cho SPA.
   - `HardwareSetupView` (HTML/JS): chỉ tương tác giao diện.

2. **Open/Closed**
   - Transport phụ thuộc `ITransport`: `DualSerialTransport`, `SocketTransport`,
     `MockSerialPort`.
   - Thêm transport mới không sửa service hay UI.

3. **Liskov**
   - Mọi transport trả `bytes`, ném lỗi serial đồng nhất và expose cùng thuộc
     tính `is_open`, `baudrate`.

4. **Interface Segregation**
   - Giao tiếp liên tầng bằng Qt Signals; `ITransport` là abstraction duy nhất.
   - Không có "god object" cho giao thức.

5. **Dependency Inversion**
   - Service và UI không import thư viện serial trực tiếp.
   - Wiring tập trung tại `src/ir_terminal/app.py`.

### 1.2. KISS / DRY / YAGNI

- **KISS:** giao thức chỉ có HEADER, DATA, END. Không ARQ, không CRC, không
  state machine bắt tay.
- **DRY:** `SYNC_MAGIC`, `SEQ_HEADER`, `SEQ_END`, `PACKET_SIZE`,
  `HEADER_OVERHEAD`, `BAUD_COMMANDS` chỉ định nghĩa tại
  `protocol/constants.py`.
- **YAGNI:** không thêm SHA-256, resume tự động, nén, mã hóa hay mesh routing
  khi firmware không hỗ trợ.

---

## 2. QUY CHUẨN COMMENT & DOCSTRING

Ngôn ngữ code, docstring và commit: **tiếng Anh**. Tài liệu người dùng: tiếng
Việt.

### 2.1. Không dùng inline comment

Trong `.py` và `.js` của dự án:

- Không `# comment` giải thích.
- Không `// comment` giải thích.
- Không comment trang trí hay chia section.
- Không `/* ... */`.

Thông tin cần giải thích nằm trong:

1. Docstring module/class/hàm.
2. Tên biến và hàm tự mô tả.
3. Tài liệu trong `docs/`.

Ngoại lệ duy nhất là file vendored `ui/web/qwebchannel.js` giữ nguyên license
và comment upstream.

### 2.2. Google-style docstring

Mọi module, class và public method đều có docstring:

```python
def pack_packet(sequence: int, data: bytes) -> bytes:
    """Serializes one WinForm frame.

    Args:
        sequence: Frame identifier (uint32).
        data: Payload bytes.

    Returns:
        Wire bytes: sync magic, sequence, length and payload.

    Raises:
        ValueError: If the sequence or payload length does not fit the header.
    """
```

### 2.3. Mô tả layout nhị phân

Offset, kiểu dữ liệu và endianness được mô tả trong docstring của hàm hoặc
module, không dùng comment trong thân hàm:

```python
def pack_header_payload(file_name: str, total_packets: int) -> bytes:
    """Serializes the HEADER packet payload.

    Layout: ``nameLength uint16 LE``, ``name UTF-8``,
    ``totalPackets int32 LE``.
    """
```

---

## 3. CẤU TRÚC THƯ MỤC

```text
LabHongNgoai/
├── README.md
├── docs/
│   ├── README.md                 # index
│   ├── PROTOCOL.md               # wire format hiện hành
│   ├── ARCHITECTURE.md           # file này
│   ├── ACCEPTANCE.md             # runbook nghiệm thu
│   ├── WFF.txt                   # WinForm C# reference
│   └── screenshots/
├── src/ir_terminal/
│   ├── __main__.py
│   ├── app.py                    # lifecycle, CLI, lock, DI
│   ├── config.py                 # LaunchConfig + InstanceContext
│   ├── core/
│   │   ├── interfaces.py         # ITransport
│   │   ├── serial_worker.py      # QObject worker trên QThread
│   │   ├── serial_manager.py     # coordinator + signals
│   │   ├── dual_serial_transport.py # COM1 (Quang) + COM2 (STM32)
│   │   ├── socket_transport.py   # TCP loopback / virtual link
│   │   ├── link_broker.py        # broker hai node
│   │   ├── transport_factory.py
│   │   ├── ring_buffer.py
│   │   └── mock_serial.py
│   ├── protocol/
│   │   ├── constants.py
│   │   ├── framer.py
│   │   └── packet.py
│   ├── services/
│   │   ├── file_transfer_service.py # open-loop paced streaming
│   │   ├── chat_service.py       # virtual file envelope
│   │   ├── storage_service.py
│   │   └── telemetry_service.py
│   ├── ui/
│   │   ├── main_window_web.py
│   │   ├── backend_bridge.py
│   │   └── web/                  # HTML/Tailwind/JS SPA
│   └── utils/
│       ├── logger.py
│       ├── instance_lock.py
│       └── path_helpers.py
├── tests/
│   ├── unit/
│   └── integration/
├── scripts/
│   ├── build_modular_web.py
│   ├── build_standalone.py
│   ├── run_dual_mock.sh
│   └── *.cmd
├── pyproject.toml
└── ir_terminal.spec
```

---

## 4. DỮ LIỆU RUNTIME

- `get_app_root()`: thư mục executable khi frozen, ngược lại repo root.
- `get_app_data_dir()`: `<app root>/data/`; `get_data_dir()`,
  `get_transfers_dir()`, `get_logs_dir()` suy ra từ đây.
- `IR_DATA_DIR` ghi đè gốc `data/`.
- `IR_INSTANCE=A|B` hoặc `--instance` tách sang `data/instances/<ID>/`.
- Không hard-code đường dẫn home; mọi thư mục tạo bằng
  `mkdir(parents=True, exist_ok=True)`.
- Thư mục nhận file đổi được từ UI, lưu tuyệt đối sau `expanduser().resolve()`.
  `default_transfers_dir` luôn được expose để reset.
- Chỉ file nhận đổi chỗ; JSON và `logs/` luôn trong `data/`.

### 4.1. Hai node trên một PC

| Cờ | Ý nghĩa |
| :--- | :--- |
| `--instance A\|B` | Tách dữ liệu, lock và WebEngine profile |
| `--link-role off\|host\|join\|auto` | Host chạy broker in-app |
| `--link-base-port 6001` | `LINK_A` = base, `LINK_B` = base + 1 |
| `--port`, `--tx-port`, `--rx-port` | Ép cổng hoặc cặp cổng |
| `--baud` | Baudrate dữ liệu quang |
| `--auto-connect` | Tự kết nối sau khi UI sẵn sàng |
| `--window-pos left\|right\|normal` | Chia màn hình demo |
| `--allow-multiple` | Bỏ lock (debug) |
| `--broker-only` | Chạy broker headless |
| `--loss`, `--latency-ms`, `--los` | Mô phỏng kênh quang |

`QLockFile` đặt tại `data/instances/<ID>/app.lock`. Mỗi instance có
`QWebEngineProfile` riêng để tránh hỏng cache/cookie SQLite.

---

## 5. LUỒNG THREADING

### 5.1. Quy tắc bất biến

1. **Không block UI thread:** mọi `read()`, `write()`, flush, chờ serial nằm
   trên worker thread.
2. **Worker-Object Pattern:** `SerialWorker(QObject)` được
   `moveToThread(QThread)`; không override `QThread.run()`.
3. **Giao tiếp qua signal:** worker phát `raw_data_received`,
   `packet_received`, `error_occurred`, `baud_changed`; manager chuyển tiếp.
4. **Không truy cập widget từ worker.**

### 5.2. Đường đi dữ liệu

```text
COM1 byte stream
  -> SerialWorker._poll_serial
  -> framer.parse_stream
  -> SerialWorker.packet_received(WinFormPacket)
  -> SerialManager.packet_received
  -> FileTransferService._handle_packet
  -> BackendBridge.fileProgress / transferCompleted
  -> JS controller
```

`parse_stream` bỏ byte nhiễu trước `AA 55`, giữ đuôi khung chưa đủ dữ liệu và
trả `(packets, remaining_buffer)`.

### 5.3. File I/O

- TX đọc từng chunk theo offset từ file handle đang mở; RAM không tăng theo
  kích thước file.
- RX ghi thẳng vào file tạm `.ir_rx_*.part`, flush định kỳ mỗi 16 lần ghi.
- `QTimer` single-shot điều nhịp gửi; không dùng `sleep` trên UI thread.
- Không có ACK nên không có retransmit; watchdog 3 giây kết thúc phiên nhận
  khi END không đến.

---

## 6. GIAO THỨC VÀ SERVICE

### 6.1. Protocol

- `pack_packet(sequence, data)`, `pack_header_payload(name, total)`,
  `unpack_header_payload(data)`, `parse_stream(buffer)`.
- `WinFormPacket` là dataclass frozen với `sequence`, `length`, `data` và các
  property `is_header`, `is_end`, `is_data`.
- Không có `Opcode`, `AckStatus`, CRC-32 hay `FramerStats`.

### 6.2. FileTransferService

- `start_transfer(path)` gửi HEADER, stream DATA, gửi END.
- `paced_delay_ms(frame_length, baudrate)` là nguồn duy nhất của công thức nhịp.
- RX chấp nhận chunk trùng, chunk sai thứ tự và chunk ngoài phạm vi bị bỏ.
- `cancel_transfer()` dừng ngay phiên hiện tại.
- AutoSave chỉ quyết định giữ file `.partial` khi phiên không hoàn tất. Phiên
  nhận đủ packet luôn được chốt thành file thật.
- Mọi phiên ghi một record vào `file_transfers.json` với trạng thái `SUCCESS`,
  `PARTIAL`, `FAILED` hoặc `ABORTED`.

### 6.3. Chat và telemetry

- `ChatService` giữ signal cũ nhưng `send_message` luôn ném `RuntimeError`;
  `handle_packet` bỏ qua mọi frame. UI chat chỉ còn giá trị lịch sử.
- `TelemetryService` tính throughput cửa sổ 3 giây, đếm frame, sniffer 200 bản
  ghi. Không còn RTT Ping/Pong, CRC Valid hay BER thực đo.

---

## 7. QUY CHUẨN CHẤT LƯỢNG

1. **Strict typing:** không `Any`, không `type: ignore` tùy tiện; mọi tham số
   và giá trị trả về có type hint. Dùng `@dataclass(frozen=True)` cho DTO.
2. **Ruff:** line length 100; `uv run ruff check .` phải sạch.
3. **Mypy:** `uv run mypy src/ tests/` phải sạch.
4. **Pytest:** `uv run pytest tests/ -v` phải 100% pass.
5. **Logging:** format
   `[%(asctime)s] [%(levelname)s] [%(name)s:%(lineno)d] %(message)s`.
   Log frame kèm `seq`, `length`, `type`, không log `opcode`/`crc`.
6. **Không LaTeX:** không dùng công thức LaTeX trong tài liệu, docstring hay
   log; dùng ký hiệu văn bản thuần.

---

## 8. KIỂM THỬ

- `tests/unit/test_winform_framer.py`: đóng/mở khung, nhiễu, khung dở.
- `tests/unit/test_winform_wire_compatibility.py`: byte-exact với WinForm C#.
- `tests/unit/test_file_transfer_service.py`: pacing, HEADER/END, RX offset,
  PARTIAL, AutoSave, watchdog.
- `tests/integration/test_paced_streaming_e2e.py`: loopback streamer.
- `tests/integration/test_dual_com1_com2_lifecycle.py`: COM1/COM2, baud command,
  reconfigure.
- `tests/integration/test_serial_manager.py`: worker lifecycle, parsing.
- Mock transport hỗ trợ loss, latency và LOS; dùng cho test mất gói open-loop.

---

## 9. LỆNH PHÁT TRIỂN

```bash
uv sync --extra dev
uv run python -m ir_terminal
uv run pytest tests/ -v
uv run ruff check .
uv run mypy src/ tests/
uv run python scripts/build_modular_web.py
uv run python scripts/build_standalone.py
```

`tests/conftest.py` đặt `QT_QPA_PLATFORM=offscreen` cho CI.
