# BÁO CÁO PHÂN TÍCH CHUYÊN SÂU PHẦN CỨNG COM/UART, LUỒNG TÍN HIỆU VÀ CẨM NANG DEBUG HỆ THỐNG LABHONGNGOAI

> **Tài liệu Kỹ thuật & Vận hành Lab**  
> **Dự án:** Trạm Thu Phát Quang Không Dây Hồng Ngoại (SpectraLink IR-OWC Workstation)  
> **Đối tượng sử dụng:** Đội ngũ Phát triển Phần mềm, Kỹ sư Phần cứng / Firmware IoT STM32, Kỹ thuật viên Triển khai Phòng Lab.  
> **Mục tiêu:** Phân tích toàn diện kiến trúc hệ thống, luồng kết nối cổng COM/UART vật lý, đường đi tín hiệu hai chiều (TX/RX Data Path), cơ chế bảo toàn dữ liệu Stop-and-Wait ARQ và cẩm nang cô lập lỗi phần cứng chuyên sâu.

---

## MỤC LỤC
1. [Tổng Quan Kiến Trúc Hệ Thống LabHongNgoai](#1-tổng-quan-kiến-trúc-hệ-thống-labhongngoai)
2. [Phân Tích Chuyên Sâu Luồng Kết Nối Phần Cứng Cổng COM/UART](#2-phân-tích-chuyên-sâu-luồng-kết-nối-phần-cứng-cổng-comuart)
3. [Phân Tích Toàn Bộ Luồng Truyền Nhận Tín Hiệu (TX / RX Data Path)](#3-phân-tích-toàn-bộ-luồng-truyền-nhận-tín-hiệu-tx--rx-data-path)
4. [Cơ Chế Điều Khiển Luồng Stop-and-Wait ARQ & Bảo Toàn Dữ Liệu](#4-cơ-chế-điều-khiển-luồng-stop-and-wait-arq--bảo-toàn-dữ-liệu)
5. [Rà Soát Hệ Thống Log & Năng Lực Debug/Thu Thập Lỗi](#5-rà-soát-hệ-thống-log--năng-lực-debugthu-thập-lỗi)
6. [Cẩm Nang Cô Lập Lỗi Phần Cứng & Troubleshooting Checklist Cho Team IoT](#6-cẩm-nang-cô-lập-lỗi-phần-cứng--troubleshooting-checklist-cho-team-iot)

---

## 1. TỔNG QUAN KIẾN TRÚC HỆ THỐNG LABHONGNGOAI

Dự án **LabHongNgoai** được thiết kế theo mô hình kiến trúc phân tầng hướng dịch vụ (Layered Service-Oriented Architecture), phân tách độc lập giữa giao diện hiển thị hiện đại (Modern Web-based UI), tầng nghiệp vụ (Application Services), giao thức khung truyền nhị phân (Binary Protocol Engine) và tầng điều khiển phần cứng thời gian thực (Hardware Core Drivers).

```
+---------------------------------------------------------------------------------------+
|                                  PRESENTATION LAYER                                   |
|   Modern Chromium WebEngine UI (HTML5 / Tailwind CSS / Vanilla JS Canvas & Charts)    |
|               ^                                                   |                   |
|               | (Qt Signals)                                      v (JS @Slot calls)  |
|   +-------------------------------------------------------------------------------+   |
|   |          BackendBridge (src/ir_terminal/ui/backend_bridge.py)                 |   |
|   |          Bi-directional IPC via QWebChannel ('bridge' registered object)      |   |
|   +-------------------------------------------------------------------------------+   |
+------------------------------------------|--------------------------------------------+
                                           | Method Invocations / Signals
+------------------------------------------v--------------------------------------------+
|                               APPLICATION SERVICES LAYER                              |
|  +--------------------+  +-----------------------+  +--------------------+  +------+  |
|  |    ChatService     |  |  FileTransferService  |  |  TelemetryService  |  |Storage| |
|  | (UTF-8, Multibyte  |  | (TxManager, RxManager,|  | (Throughput, RTT,  |  |Service| |
|  |  Safe Truncate)    |  |  Stop-and-Wait ARQ)   |  |  Loss Rate, Sniffer|  | (JSON)| |
|  +--------------------+  +-----------------------+  +--------------------+  +------+  |
+------------------------------------------|--------------------------------------------+
                                           | Packets / Bytes
+------------------------------------------v--------------------------------------------+
|                             PROTOCOL & FRAMING LAYER                                  |
|  - Frame Structure: [SOF 2B] [SEQ 1B] [CMD 1B] [LEN 2B] [PAYLOAD NB] [CRC 4B] [EOF 2B]|
|  - framer.py: pack_frame(), parse_frames_with_stats(), IEEE 802.3 CRC-32 (zlib)       |
|  - constants.py: Opcodes (0x01..0x36), Timeouts (250ms), Max Retries (5), Delimiters  |
|  - file_payloads.py: FileStartPayload, FileChunkPayload, AckPayload, Extended Payloads|
+------------------------------------------|--------------------------------------------+
                                           | Raw Byte Streams / Thread Signals
+------------------------------------------v--------------------------------------------+
|                         CORE HARDWARE & TRANSPORT DRIVERS                             |
|  +---------------------------------------------------------------------------------+  |
|  | SerialManager (Main Thread Facade - src/ir_terminal/core/serial_manager.py)     |  |
|  +----------------------------------------|----------------------------------------+  |
|                                           | Cross-thread Signals (_sig_open/send)     |
|  +----------------------------------------v----------------------------------------+  |
|  | SerialWorker (Dedicated Background QThread - core/serial_worker.py)             |  |
|  |   - 100 Hz Hardware Read Polling (10ms QTimer)                                  |  |
|  |   - 1500 ms Port Hotplug Watcher (1.5s QTimer)                                  |  |
|  |   - 64 KB Rx Accumulation Buffer with Auto-Compaction & Overflow Protection     |  |
|  +----------------------------------------|----------------------------------------+  |
|                                           | Polymorphic ITransport Interface          |
|         +---------------------------------+---------------------------------+         |
|         |                                 |                                 |         |
|         v                                 v                                 v         |
|  PySerialTransport               SocketTransport                     MockSerialPort   |
| (pyserial wrapper)             (Virtual Link Broker)              (In-process loopback|
| Physical COM/UART              TCP 18881 / 18882 Dual-App          with LOS & Latency)|
+---------------------------------------------------------------------------------------+
```

### 1.1. Chi tiết các Tầng Thành phần

1. **Presentation Layer (Tầng Trình diễn):**
   - File: `src/ir_terminal/ui/main_window_web.py` và `src/ir_terminal/ui/backend_bridge.py`.
   - Sử dụng `QWebEngineView` nhúng Chromium engine với `QWebChannel`. Khi chạy chế độ 2 ứng dụng độc lập (`IR_INSTANCE=1` và `IR_INSTANCE=2`), mỗi ứng dụng được cấp một `QWebEngineProfile` cô lập hoàn toàn thư mục cache và local storage (`data/instance_1/webengine/...`).
   - `BackendBridge` đóng vai trò là "Cầu nối lưỡng cực" an toàn kiểu (Type-safe), công khai các `@Slot` cho JavaScript gọi và phát các `@Signal` truyền dữ liệu về giao diện thời gian thực.
2. **Application Services Layer (Tầng Dịch vụ Ứng dụng):**
   - **`ChatService` (`src/ir_terminal/services/chat_service.py`):** Xử lý gửi/nhận tin nhắn văn bản UTF-8. Tự động kiểm tra độ dài payload; nếu vượt quá 512 bytes (`MAX_PAYLOAD_SIZE`), hệ thống thực hiện thuật toán cắt chuỗi an toàn theo biên ký tự multibyte (tránh cắt đôi byte UTF-8 dẫn đến lỗi font).
   - **`FileTransferService` (`src/ir_terminal/services/file_transfer_service.py`):** Tuân thủ chặt chẽ nguyên lý Đơn trách nhiệm (Single Responsibility Principle - SRP), được mô đun hóa thành:
     - `TxManager`: Quản lý truyền file, đọc stream từ đĩa, FSM phát chunk, hẹn giờ chờ ACK (Stop-and-Wait).
     - `RxManager`: Nhận chunk, ghi trực tiếp vào file tạm `.part`, kiểm tra tính toàn vẹn và re-name nguyên tử sang file đích.
     - `StateCoordinator`: Quản lý chuyển trạng thái và đồng bộ đa luồng.
     - `ProtocolAdapter`: Đóng/mở gói tương thích ngược giữa chuẩn v1 (`uint16`) và chuẩn mở rộng Large-File (`uint32`/`uint64`).
   - **`TelemetryService` (`src/ir_terminal/services/telemetry_service.py`):** Tính toán thông lượng tức thời (Throughput KB/s) theo cửa sổ trượt 3 giây (`TELEMETRY_WINDOW_SEC = 3.0s`), đo RTT (Round Trip Time) bằng cặp gói tin PING/PONG, tính tỷ lệ mất gói lũy kế (Packet Loss Rate %) và lưu trữ 100 khung tin gần nhất vào ring buffer sniffer.
   - **`StorageService` (`src/ir_terminal/services/storage_service.py`):** Lưu trữ lịch sử chat, truyền file và cấu hình hệ thống ra các file JSON (`data/chat_history.json`, `data/transfer_history.json`, `data/settings.json`) với kỹ thuật ghi file nguyên tử (atomic rename qua file tạm).
3. **Protocol & Framing Layer (Tầng Giao thức Khung truyền):**
   - File: `src/ir_terminal/protocol/framer.py`, `packet.py`, `constants.py`, `file_payloads.py`.
   - Hiện thực hóa cấu trúc khung nhị phân chuẩn Little-Endian, kiểm tra tính toàn vẹn bằng CRC-32 (chuẩn IEEE 802.3 Ethernet). Khung truyền có độ dài cố định phần header là 6 bytes, footer là 6 bytes, payload tối đa 512 bytes (Tổng khung tối đa 524 bytes).
4. **Core Hardware & Transport Drivers (Tầng Trình điều khiển Phần cứng):**
   - **`ITransport` (`src/ir_terminal/core/interfaces.py`):** Lớp trừu tượng (Abstract Base Class) quy định hợp đồng cho mọi luồng I/O: `open()`, `close()`, `is_open`, `write(data)`, `read(size)`, `in_waiting`.
   - **`PySerialTransport` (`src/ir_terminal/core/pyserial_transport.py`):** Trình bao bọc trực tiếp thư viện `pyserial` để giao tiếp với cổng COM vật lý.
   - **`SocketTransport` (`src/ir_terminal/core/socket_transport.py`):** Giao tiếp TCP client kết nối với `LinkBroker` khi chạy chế độ giả lập 2 node ảo trên cùng máy tính (`LINK_A` -> TCP 18881, `LINK_B` -> TCP 18882).
   - **`MockSerialPort` (`src/ir_terminal/core/mock_serial.py`):** Giả lập cổng COM nội trình phục vụ kiểm thử đơn vị và tích hợp mà không cần phần cứng thật.
   - **`SerialWorker` & `SerialManager`:** Quản lý vòng lặp I/O trên luồng nền (Worker Thread), ngăn ngừa hoàn toàn hiện tượng treo đơ giao diện đồ họa chính khi I/O phần cứng bị nghẽn.

---

## 2. PHÂN TÍCH CHUYÊN SÂU LUỒNG KẾT NỐI PHẦN CỨNG CỔNG COM/UART

### 2.1. Cơ chế Quét và Liệt kê Cổng COM (COM Enumeration)
Quá trình rà quét cổng COM được thực thi trong hàm `scan_available_ports()` (`src/ir_terminal/core/serial_worker.py:499-522`):

```python
def scan_available_ports(include_link: bool | None = None) -> list[str]:
    ports = [p.device for p in list_ports.comports()]
    for extra in _windows_registry_ports():
        if extra not in ports:
            ports.append(extra)
    ports.append("MOCK_COM_LOOPBACK")
    link_enabled = is_dual_mock_enabled() if include_link is None else include_link
    if link_enabled:
        ports.extend(["LINK_A", "LINK_B"])
    return sorted(set(ports), key=_natural_port_key)
```

#### Phân tích chi tiết các kỹ thuật:
1. **Quét PnP qua `pyserial`:** Sử dụng `serial.tools.list_ports.comports()` để truy vấn API của hệ điều hành (trên Windows gọi qua `SetupAPI`, trên Linux đọc từ `/sys/class/tty/`).
2. **Cơ chế Fallback qua Windows Registry (`_windows_registry_ports()`):**
   - *Vấn đề thực tế:* Một số driver Virtual COM (như com0com, VSPE) hoặc driver USB-to-UART cũ/tùy biến không đăng ký GUID thiết bị cổng COM chuẩn trong Windows Device Manager, khiến `pyserial` bỏ sót.
   - *Giải pháp:* Hàm `_windows_registry_ports()` (`serial_worker.py:40-68`) trực tiếp mở khóa Registry:
     `HKEY_LOCAL_MACHINE\HARDWARE\DEVICEMAP\SERIALCOMM`
     Hàm duyệt toàn bộ các giá trị (values) tại khóa này để thu thập danh sách cổng COM (ví dụ: `COM1`, `COM3`, `COM10`).
3. **Thuật toán sắp xếp tự nhiên (`_natural_port_key`):**
   - Sắp xếp cổng COM theo thứ tự số học tự nhiên: `COM2` luôn đứng trước `COM10` (khắc phục lỗi sắp xếp chữ cái thông thường khiến `COM10` đứng trước `COM2`).

#### Đánh giá hạn chế kỹ thuật hiện tại:
- Hiện tại, danh sách cổng trả về cho UI (`scanPorts`) chỉ là mảng chuỗi tên cổng đơn thuần (ví dụ `["COM3", "COM4"]`).
- Chưa công khai thông tin chi tiết của phần cứng: **VID (Vendor ID)**, **PID (Product ID)**, **Mô tả thiết bị (Description/Friendly Name)**. Ví dụ: chip nạp ST-Link V2 có VID `0x0483` / PID `0x374B`, STM32 Virtual COM có VID `0x0483` / PID `0x5740`, CP2102 có VID `0x10C4` / PID `0xEA60`. Nếu người dùng cắm nhiều thiết bị USB, họ không phân biệt được đâu là cổng của STM32, đâu là chuột/bàn phím ảo nếu chỉ nhìn thấy `COM3`, `COM4`.

---

### 2.2. Quá trình Thiết lập Kết nối Phần cứng (Connection Establishment)

Khi người dùng chọn cổng COM và bấm "Connect" trên giao diện Web, luồng thực thi diễn ra như sau:
1. **Phát lệnh từ UI:** JavaScript gọi `bridge.connectPort("COM3", 115200)`.
2. **Ủy quyền qua Main Thread:** `BackendBridge.connectPort()` chuyển tiếp đến `SerialManager.connect_port("COM3", 115200)`.
3. **Bắn tín hiệu liên luồng (Cross-thread Signal):** `SerialManager` phát tín hiệu Qt nội bộ `_sig_open.emit(port_name, baudrate)`. Do `SerialWorker` đã được chuyển sang `QThread` riêng (`moveToThread`), cơ chế Qt Queued Connection đảm bảo việc mở cổng được thực hiện hoàn toàn trong luồng nền.
4. **Khởi tạo kết nối trong `SerialWorker.open_port()` (`src/ir_terminal/core/serial_worker.py:129-195`):**
   - Đầu tiên, gọi `self.close_port()` để dọn dẹp kết nối cũ nếu đang mở.
   - Gọi `create_transport(port_name, baudrate)` để khởi tạo đối tượng `PySerialTransport`.
   - Cấu hình thông số UART vật lý (`src/ir_terminal/core/pyserial_transport.py:39-47`):
     - **Baudrate:** Mặc định `115200 bps` (hỗ trợ các mức từ 9600 đến 230400).
     - **Data Bits:** `serial.EIGHTBITS` (8 bits dữ liệu).
     - **Parity:** `serial.PARITY_NONE` (Không dùng bit kiểm tra chẵn lẻ).
     - **Stop Bits:** `serial.STOPBITS_ONE` (1 bit dừng).
     - **Read Timeout:** `0.05s` (50 ms). Đảm bảo hàm đọc không bị block vĩnh viễn.
     - **Write Timeout:** `0.2s` (200 ms). Ngăn chặn treo phần mềm nếu bộ đệm truyền phần cứng bị đầy hoặc cáp UART bị nghẽn.
     - **Flow Control:** Không sử dụng phần cứng RTS/CTS hay phần mềm XON/XOFF.
   - Khởi tạo bộ đệm nhận `_rx_accumulation = bytearray()` và đối tượng thống kê khung `_framer_stats = FramerStats()`.
   - **Kích hoạt Polling Timer:** Thiết lập `_poll_timer = QTimer(self)` với chu kỳ `HW_POLL_MS = 10ms` (tần số lấy mẫu 100 Hz).
   - **Kích hoạt Hotplug Watcher:** Khởi động `_port_poll_timer` với chu kỳ `1500ms` để giám sát trạng thái cắm/rút thiết bị.
   - Phát tín hiệu `connected.emit(port_name, baudrate)`.

---

### 2.3. Cơ chế Xử lý Ngoại lệ Phần cứng (Hardware Exception Handling)

Hệ thống đã cài đặt cơ chế bắt lỗi phần cứng đa tầng tại `SerialWorker` và `PySerialTransport`:

```
                           +-------------------------------------+
                           | STM32 Board Unplugged / Cable Break |
                           +------------------|------------------+
                                              |
                                              v
                              PySerial read() / in_waiting
                                              |
                   Raises serial.SerialException / OSError (Errno 19)
                                              |
                                              v
                   +----------------------------------------------------+
                   | SerialWorker._poll_serial() Exception Block        |
                   | 1. Log error with LogTag.HW_SERIAL                 |
                   | 2. Emit error_occurred("Read error: ...")          |
                   | 3. Call close_port()                               |
                   | 4. Stop _poll_timer (10ms) & watcher               |
                   | 5. Emit disconnected()                             |
                   +--------------------------|-------------------------+
                                              |
                   +--------------------------v-------------------------+
                   | BackendBridge receives portDisconnected            |
                   | 1. Stop health check ping timer                    |
                   | 2. Notify Chromium UI -> Show red indicator        |
                   +----------------------------------------------------+
```

#### 1. Ngắt kết nối đột ngột (Hot Unplug / Device Removal):
- Khi board STM32 hoặc cáp USB-UART bị rút đột ngột trong khi đang kết nối, tại chu kỳ 10ms tiếp theo của `_poll_serial()`, việc truy cập `self._transport.in_waiting` hoặc `self._transport.read()` trên Windows sẽ ném ra ngoại lệ `serial.SerialException: ClearCommError failed (PermissionError(13, 'The device does not recognize the command.'))` hoặc trên Linux ném ra `OSError: [Errno 19] No such device`.
- Khối `try...except` tại `serial_worker.py:415-424` bắt trọn ngoại lệ này:
  - Ghi log nghiêm trọng: `logger.exception("[HW_SERIAL] Serial read poll error (%s: %s); closing port", ...)`
  - Phát tín hiệu: `self.error_occurred.emit(f"Read error: {exc}")`.
  - Gọi hàm dọn dẹp: `self.close_port()`.
  - Hàm `close_port()` hủy timer đọc 10ms, đóng cổng cấp driver, giải phóng bộ nhớ đệm và phát tín hiệu `disconnected.emit()`.
- Cùng lúc đó, timer giám sát hotplug `_poll_ports()` (chạy mỗi 1.5s) phát hiện cổng COM đó đã biến mất khỏi hệ thống, cập nhật lại `_last_ports` và phát `ports_changed.emit(current_ports)`, giúp dropdown trên UI tự động loại bỏ cổng bị rút.

#### 2. Xung đột Cổng COM và Lỗi Quyền Truy cập (PermissionError / Port Busy):
- Nếu cổng COM đang bị chiếm dụng bởi phần mềm khác (như STM32CubeIDE, Keil C, TeraTerm, hoặc một instance khác của app):
  - Lệnh `PySerialTransport.open()` thất bại và ném `serial.SerialException: could not open port 'COM3': PermissionError(13, 'Access is denied.')`.
  - Ngoại lệ được ghi nhận tại `PySerialTransport` và chuyển tiếp lên `SerialWorker.open_port()` (`serial_worker.py:196-206`).
  - Ghi log chi tiết: `[HW_SERIAL] Failed to open serial port COM3 @ 115200 bps (SerialException: ...)`.
  - Phát tín hiệu: `self.error_occurred.emit("Connection failed: ...")`. Giao diện Web hiển thị hộp thông báo lỗi ngay lập tức mà không gây crash ứng dụng.

#### 3. Chính sách Kết nối Lại Tự Động (Auto-Reconnect Policy):
- **Hiện trạng:** Hệ thống hiện chỉ hỗ trợ auto-connect một lần duy nhất lúc khởi động ứng dụng (`src/ir_terminal/app.py:222-234` qua `QTimer.singleShot(700, _auto_connect)`).
- **Điểm cần lưu ý:** Nếu cổng COM bị rút ra rồi cắm lại giữa chừng, hiện tại ứng dụng chưa tự động kết nối lại (Auto-reconnect loop) mà chỉ cập nhật lại danh sách cổng trên UI và yêu cầu người dùng bấm nút "Connect" thủ công.

---

## 3. PHÂN TÍCH TOÀN BỘ LUỒNG TRUYỀN NHẬN TÍN HIỆU (TX / RX DATA PATH)

### 3.1. Luồng Nhận Tín hiệu (RX Data Path: Từ Quang đến Màn hình UI)

```
[Ánh sáng hồng ngoại 880nm]
          |
          v
[TFDU4101 Optical Transceiver]  (Thu nhận xung photon, chuyển sang tín hiệu điện số)
          |
          v
[MCP2122 Bit Encoder/Decoder]   (Giải mã xung IrDA 3/16 bit width -> tín hiệu UART chuẩn NRZ)
          |
          v
[STM32F407 USART RX (DMA)]     (Nhận byte vật lý, đẩy qua USB Virtual COM Port / UART-USB)
          |
          v
[Driver OS & PySerialTransport] (Lưu vào kernel serial buffer)
          |
          |  (100 Hz Poll: PySerialTransport.read())
          v
[SerialWorker._poll_serial()]  (src/ir_terminal/core/serial_worker.py:375)
          |
          +---> raw_data_received.emit(chunk)  --> BackendBridge --> UI Hex Monitor
          |
          v
[SerialWorker._append_rx_chunk()] (Nạp vào bytearray; kiểm tra trần 64 KB; drop oldest nếu tràn)
          |
          v
[SerialWorker._drain_rx_buffer()] (Cắt memoryview unconsumed; phân tích khung bằng Framer)
          |
          v
[framer.parse_frames_with_stats()] (src/ir_terminal/protocol/framer.py:170)
  1. Quét tìm Delimiter SOF: 0x55 0xAA (0xAA55 LE)
  2. Đọc Header 6 bytes: [SOF (2B)] [SEQ (1B)] [OPCODE (1B)] [LEN (2B)]
  3. Kiểm tra trần LEN <= 512 bytes (Loại bỏ nhiễu SOF giả)
  4. Đọc Footer 6 bytes: [CRC32 (4B)] [EOF (2B)]
  5. Đối soát EOF: Phải là 0xAA 0x55 (0x55AA LE)
  6. Tính toán & Đối soát CRC-32 (IEEE 802.3 over SEQ..PAYLOAD)
          |
          +---> Nếu lỗi: Cập nhật FramerStats (crc_errors, eof_errors, noise_bytes)
          |              Bắn tín hiệu framer_error.emit(kind, count)
          |
          v (Nếu CRC & EOF Hợp lệ)
[Tạo đối tượng Packet] (src/ir_terminal/protocol/packet.py)
          |
          v
[SerialWorker.packet_received.emit(pkt)]
          |
          +=========================== ĐỊNH TUYẾN DỊCH VỤ ===========================+
          |                                                                           |
          v                                                                           v
[ChatService.handle_packet()]                                     [FileTransferService._handle_packet()]
(Nếu Opcode == 0x02 DATA_TEXT)                                    (Nếu Opcode == 0x01/0x35 DATA_CHUNK,
  - Giải mã UTF-8 payload                                           0x10 ACK, 0x11 NACK, 0x30 START, 0x31 END)
  - Fallback sang Hex nếu lỗi mã hóa                               - Định tuyến sang TxManager / RxManager
  - Lưu vào StorageService                                         - Cập nhật tiến độ truyền file
  - Phát message_received signal                                   - Lưu vào StorageService
          |                                                                           |
          +-------------------------------------+-------------------------------------+
                                                |
                                                v
                              [BackendBridge (Qt WebChannel)]
                                (src/ir_terminal/ui/backend_bridge.py)
                                - chatMessageReceived.emit()
                                - transferProgress.emit()
                                - rawRxData.emit()
                                - snifferFrameLogged.emit()
                                                |
                                                v
                              [Chromium WebEngine View (UI)]
                                - Hiển thị tin nhắn chat trên Web UI
                                - Cập nhật thanh tiến độ % và tốc độ KB/s
                                - Hiển thị bảng Sniffer và đồ thị Telemetry
```

#### Các chốt chặn kỹ thuật trọng yếu trong RX Path:
1. **Bảo vệ chống tràn bộ đệm (Buffer Overflow Protection - `serial_worker.py:274-292`):**
   - Bộ đệm tích lũy `_rx_accumulation` được giới hạn cứng tại `RING_BUFFER_CAPACITY = 65536 bytes` (64 KB).
   - Nếu luồng nhận dữ liệu đến dồn dập từ MCU mà luồng xử lý chưa kịp tiêu thụ, khi phần dữ liệu còn lại (`live = len(self._rx_accumulation) - self._rx_consumed`) vượt quá 64 KB, hệ thống sẽ thực hiện **Drop Oldest**: xóa đúng số lượng byte tràn từ đầu bộ đệm, tăng biến đếm `self._rx_dropped`, và ghi log cảnh báo:
     `[HW_SERIAL] RX accumulation overflow: dropped X oldest bytes (cap=65536B)`
   - Cơ chế này loại bỏ hoàn toàn nguy cơ rò rỉ bộ nhớ RAM (Memory Runaway).
2. **Kỹ thuật gom rác bộ đệm (Compaction Threshold - `serial_worker.py:303-306`):**
   - Việc gọi hàm `del bytearray[:N]` ở mỗi chu kỳ nhận 10ms sẽ gây hiện tượng di dời bộ nhớ liên tục (memory shift / `memmove`), làm suy giảm hiệu năng CPU.
   - Do đó, hệ thống sử dụng con trỏ `_rx_consumed`. Chỉ khi số byte đã xử lý vượt qua ngưỡng `RX_COMPACT_THRESHOLD = 4096 bytes`, thao tác cắt lát `del self._rx_accumulation[: self._rx_consumed]` mới được thực thi một lần.
3. **Cơ chế Memoryview Zero-Copy (`framer.py:196-199`, `serial_worker.py:296-302`):**
   - Hàm tách khung `parse_frames_with_stats()` nhận vào một `memoryview` của buffer thay vì sao chép chuỗi byte.
   - Trước khi thực hiện cắt lát mảng bytearray, đối tượng memoryview bắt buộc phải được giải phóng qua `pending.release()`. Điều này ngăn chặn triệt để ngoại lệ `BufferError: Existing exports of data: object cannot be re-sized` của Python.
4. **Thuật toán quét phục hồi Delimiter (Resynchronization):**
   - Khi tín hiệu quang bị nhiễu làm sai lệch byte, framer quét tìm cặp byte `0x55 0xAA` tiếp theo. Mọi byte rác đứng trước SOF được phân loại vào `noise_bytes` và ghi nhận trong telemetry. Nếu gặp độ dài gói `payload_len > 512`, framer nhận diện đây là SOF giả do nhiễu, ghi nhận `len_errors += 1`, dịch con trỏ qua 2 bytes và tiếp tục quét.

---

### 3.2. Luồng Gửi Tín hiệu (TX Data Path: Từ Thao tác UI ra Quang)

```
[Thao tác Người dùng trên UI Web] (Gửi chat text / Chọn file truyền / Bấm Ping)
          |
          v
[BackendBridge] (src/ir_terminal/ui/backend_bridge.py)
          |
          v
[ChatService.send_message()] hoặc [FileTransferService.start_transfer()]
          |
          v (Cắt nhỏ file thành các chunk 240B, gán sequence number)
[SerialManager.send_packet(seq, opcode, payload)] (src/ir_terminal/core/serial_manager.py:184)
          |
          v
[framer.pack_frame(seq, opcode, payload)] (src/ir_terminal/protocol/framer.py:66)
  1. Kiểm tra ràng buộc: payload <= 512B, seq in 0..255, opcode in 0..255
  2. Đóng gói Header: struct.pack("<HBBH", 0xAA55, seq, opcode, len(payload))
     (Little-Endian: SOF xuất ra dây UART là 0x55 rồi tới 0xAA)
  3. Tính toán CRC-32: zlib.crc32() trên vùng dữ liệu [SEQ .. PAYLOAD] (chuẩn IEEE 802.3)
  4. Đóng gói Footer: struct.pack("<IH", crc32, 0x55AA)
     (Little-Endian: EOF xuất ra dây UART là 0xAA rồi tới 0x55)
  5. Ghép nối nhị phân: Frame hoàn chỉnh = SOF + Header Tail + Payload + Footer
          |
          v
[SerialManager.send_raw(frame)]
          |
          v (Phát tín hiệu liên luồng Qt _sig_send)
[SerialWorker.send_bytes(data)] (Chạy trong Worker QThread - serial_worker.py:342)
          |
          v
[PySerialTransport.write(data)] (pyserial wrapper - pyserial_transport.py:91)
          |
          v
[serial.Serial.write(data)] (Ghi vào driver phần cứng cổng COM, write_timeout=0.2s)
          |
          v
[Cáp USB-to-UART / STM32F407 USART TX] (Chuyển byte thành chuỗi xung điện áp NRZ)
          |
          v
[Microchip MCP2122 Encoder] (Mã hóa xung bit rộng 3/16 thời gian bit ở baud 115200)
          |
          v
[Vishay TFDU4101 Transceiver] (Phát xung photon quang hồng ngoại bước sóng 880nm ra không gian)
```

---

## 4. CƠ CHẾ ĐIỀU KHIỂN LUỒNG STOP-AND-WAIT ARQ & BẢO TOÀN DỮ LIỆU

Hệ thống truyền file quang hồng ngoại áp dụng cơ chế **Stop-and-Wait ARQ (Automatic Repeat reQuest)** kết hợp cửa sổ trượt kích thước $W = 1$. Đây là thiết kế tối ưu tuyệt đối cho kênh truyền vật lý quang bán song công (Half-Duplex) nhằm tránh việc phát và nhận đồng thời gây phản xạ quang (optical echo / self-reception) làm nhiễu đầu thu TFDU4101.

```
       TRANSMITTER (TX)                                   RECEIVER (RX)
              |                                                 |
              |----------- 1. TRANSFER_START (seq=0) ---------->|
              |<---------- 2. ACK (acked_seq=0, OK) ------------|
              |                                                 |
              |----------- 3. DATA_CHUNK #0 (seq=1) ----------->|
              |<---------- 4. ACK (acked_seq=1, OK) ------------|
              |                                                 |
              |--- 5. DATA_CHUNK #1 (seq=2) ---> [LỖI QUANG / MẤT GÓI]
              |    (Hẹn giờ ACK Timeout 250ms chạy...)          |
              |    [Timeout! Retry #1/5]                        |
              |                                                 |
              |--- 6. Retransmit CHUNK #1 (seq=2) ------------->|
              |<-- 7. ACK (acked_seq=2, OK) -------------------| (RX phát hiện đúng seq -> Ghi đĩa)
              |                                                 |
              |--- 8. DATA_CHUNK #2 (seq=3) ------------------->|
              |    [MẤT ACK TRÊN ĐƯỜNG VỀ] <--- ACK (seq=3) ---|
              |    [Timeout! Retry #1/5]                        |
              |                                                 |
              |--- 9. Retransmit CHUNK #2 (seq=3) ------------->|
              |                                                 | (RX thấy chunk_id < expected:
              |<-- 10. Re-ACK (acked_seq=3, OK) ----------------|  KHÔNG ghi trùng đĩa, chỉ gửi lại ACK)
              |                                                 |
              |----------- 11. TRANSFER_END (seq=4) ----------->| (So khớp CRC toàn file)
              |<---------- 12. ACK (acked_seq=4, OK) -----------|
              |                                                 |
       [TX COMPLETED]                                    [RX COMPLETED]
```

### 4.1. Máy Trạng Thái FSM Truyền File (Finite State Machine)
Các trạng thái truyền file được quản lý tại `TransferState` (`src/ir_terminal/services/file_transfer/state_coordinator.py`):
1. **`IDLE`:** Trạng thái nghỉ, sẵn sàng nhận phiên mới.
2. **`HANDSHAKING`:** TX gửi gói `TRANSFER_START` chứa siêu dữ liệu (Metadata: tên file, kích thước, tổng số chunk, CRC-32 toàn file). Bật timer chờ ACK 250ms.
3. **`TRANSFERRING`:** TX đọc tuần tự từng chunk từ đĩa (`read_at`), đóng gói `DATA_CHUNK`, phát ra UART và chờ ACK tương ứng.
4. **`FINALIZING`:** Khi toàn bộ chunk đã gửi xong, TX phát gói `TRANSFER_END` mang mã băm CRC-32 toàn file.
5. **`COMPLETED`:** Nhận được ACK xác thực của `TRANSFER_END`. Lưu lịch sử truyền và đóng phiên thành công.
6. **`FAILED` / `ABORTED`:** Xảy ra lỗi quá số lần thử lại (Max retries exceeded) hoặc người dùng chủ động bấm Hủy. File tạm `.part` bên phía RX sẽ bị xóa an toàn.
7. **`RECEIVING`:** Trạng thái bên phía trạm nhận (RX), đón nhận các chunk và ghi tuần tự vào đĩa.

### 4.2. Các Tham số Kỹ thuật & Thuật toán Xử lý Ngoại lệ ARQ

| Tham số | Giá trị | File định nghĩa | Giải thích kỹ thuật |
| :--- | :--- | :--- | :--- |
| `DEFAULT_CHUNK_SIZE` | **240 Bytes** | `protocol/constants.py:46` | Ràng buộc báo giá MVP. Cân bằng tối ưu giữa thông lượng và độ ổn định kênh quang 115200 bps. |
| `MAX_PAYLOAD_SIZE` | **512 Bytes** | `protocol/constants.py:28` | Giới hạn phần cứng tránh tràn buffer SRAM của STM32F407. |
| `DEFAULT_ACK_TIMEOUT_SEC`| **0.250 s (250 ms)** | `protocol/constants.py:109` | Thời gian tối đa chờ phản hồi ACK/NACK trước khi kích hoạt gửi lại. |
| `MAX_RETRY_COUNT` | **5 lần** | `protocol/constants.py:112` | Số lần gửi lại tối đa cho mỗi chunk trước khi kết luận đứt liên kết quang (LOS Blockage). |

#### 1. Quy tắc Xác thực ACK / NACK:
- Khi nhận được gói ACK (`Opcode.ACK`), `TxManager._on_ack_received()` (`tx_manager.py:339-483`) giải mã `AckPayload` lấy `acked_seq` và `status`:
  - **Kiểm tra Sequence:** `acked_seq` phải bằng chính xác `self._seq` của gói tin vừa phát đi. Nếu `acked_seq != self._seq`, hệ thống coi đây là **Stale ACK** (ACK trễ từ phiên/chu kỳ trước) và hủy bỏ, không tăng đếm chunk.
  - **Kiểm tra Status:** Nếu status khác `AckStatus.OK` (ví dụ `CRC_ERROR`, `OUT_OF_ORDER`), coi như nhận NACK, tăng biến đếm `_retry_count` và gọi `_retransmit()`.
  - **Hết lượt Retry:** Nếu `_retry_count > MAX_RETRY_COUNT (5)`, gọi `_fail_tx("Transfer failed: Remote error status after 5 retries")`.

#### 2. Xử lý Mất ACK trên Kênh truyền (Lost ACK Handling & Idempotency):
- *Tình huống:* TX gửi Chunk #2 thành công, RX đã nhận và ghi đĩa rồi gửi `ACK(seq=3)`. Tuy nhiên, gói ACK bị nhiễu quang làm biến dạng trên đường về.
- *Phản ứng của TX:* Hết 250ms, TX không nhận được ACK hợp lệ -> Kích hoạt timeout -> Gửi lại Chunk #2 với cùng `seq=3`.
- *Phản ứng của RX (`rx_manager.py:430-442`):*
  ```python
  if chunk_id < service._rx_expected_chunk:
      ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OK)
      service._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.ACK, payload=ack.pack())
      return
  ```
  RX phát hiện `chunk_id < expected_chunk`, lập tức hiểu rằng gói ACK trước đó của mình đã bị thất lạc. RX **không ghi trùng dữ liệu vào đĩa**, mà chỉ gửi lại ngay lập tức một gói `ACK(pkt.seq, OK)`. Điều này đảm bảo tính toàn vẹn (idempotency) tuyệt đối của file.

#### 3. Chống Lệch Chunk (Out-of-Order Handling):
- Nếu RX nhận được `chunk_id > expected_chunk` (do bị rớt mất một chunk ở giữa), RX không bao giờ ghi đĩa mà gửi ngay `NACK` với trạng thái `AckStatus.OUT_OF_ORDER` (`rx_manager.py:444-454`), yêu cầu TX gửi lại đúng chunk còn thiếu.

#### 4. Large-File Protocol Extension (`0x34 / 0x35 / 0x36`):
- Chuẩn giao thức gốc (Legacy v1) sử dụng trường `total_chunks` kiểu `uint16`, dẫn đến giới hạn cứng $65.535 \text{ chunks} \times 240\text{B} \approx 15.7\text{ MB}$.
- Hệ thống đã tích hợp sẵn cơ chế tự động nâng cấp: Khi file cần nhiều hơn 65.535 chunk hoặc dung lượng vượt quá 4 GB, hệ thống tự động kích hoạt **Large-File Extension**:
  - `TRANSFER_START_LARGE (0x34)`: `file_size` mở rộng lên `uint64` (8 bytes), `total_chunks` lên `uint32` (4 bytes).
  - `DATA_CHUNK_LARGE (0x35)`: `chunk_id` mở rộng lên `uint32` (4 bytes, chunk data tối đa 508B).
  - `TRANSFER_END_LARGE (0x36)`: `total_bytes` mở rộng lên `uint64` (8 bytes).
  - Nâng giới hạn lý thuyết của hệ thống lên **2.18 TB**, giải phóng hoàn toàn giới hạn dung lượng phần mềm.

---

## 5. RÀ SOÁT HỆ THỐNG LOG & NĂNG LỰC DEBUG/THU THẬP LỖI

### 5.1. Bảng Tra cứu Subsystem LogTag Chuyên Dụng Cho IoT

Toàn bộ hệ thống áp dụng các thẻ phân loại subsystem chuẩn hóa tại `LogTag` (`src/ir_terminal/utils/logger.py:17-28`):

| LogTag | Tên Subsystem | Phạm vi Theo dõi & Chẩn đoán Lỗi |
| :--- | :--- | :--- |
| `[HW_SERIAL]` | Physical Hardware Serial | Khởi tạo cổng COM, quét cổng, đóng/mở port, baudrate, ngoại lệ USB cắm/rút, tràn bộ đệm nhận `rx_dropped`. |
| `[OPTICAL_PHY]`| Optical Physical Channel | Đo đạc tầng vật lý quang: RTT, RSSI suy diễn, chắn tia Direct LOS, thống kê tỷ lệ rớt gói, đo thông lượng KB/s. |
| `[PROTO_FRAME]`| Protocol Framing Engine | Đóng/mở khung nhị phân, quét SOF, kiểm tra EOF magic, kiểm tra độ dài LEN, và **đối soát checksum CRC-32**. |
| `[ARQ_ENGINE]` | Stop-and-Wait ARQ FSM | Máy trạng thái Stop-and-Wait, gửi/nhận ACK/NACK, hẹn giờ timeout 250ms, bộ đếm thử lại Retry count (1..5). |
| `[CHAT_SVC]`   | Optical Chat Service | Mã hóa/giải mã tin nhắn UTF-8, thuật toán cắt ngắn multibyte, thanh tra khung chat (Chat Inspector). |
| `[FILE_SVC]`   | File Transfer Service | Quản lý phiên file, phân mảnh chunk, I/O đĩa, quản lý file tạm `.part`, kiểm tra toàn vẹn CRC-32 toàn file. |
| `[STORAGE]`    | Persistence Storage | Lưu trữ dữ liệu lịch sử chat, transfer, cấu hình hệ thống ra đĩa JSON với kỹ thuật atomic save. |
| `[IPC_BRIDGE]` | QWebChannel IPC Bridge | Cầu nối giao tiếp hai chiều giữa Chromium JavaScript và PySide6 Backend, timer phát xung PING kiểm tra liên kết. |

---

### 5.2. Vị trí Lưu Trữ và Định dạng Log File

- **Vị trí tệp Log:**
  - Chế độ đơn (Single mode): `data/logs/app.log`
  - Chế độ 2 node (Dual-Instance): `data/instance_1/logs/app.log` và `data/instance_2/logs/app.log`
- **Cấu hình Rotation:** Sử dụng `RotatingFileHandler` với dung lượng tối đa **10 MB** mỗi file, lưu trữ tối đa **5 file backup** (`app.log.1` đến `app.log.5`), đảm bảo dung lượng lưu trữ log tối đa 60 MB, không gây tràn đĩa cứng máy tính.
- **Định dạng Log Chuẩn Hóa:**
  ```text
  [%(asctime)s.%(msecs)03d] [%(levelname)-7s] [%(threadName)s] [%(name)s:%(funcName)s:%(lineno)d] %(message)s
  ```
  *Ví dụ mẫu log thực tế:*
  ```text
  [2026-10-21 14:02:15.123] [INFO   ] [QThread:worker] [ir_terminal.core.serial_worker:open_port:186] [HW_SERIAL] Port OPENED: COM3 @ 115200 bps via PySerialTransport poll=10ms rx_cap=65536B
  [2026-10-21 14:02:18.456] [DEBUG  ] [MainThread    ] [ir_terminal.services.telemetry_service:record_rtt:330] [OPTICAL_PHY] RTT update 12.4ms -> 8.2ms (raw=8.18ms)
  [2026-10-21 14:02:20.789] [WARNING] [QThread:worker] [ir_terminal.protocol.framer:parse_frames_with_stats:314] [PROTO_FRAME] RX crc_error @offset=0: rx=0x12345678 expected=0x9ABCDEF0 | seq=1 opcode=DATA_CHUNK(0x01) len=242B crc=0x12345678 hex=55 AA 01 01 F2 00 ... (len=254B, shown=32B)
  ```

---

### 5.3. Năng lực Dump Raw Hex Bytes và Đo đạc Chất lượng Kênh truyền

1. **Khả năng Dump Raw Hex Bytes Khi Lỗi:**
   - Hệ thống được trang bị hàm tiện ích `format_hex_preview(data, max_bytes=32)` (`src/ir_terminal/utils/logger.py:88-109`).
   - **Khi lỗi CRC (`crc_error`):** Toàn bộ frame bị lỗi được in ra log dưới dạng mã Hex viết hoa, kèm theo offset, giá trị CRC nhận được (`rx_crc`) và CRC kỳ vọng tính toán được (`expected_crc`).
   - **Khi lỗi EOF (`eof_error`):** Ghi rõ mã 2 byte EOF nhận được so với mã chuẩn `0x55AA`.
   - **Khi lỗi LEN (`len_error`):** Dump 8 bytes đầu của header để kỹ sư xem xét liệu có phải do nhiễu kích hoạt SOF giả mạo hay không.
   - **Khi xuất hiện nhiễu (`noise_bytes`):** Dump tối đa 16 byte rác bị loại bỏ trước khi gặp SOF.
2. **Năng lực Đo đạc Chất lượng Kênh truyền (Throughput, RTT, Packet Loss):**
   - **Đo RTT thời gian thực:** Định kỳ 5 giây một lần (`DEFAULT_HEALTH_CHECK_SEC = 5.0s`), `BackendBridge` phát một gói `PING` siêu nhẹ (`0x20`). Khi trạm đối diện phản hồi `PONG` (`0x21`), hệ thống tính toán chính xác độ trễ khứ hồi RTT tính bằng mili-giây.
   - **Đo Thông lượng (Throughput):** Tính toán tốc độ truyền nhận tức thời dựa trên số byte ghi nhận trong cửa sổ trượt 3.0 giây.
   - **Đo Tỷ lệ Mất gói (Packet Loss Rate %):** Công thức tính toán:
     $$\text{Loss Rate} = \frac{\text{Total Packets Lost}}{\text{Total Packets Sent} + \text{Total Packets Lost}} \times 100\%$$
     Trong đó, `Total Packets Lost` bao gồm cả **lỗi hỏng khung ở tầng Framer** (`crc_errors`, `eof_errors`) và **lỗi timeout phải gửi lại ở tầng ARQ** (`record_packet_lost()`).
   - **Đánh giá về RSSI/SNR:** Phần cứng quang TFDU4101 + MCP2122 là mô-đun truyền nhận số (chỉ xuất tín hiệu mức logic 0 và 1 ra chân RXD của STM32), không có mạch đo điện áp tương tự (Analog RSSI) đưa vào chân ADC của vi điều khiển. Do đó, phần mềm **suy diễn chất lượng kênh truyền vật lý thông qua các chỉ số phần mềm** (Loss Rate, Tần suất lỗi CRC, Jitter của RTT, và số byte nhiễu `noise_bytes`).

---

## 6. CẨM NANG CÔ LẬP LỖI PHẦN CỨNG & TROUBLESHOOTING CHECKLIST CHO TEAM IOT

Để giúp Đội ngũ Phần cứng / IoT STM32 và Kỹ thuật viên vận hành cô lập nguyên nhân sự cố trong thời gian nhanh nhất (< 5 phút), quy trình chẩn đoán được tiêu chuẩn hóa theo ma trận 4 tầng bên dưới:

### 6.1. Ma trận Cô lập Nguyên nhân Lỗi (Fault Isolation Matrix)

```
                            SỰ CỐ XẢY RA TRÊN HỆ THỐNG
                                        |
       +--------------------------------+--------------------------------+
       |                                                                 |
[Cổng COM không mở được / Lỗi I/O]                             [Kết nối COM OK nhưng không gửi/nhận được]
       |                                                                 |
       v                                                                 v
+-------------------------------+                               +-------------------------------+
| TẦNG 1: LỖI CÁP & CỔNG COM    |                               | Xem xét log có hiện thẻ:      |
| - PermissionError (13)        |                               | [PROTO_FRAME] hay [ARQ_ENGINE]|
| - Device removal / Unplugged  |                               +---------------+---------------+
| - Driver thiếu / COM ảo       |                                               |
+-------------------------------+                       +-----------------------+-----------------------+
                                                        |                                               |
                                                        v                                               v
                                        +-------------------------------+               +-------------------------------+
                                        | TẦNG 2: KÊNH QUANG IR         |               | TẦNG 3: FIRMWARE STM32/MCP2122|
                                        | - Log: noise_bytes tăng cao   |               | - Log: Timeout 250ms liên tục |
                                        | - Log: crc_error / eof_error  |               | - Không thấy bất kỳ byte nào  |
                                        | - Lệch góc phát TFDU4101      |               | - Sai xung 3/16 bit width     |
                                        | - Ánh sáng đèn huỳnh quang    |               | - Tràn bộ đệm DMA/UART MCU    |
                                        | - Khoảng cách vượt quá 1 mét  |               | - Đảo chân TX/RX              |
                                        +-------------------------------+               +-------------------------------+
```

---

### 6.2. Hướng dẫn Chẩn đoán Chi tiết Từng Tầng

#### TẦNG 1: Lỗi Cáp Vật lý, Driver & Cổng COM Máy tính
* **Hiện tượng:** Dropdown không có cổng COM, hoặc bấm "Connect" báo `Connection failed: PermissionError(13, 'Access is denied.')`.
* **Dấu hiệu trong Log (`[HW_SERIAL]`):**
  - `Failed to open serial port COMx ... PermissionError`
  - `Serial read poll error ... ClearCommError failed`
* **Quy trình xử lý:**
  1. Kiểm tra xem có phần mềm nào khác đang chiếm cổng COM không (STM32CubeIDE, TeraTerm, PuTTY, Arduino IDE). Đóng tất cả các app này.
  2. Mở `Device Manager` (Windows) -> mục `Ports (COM & LPT)`. Kiểm tra xem thiết bị có dấu chấm than vàng (`Code 10` hoặc `Code 28`) không. Nếu có, cài lại driver STM32 VCP hoặc driver chip nạp CH340/CP2102.
  3. Rút cáp USB ra và cắm lại sang cổng USB 3.0 / USB phía sau case máy tính (tránh cắm qua USB Hub thụ động thiếu nguồn).

#### TẦNG 2: Lỗi Kênh truyền Quang Hồng ngoại (Optical Alignment & Channel)
* **Hiện tượng:** Cổng COM mở thành công, nhưng gửi file bị `ACK timeout 250ms (retry 1/5..5/5)` rồi báo `FAILED`. Tỷ lệ Loss rate vọt lên > 50%.
* **Dấu hiệu trong Log (`[PROTO_FRAME]` & `[OPTICAL_PHY]`):**
  - `[PROTO_FRAME] Framer crc_errors xN (scanned=... parsed=...)`
  - `[PROTO_FRAME] RX skipped N noise byte(s) before SOF`
  - `[OPTICAL_PHY] Optical packet drop/retry #N (loss=...%)`
* **Quy trình xử lý:**
  1. **Căn chỉnh quang trục (Optical Alignment):** Đảm bảo mắt phát và mắt thu TFDU4101 của 2 node đối diện trực tiếp nhau trên một đường thẳng (Direct Line-of-Sight - LOS). Góc lệch quang của TFDU4101 là $\pm 15^\circ$; nếu góc lệch lớn hơn, cường độ quang sẽ suy hao theo hàm cosin và không kích hoạt được bộ khuếch đại quang.
  2. **Kiểm tra vật cản (Beam Obstruction):** Đảm bảo không có vật cản che chắn giữa 2 mắt quang.
  3. **Kiểm tra nhiễu ánh sáng môi trường (Ambient Light Interference):** Đèn huỳnh quang chấn lưu cũ hoặc ánh sáng mặt trời chiếu trực tiếp chứa quang phổ hồng ngoại mạnh có thể làm bão hòa photodiode của TFDU4101. Thử che chắn tối xung quanh mô-đun để kiểm tra.
  4. **Kiểm tra khoảng cách truyền:** Khoảng cách tối ưu cho TFDU4101 ở cấu hình chuẩn là $20\text{ cm} - 100\text{ cm}$. Nếu đặt quá xa (> 1.5m), công suất quang không đủ; nếu đặt quá gần (< 5cm), tín hiệu quang có thể bị bão hòa (optical saturation / blinding).

#### TẦNG 3: Lỗi Phần cứng & Firmware STM32F407 / MCP2122
* **Hiện tượng:** TX báo gửi thành công nhưng RX hoàn toàn không có phản hồi nào, không có cả log `noise_bytes` hay `raw_rx`.
* **Dấu hiệu trong Log (`[ARQ_ENGINE]`):**
  - TX liên tục gặp `ACK timeout 250ms` ngay ở gói `seq=0` (`TRANSFER_START`).
  - Phía RX không xuất hiện bất kỳ dòng log `[PROTO_FRAME] RX packet` nào.
* **Quy trình xử lý đối với Kỹ sư Firmware STM32:**
  1. **Kiểm tra cấu hình xung IrDA của MCP2122:**
     - MCP2122 yêu cầu xung nhịp chuẩn để tạo độ rộng xung quang bằng **$3/16$ chu kỳ bit**. Ở tốc độ `115200 bps`, một chu kỳ bit là $T = 8.68\ \mu\text{s}$, do đó độ rộng xung quang phát ra bắt buộc phải là $1.63\ \mu\text{s}$.
     - Kiểm tra chân cấp xung `16XCLK` vào MCP2122 (phải có tần số $16 \times 115200 = 1.8432\text{ MHz}$). Nếu chân clock này bị sai tần số, toàn bộ dữ liệu quang phát ra sẽ bị méo dạng và đầu thu không thể giải mã thành byte UART chuẩn.
  2. **Kiểm tra đảo chân USART TX/RX:**
     - Chân TX của STM32 nối vào chân `TXD` của MCP2122; chân `RXD` của MCP2122 nối vào chân RX của STM32.
  3. **Kiểm tra Buffer Overflow trên STM32:**
     - STM32F407 cần sử dụng ngắt UART kết hợp bộ đệm vòng (Ring Buffer) hoặc tốt nhất là cấu hình **DMA UART (Direct Memory Access) ở chế độ Circular Buffer**. Nếu firmware đọc UART từng byte bằng ngắt thông thường (`USART_IT_RXNE`) với độ ưu tiên ngắt thấp, khi máy tính bắn luồng chunk liên tục, STM32 sẽ bị lỗi tràn phần cứng `ORE (Overrun Error)` và làm mất byte dữ liệu.
  4. **Đối soát thuật toán CRC-32:**
     - Thuật toán CRC-32 của Desktop tính từ trường `SEQ` qua hết trường `PAYLOAD` (không tính 2 byte SOF), sử dụng đa thức IEEE 802.3 `0xEDB88320`. Nếu firmware STM32 tự tính CRC tính cả 2 byte SOF, mã CRC sẽ không bao giờ khớp.

#### TẦNG 4: Lỗi Phần mềm Desktop PySide6 / Giao diện WebEngine
* **Hiện tượng:** File chuyển sang trạng thái `FAILED` với thông báo `Receive file handle is closed` hoặc `PermissionError`.
* **Dấu hiệu trong Log (`[FILE_SVC]` & `[STORAGE]`):**
  - `Failed to write file chunk: ...`
  - `File size mismatch: got=... expected=...`
* **Quy trình xử lý:**
  1. Kiểm tra quyền ghi vào thư mục tải về (`data/transfers` hoặc thư mục tùy chỉnh của người dùng).
  2. Kiểm tra dung lượng ổ đĩa cứng máy tính còn trống hay không.
  3. Trong trường hợp giao diện Web bị đơ, mở Chromium DevTools bằng cách khởi chạy ứng dụng với biến môi trường `QTWEBENGINE_REMOTE_DEBUGGING=9222` và truy cập `http://localhost:9222` trên trình duyệt Chrome để xem log console JavaScript.

---

### 6.3. Bảng Kiểm Tra Nhanh Khi Đi Lab (Quick Inspection Checklist)

In bảng này dán tại bàn thí nghiệm để kiểm tra trước mỗi buổi đo đạc:

- [ ] **Bước 1 (Nguồn & Cáp):** Board STM32 sáng đèn nguồn LED đỏ; cáp Micro-USB/Type-C cắm chắc chắn vào máy tính.
- [ ] **Bước 2 (Cổng COM):** Mở Device Manager, ghi nhận đúng số hiệu cổng COM (ví dụ: `COM5`).
- [ ] **Bước 3 (Khởi chạy App):** Mở SpectraLink Desktop, chọn đúng `COM5`, chọn Baudrate `115200`, bấm **Connect** -> Thanh trạng thái chuyển sang màu xanh lá (`Connected: COM5 @ 115200`).
- [ ] **Bước 4 (Căn chỉnh quang):** Đặt 2 trạm đối diện thẳng hàng, khoảng cách từ 30 cm đến 50 cm.
- [ ] **Bước 5 (Kiểm tra Ping/RTT):** Bấm nút **Ping Link** trên giao diện -> Quan sát giá trị RTT hiển thị (mức chuẩn: $10\text{ ms} - 30\text{ ms}$). Nếu Ping trả về thành công nghĩa là kênh quang 2 chiều đã thông suốt!
- [ ] **Bước 6 (Test Chat Text):** Gửi một tin nhắn "Hello IR" -> Trạm bên kia nhận đúng văn bản, bảng Sniffer ghi nhận gói opcode `0x02 DATA_TEXT`.
- [ ] **Bước 7 (Truyền File):** Chọn file mẫu dung lượng nhỏ (< 1 MB), bấm **Send File** -> Thanh tiến độ chạy từ 0% đến 100%, trạng thái chuyển sang `COMPLETED`, kiểm tra mã CRC-32 trùng khớp.

---
*Tài liệu được tổng hợp và xuất bản tự động phục vụ công tác chuẩn hóa kỹ thuật và vận hành phòng Lab Truyền thông Quang Hồng ngoại.*
