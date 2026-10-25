# KẾ HOẠCH NÂNG CẤP KIẾN TRÚC DUAL COM PORT (TX/RX TÁCH BIỆT), DYNAMIC BAUDRATE VÀ TỐI GIẢN HÓA GIAO DIỆN KỸ THUẬT HIỆN ĐẠI

> **Mã kế hoạch:** PLAN-20261025-HARDWARE-DUAL-COM-UI  
> **Ngày lập:** 25/10/2026  
> **Dự án:** Trạm Thu Phát Quang Không Dây Hồng Ngoại (SpectraLink IR-OWC Workstation)  
> **Tài liệu mục tiêu:** `docs/plans/2026-10-25_HARDWARE_DUAL_COM_AND_UI_UPDATE_PLAN.md`  
> **Đối tượng:** Software Engineering Team, IoT Hardware & Firmware STM32 Team, QA/Test Automation Engineers.  

---

## MỤC LỤC
1. [Bối Cảnh & Mục Tiêu Nâng Cấp](#1-bối-cảnh--mục-tiêu-nâng-cấp)
2. [Phân Tích Chuyên Sâu Yêu Cầu 1: Kiến Trúc Dual COM Port (TX / RX Tách Biệt)](#2-phân-tích-chuyên-sâu-yêu-cầu-1-kiến-trúc-dual-com-port-tx--rx-tách-biệt)
   - 2.1. [Bản chất Vật lý & Luồng Tín Hiệu Quang Học](#21-bản-chất-vật-lý--luồng-tín-hiệu-quang-học)
   - 2.2. [Kịch Bản Triển Khai: Dual Node trên 1 Máy vs. 2 Máy Độc Lập](#22-kịch-bản-triển-khai-dual-node-trên-1-máy-vs-2-máy-độc-lập)
   - 2.3. [Tái Thiết Kế Tầng Giao Vận (Transport Layer) & Lớp `DualSerialTransport`](#23-tái-thiết-kế-tầng-giao-vận-transport-layer--lớp-dualserialtransport)
   - 2.4. [Nâng Cấp `SerialWorker` & `SerialManager` (Thread I/O & Signal Flow)](#24-nâng-cấp-serialworker--serialmanager-thread-io--signal-flow)
   - 2.5. [Nâng Cấp `BackendBridge` & IPC WebChannel](#25-nâng-cấp-backendbridge--ipc-webchannel)
   - 2.6. [Cấu Hình Khởi Chạy `LaunchConfig` & CLI Arguments](#26-cấu-hình-khởi-chạy-launchconfig--cli-arguments)
3. [Phân Tích Chuyên Sâu Yêu Cầu 2: Cấu Hình Baudrate Dạng Mảng / Danh Sách Động](#3-phân-tích-chuyên-sâu-yêu-cầu-2-cấu-hình-baudrate-dạng-mảng--danh-sách-động)
   - 3.1. [Bối Cảnh Kỹ Thuật: Switch Gạt Baudrate Phần Cứng Trên Board IoT](#31-bối-cảnh-kỹ-thuật-switch-gạt-baudrate-phần-cứng-trên-board-iot)
   - 3.2. [Mô Hình Dữ Liệu & Khả Năng Mở Rộng Danh Sách Baudrate](#32-mô-hình-dữ-liệu--khả-năng-mở-rộng-danh-sách-baudrate)
   - 3.3. [Cơ Chế Đồng Bộ Giữa Backend và UI Controller](#33-cơ-chế-đồng-bộ-giữa-backend-và-ui-controller)
4. [Phân Tích Chuyên Sâu Yêu Cầu 3: Rà Soát & Tối Giản Hóa Giao Diện Kỹ Thuật Hiện Đại](#4-phân-tích-chuyên-sâu-yêu-cầu-3-rà-soát--tối-giản-hóa-giao-diện-kỹ-thuật-hiện-đại)
   - 4.1. [Audit Hiện Trạng Giao Diện & Điểm Gây Nhiễu Thị Giác (Visual Noise)](#41-audit-hiện-trạng-giao-diện--điểm-gây-nhiễu-thị-giác-visual-noise)
   - 4.2. [Định Hình Phong Cách Giao Diện Kỹ Thuật (Modern Technical Instrument)](#42-định-hình-phong-cách-giao-diện-kỹ-thuật-modern-technical-instrument)
   - 4.3. [Kế Hoạch Tinh Chỉnh Chi Tiết Cho Từng View](#43-kế-hoạch-tinh-chỉnh-chi-tiết-cho-từng-view)
5. [Kế Hoạch Triển Khai Từng Bước (Step-by-Step Implementation Plan)](#5-kế-hoạch-triển-khai-từng-bước-step-by-step-implementation-plan)
   - 5.1. [Giai Đoạn 1: Core Transport Layer & `DualSerialTransport`](#51-giai-đoạn-1-core-transport-layer--dualserialtransport)
   - 5.2. [Giai Đoạn 2: Configuration & Dynamic Baudrate Management](#52-giai-đoạn-2-configuration--dynamic-baudrate-management)
   - 5.3. [Giai Đoạn 3: Backend Bridge & Signal Routing](#53-giai-đoạn-3-backend-bridge--signal-routing)
   - 5.4. [Giai Đoạn 4: Web UI Modernization & Dual COM Connect Component](#54-giai-đoạn-4-web-ui-modernization--dual-com-connect-component)
   - 5.5. [Giai Đoạn 5: Launcher Scripts, Mock Simulation & Integration Tests](#55-giai-đoạn-5-launcher-scripts-mock-simulation--integration-tests)
6. [Chiến Lược Kiểm Thử & Tiêu Chí Nghiệm Thu (Acceptance Criteria)](#6-chiến-lược-kiểm-thử--tiêu-chí-nghiệm-thu-acceptance-criteria)
7. [Quản Trị Rủi Ro & Cơ Chế Khôi Phục (Risk Mitigation & Rollback)](#7-quản-trị-rủi-ro--cơ-chế-khôi-phục-risk-mitigation--rollback)

---

## 1. BỐI CẢNH & MỤC TIÊU NÂNG CẤP

Dự án **SpectraLink IR-OWC Workstation (LabHongNgoai)** là trạm điều khiển, giám sát và truyền nhận dữ liệu qua kênh truyền thông quang không dây tầm gần (Infrared Optical Wireless Communications - IR-OWC) bước sóng 880 nm (TFDU4101 + MCP2122 + STM32F407).

Theo thông báo cập nhật mới nhất từ **Client** và **Team Phần cứng / IoT**:
1. **Kiến trúc phần cứng chuyển đổi sang 2 cổng COM độc lập cho mỗi Controller:**
   - Mỗi bộ điều khiển (Node phần cứng) khi kết nối với máy tính sẽ cung cấp **2 cổng COM UART vật lý**:
     - **Cổng COM TX:** Dành riêng cho máy tính gửi dữ liệu xuống board để phát tín hiệu quang học hồng ngoại.
     - **Cổng COM RX:** Dành riêng cho board đẩy dữ liệu thu được từ cảm biến quang học hồng ngoại lên máy tính.
   - Cần thiết kế lại toàn bộ luồng truyền/nhận ở các tầng: `transport`, `core/serial`, `backend_bridge`, và giao diện kết nối trên Web UI.
   - Hệ thống phải hỗ trợ trơn tru cả 2 kịch bản: **1 máy tính chạy 2 node đồng thời (Dual-node demo)** và **2 máy tính chạy độc lập nối quang với nhau**.
2. **Cấu hình Baudrate động dạng mảng/danh sách hỗ trợ:**
   - Trên board mạch của Team IoT có tích hợp switch gạt (DIP Switch) để thay đổi tốc độ baudrate vật lý (ví dụ: 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600 bps).
   - Thay vì hardcode tốc độ cố định đơn lẻ trong code hoặc trên giao diện HTML, hệ thống cần hỗ trợ cấu hình Baudrate dưới dạng danh sách/mảng linh hoạt, cho phép nạp từ file cấu hình, mở rộng hoặc chuyển đổi nhanh chóng trên giao diện để tương thích chính xác với switch phần cứng.
3. **Rà soát & Tối giản hóa Giao diện (Clean, Professional Technical Modern):**
   - Loại bỏ các badge highlight nhiều màu sắc gây rối mắt (visual noise) như: "CLEAN LAYERED ARCHITECTURE • ACTIVE", các badge lặp lại thông điệp "880nm NIR Direct LOS", "BER < 10^-9", các hiệu ứng nhấp nháy `animate-ping` không cần thiết.
   - Chuẩn hóa toàn bộ ứng dụng theo phong cách giao diện kỹ thuật hiện đại, phẳng, gọn gàng, tăng độ tương phản và chú trọng hiển thị dữ liệu đo lường thực tế (tương tự các phần mềm đo kiểm chuyên dụng như Logic Analyzer, Wireshark, LabVIEW).

> **LƯU Ý QUAN TRỌNG KHI TRIỂN KHAI:**
> - Tuyệt đối **KHÔNG viết comment vào code** khi thực hiện implementation trong các file `.py`, `.js`, `.html`.
> - Tài liệu này là kim chỉ nam chi tiết nhất quy định thiết kế, kiến trúc và các bước thực thi.

---

## 2. PHÂN TÍCH CHUYÊN SÂU YÊU CẦU 1: KIẾN TRÚC DUAL COM PORT (TX / RX TÁCH BIỆT)

### 2.1. Bản chất Vật lý & Luồng Tín Hiệu Quang Học

Trong thiết kế phần cứng trước đây, mỗi controller sử dụng 1 cổng COM duy nhất vừa phát vừa thu (Half-Duplex trên 1 kênh UART). Tuy nhiên, để tối ưu hóa băng thông, tránh xung đột bộ đệm DMA bên trong vi điều khiển STM32 và cho phép thu phát đồng thời hoặc truyền nhận tốc độ cao, Team IoT đã thiết kế phần cứng gồm **2 chip USB-to-UART độc lập** (hoặc 1 chip dual-UART như FT2232D / CH342 / CP2105) trên mỗi bo mạch:
- **Kênh TX (COM phát):** Nhận byte stream từ App gửi xuống qua USB -> đẩy vào chân UART_TX của STM32 -> qua bộ mã hóa MCP2122 (tạo xung 3/16 bit period) -> kích hoạt LED hồng ngoại TFDU4101 phát chùm tia quang 880 nm.
- **Kênh RX (COM thu):** Photodiode TFDU4101 nhận chùm tia quang 880 nm -> đưa qua bộ giải mã MCP2122 khôi phục xung chuẩn UART -> đưa vào chân UART_RX của STM32 -> đẩy lên cổng USB COM RX -> App đọc byte stream.

```
+---------------------------------------------------------------------------------------------------------+
|                                    KỊCH BẢN KẾT NỐI VẬT LÝ TOÀN PHẦN                                    |
|                                                                                                         |
|  [ NODE A - TRẠM A ]                                                       [ NODE B - TRẠM B ]          |
|  +---------------------------+                                             +---------------------------+|
|  |       Lab Terminal A      |                                             |       Lab Terminal B      | |
|  |  +----------+ +---------+ |                                             | +---------+ +----------+ | |
|  |  |  COM TX  | |  COM RX | |                                             | |  COM TX | |  COM RX  | | |
|  +--+----+-----+---+---+---+                                             +---+----+----+---+---+----+--+ |
|          |             ^                                                          |             ^        |
|      (Ghi UART)    (Đọc UART)                                                 (Ghi UART)    (Đọc UART)   |
|          v             |                                                          v             |        |
|  +-------+-------------+-----+                                             +------+-------------+-----+  |
|  | Hardware Controller A     |                                             | Hardware Controller B    |  |
|  | [STM32 + MCP2122 + TFDU]  |                                             | [STM32 + MCP2122 + TFDU] |  |
|  |                           |                                             |                          |  |
|  | Phát Quang (TX)           | ===== Chùm tia quang học hồng ngoại 880nm ===> | Thu Quang (RX)           |  |
|  |                           |                                             |                          |  |
|  | Thu Quang (RX)            | <==== Chùm tia quang học hồng ngoại 880nm ===== | Phát Quang (TX)          |  |
|  +---------------------------+                                             +--------------------------+  |
+---------------------------------------------------------------------------------------------------------+
```

#### Chi tiết luồng gửi dữ liệu (Data Flight Flow):
1. **Node A phát dữ liệu sang Node B (Chat / Data Chunk / Ping):**
   - App A đóng gói dữ liệu thành khung nhị phân chuẩn (`0xAA55` SOF, SEQ, CMD, LEN, PAYLOAD, CRC32, `0x55AA` EOF).
   - App A ghi toàn bộ khung nhị phân vào cổng **COM TX của Node A** (`COM_TX_A`).
   - Hardware Controller A điều chế và phát chùm tia hồng ngoại 880 nm qua không gian.
   - Hardware Controller B bắt tín hiệu quang học, giải mã thành chuỗi byte UART và đẩy ra cổng **COM RX của Node B** (`COM_RX_B`).
   - App B lắng nghe liên tục trên cổng **COM RX của Node B** (`COM_RX_B`), gom byte vào accumulation buffer, giải mã khung và xử lý gói tin.
2. **Node B gửi phản hồi về Node A (ACK / Pong / Chat reply):**
   - App B tạo khung ACK (Opcode `0x10`, mã xác thực CRC và SEQ).
   - App B ghi khung ACK vào cổng **COM TX của Node B** (`COM_TX_B`).
   - Hardware Controller B phát chùm tia hồng ngoại 880 nm ngược về Controller A.
   - Hardware Controller A nhận tín hiệu quang học, giải mã và đẩy ra cổng **COM RX của Node A** (`COM_RX_A`).
   - App A nhận khung ACK trên cổng **COM RX của Node A** (`COM_RX_A`), dừng bộ đếm thời gian Stop-and-Wait ARQ và chuyển sang gửi chunk tiếp theo.

---

### 2.2. Kịch Bản Triển Khai: Dual Node trên 1 Máy vs. 2 Máy Độc Lập

Hệ thống phải tương thích liền mạch với 2 mô hình vận hành:

#### Kịch bản 1: Hai máy tính độc lập (2 Independent PCs - Môi trường vận hành thực tế phòng Lab)
- **PC 1 (Trạm A):**
  - Cắm Controller A qua cổng USB. Hệ điều hành máy 1 nhận diện 2 cổng COM (Ví dụ Windows: `COM3` và `COM4`; Linux: `/dev/ttyUSB0` và `/dev/ttyUSB1`).
  - Người dùng mở App A, chọn:
    - **Port TX:** `COM3`
    - **Port RX:** `COM4`
    - **Baudrate:** `115200` (khớp với switch phần cứng trên board A)
    - Nhấn **Connect**.
- **PC 2 (Trạm B):**
  - Cắm Controller B qua cổng USB. Hệ điều hành máy 2 nhận diện 2 cổng COM (Ví dụ: `COM3` và `COM4` của máy 2, hoàn toàn độc lập với máy 1).
  - Người dùng mở App B, chọn:
    - **Port TX:** `COM3`
    - **Port RX:** `COM4`
    - **Baudrate:** `115200` (khớp với switch phần cứng trên board B)
    - Nhấn **Connect**.
- **Kết quả:** Hai máy tính truyền nhận dữ liệu hoàn toàn qua môi trường quang học không dây.

#### Kịch bản 2: Một máy chạy Dual Node (Dual-Instance on 1 PC - Môi trường Test/Demo)
- **Trường hợp có 2 bộ phần cứng thật cắm cùng 1 máy tính:**
  - Máy tính nhận diện 4 cổng COM: ví dụ `COM3`, `COM4`, `COM5`, `COM6`.
  - Khởi chạy App Instance A (`--instance A`):
    - Cấu hình: TX = `COM3`, RX = `COM4`.
    - Dữ liệu lưu tại: `data/instance_A/`.
  - Khởi chạy App Instance B (`--instance B`):
    - Cấu hình: TX = `COM5`, RX = `COM6`.
    - Dữ liệu lưu tại: `data/instance_B/`.
  - Hai cửa sổ ứng dụng chạy song song trên cùng màn hình (hỗ trợ `--window-pos left` và `--window-pos right`), mỗi ứng dụng chiếm 2 cổng COM tương ứng của board đó.
- **Trường hợp giả lập (Virtual Mock / Virtual Dual Link):**
  - Khi không có phần cứng vật lý, hệ thống hỗ trợ:
    - `MOCK_COM_LOOPBACK`: Cho phép kiểm thử tự phát - tự thu (TX và RX cùng trỏ vào 1 cổng ảo).
    - `LINK_A` và `LINK_B`: Kết nối qua `LinkBroker` TCP socket nội trình (Node A dùng `LINK_A`, Node B dùng `LINK_B`).
    - Trong chế độ cổng ảo, cho phép chọn TX = `LINK_A` và RX = `LINK_A` (hoặc tự động đồng bộ).

---

### 2.3. Tái Thiết Kế Tầng Giao Vận (Transport Layer) & Lớp `DualSerialTransport`

Hợp đồng giao vận hiện tại được định nghĩa trong `src/ir_terminal/core/interfaces.py`:
```python
class ITransport(ABC):
    @abstractmethod
    def open(self) -> None: ...
    @abstractmethod
    def close(self) -> None: ...
    @property
    @abstractmethod
    def is_open(self) -> bool: ...
    @abstractmethod
    def write(self, data: bytes) -> int: ...
    @abstractmethod
    def read(self, size: int | None = None) -> bytes: ...
    @property
    @abstractmethod
    def in_waiting(self) -> int: ...
```

Để giữ nguyên tính toàn vẹn của kiến trúc đa tầng (Layered Architecture) mà không làm xáo trộn các tầng trên (`framer`, `services`, `telemetry`), giải pháp tối ưu nhất là tạo ra một lớp giao vận mới: **`DualSerialTransport`** hiện thực hóa trực tiếp interface `ITransport`.

#### Thiết kế lớp `DualSerialTransport`:
- **File mới:** `src/ir_terminal/core/dual_serial_transport.py`
- **Nguyên lý hoạt động:**
  - Nhận vào 2 tham số cổng: `tx_port: str` và `rx_port: str`, cùng tốc độ `baudrate: int`.
  - **Trường hợp `tx_port == rx_port` (Chế độ Unified / Single COM):**
    - Mở một đối tượng `serial.Serial` duy nhất.
    - Gán cả con trỏ ghi (`_tx_serial`) và con trỏ đọc (`_rx_serial`) vào đối tượng này.
    - Đảm bảo tương thích ngược 100% với các bộ chuyển đổi đơn cổng và cổng ảo.
  - **Trường hợp `tx_port != rx_port` (Chế độ Dual COM chuẩn):**
    - Khởi tạo 2 đối tượng `serial.Serial` riêng biệt:
      - `self._tx_serial = serial.Serial(port=self._tx_port, baudrate=self._baudrate, timeout=0, write_timeout=1.0)`
      - `self._rx_serial = serial.Serial(port=self._rx_port, baudrate=self._baudrate, timeout=0)`
    - `write(data)`: Ghi trực tiếp vào `_tx_serial`.
    - `read(size)`: Đọc trực tiếp từ `_rx_serial`.
    - `in_waiting`: Trả về `_rx_serial.in_waiting`.
    - `open()`: Mở `_tx_serial`, sau đó mở `_rx_serial`. Nếu cổng thứ hai mở thất bại, tự động đóng an toàn cổng thứ nhất (Clean Rollback) và giải phóng tài nguyên.
    - `close()`: Đóng an toàn cả hai cổng trong khối `finally`, bắt mọi ngoại lệ để không bao giờ rò rỉ file descriptor.
    - `is_open`: Trả về `True` khi và chỉ khi cả `_tx_serial` và `_rx_serial` đều đang mở.

#### Nâng cấp `src/ir_terminal/core/transport_factory.py`:
Cập nhật chữ ký hàm `create_transport`:
```python
def create_transport(
    tx_port: str,
    rx_port: str | None = None,
    baudrate: int = 115200,
    link_endpoints: dict[str, tuple[str, int]] | None = None,
    *,
    link_connect_retries: int = 0,
    link_retry_interval_sec: float = 0.25,
    link_connect_timeout_sec: float = SOCKET_CONNECT_TIMEOUT_SEC,
    link_token: str | None = None,
) -> ITransport:
```
- Nếu `rx_port is None`: Mặc định gán `rx_port = tx_port`.
- Nếu `tx_port` bắt đầu bằng `"MOCK"`: Trả về `MockSerialPort`.
- Nếu `tx_port` là `LINK_A` hoặc `LINK_B`: Trả về `SocketTransport`.
- Nếu là cổng vật lý (`COMx` hoặc `/dev/ttyUSBx`):
  - Khởi tạo và trả về `DualSerialTransport(tx_port=tx_port, rx_port=rx_port, baudrate=baudrate)`.

---

### 2.4. Nâng Cấp `SerialWorker` & `SerialManager` (Thread I/O & Signal Flow)

#### 1. Trong `src/ir_terminal/core/serial_worker.py`:
- Thêm slot mở cổng kép:
  ```python
  @Slot(str, str, int)
  def open_ports(self, tx_port: str, rx_port: str, baudrate: int = DEFAULT_BAUDRATE) -> None:
  ```
- Cập nhật slot `open_port(self, port_name: str, baudrate: int = DEFAULT_BAUDRATE)` hiện tại để ủy quyền (delegate) sang `open_ports(port_name, port_name, baudrate)`.
- Lưu trữ thông tin trạng thái:
  - `self._current_tx_port: str = ""`
  - `self._current_rx_port: str = ""`
  - `self._current_baud: int = baudrate`
- Khi mở cổng thành công:
  - Phát tín hiệu: `self.connected.emit(tx_port, rx_port, baudrate)` (hoặc phát định dạng mô tả chuỗi để tương thích các listener cũ: `f"TX:{tx_port} | RX:{rx_port}"`).
- Trong vòng lặp polling `_poll_serial()`:
  - Giữ nguyên cơ chế polling 10ms (100 Hz), kiểm tra `self._transport.in_waiting` và `self._transport.read()`. Nhờ tính đa hình của `DualSerialTransport`, dữ liệu tự động được kéo từ cổng RX mà không cần thay đổi logic gom byte và framer.
- Trong slot phát dữ liệu `send_bytes(data)`:
  - Giữ nguyên `self._transport.write(data)`. Dữ liệu tự động được đẩy ra cổng TX.

#### 2. Trong `src/ir_terminal/core/serial_manager.py`:
- Cập nhật internal dispatch signal:
  `_sig_open_dual: ClassVar[Signal] = Signal(str, str, int)`
- Bổ sung phương thức kết nối:
  ```python
  def connect_ports(self, tx_port: str, rx_port: str, baudrate: int = DEFAULT_BAUDRATE) -> None:
      self._sig_open_dual.emit(tx_port, rx_port, baudrate)
  ```
- Duy trì phương thức cũ để tương thích ngược:
  ```python
  def connect_port(self, port_name: str, baudrate: int = DEFAULT_BAUDRATE) -> None:
      self.connect_ports(port_name, port_name, baudrate)
  ```
- Properties công khai:
  - `current_tx_port -> str`
  - `current_rx_port -> str`
  - `current_baud -> int`
  - `is_connected -> bool`

---

### 2.5. Nâng Cấp `BackendBridge` & IPC WebChannel

Trong `src/ir_terminal/ui/backend_bridge.py`:
1. **Tín hiệu ra JavaScript (Signals):**
   - Nâng cấp `portConnected`:
     `portConnected: ClassVar[Signal] = Signal(str, str, int)`  # (tx_port, rx_port, baudrate)
     *(Có thể hỗ trợ tín hiệu phụ hoặc chuỗi kết hợp để đảm bảo an toàn tuyệt đối cho mọi view)*
2. **Khe nhận từ JavaScript (Slots):**
   - Bổ sung `@Slot(str, str, int)`:
     ```python
     @Slot(str, str, int)
     def connectDualPorts(self, tx_port: str, rx_port: str, baudrate: int) -> None:
         # Validate tx_port, rx_port, baudrate
         # Gọi self._serial_manager.connect_ports(tx_port, rx_port, baudrate)
     ```
   - Giữ nguyên `@Slot(str, int) def connectPort(self, port_name: str, baudrate: int)` gọi sang `connectDualPorts(port_name, port_name, baudrate)`.
3. **Cung cấp dữ liệu cổng cho UI:**
   - Trong `scanPorts()`, tiếp tục trả về mảng danh sách chi tiết các cổng kèm metadata (VID, PID, description, label) để Web UI điền vào cả 2 dropdown TX và RX.

---

### 2.6. Cấu Hình Khởi Chạy `LaunchConfig` & CLI Arguments

Trong `src/ir_terminal/config.py`:
1. **Bổ sung thuộc tính vào `LaunchConfig`:**
   - `tx_port: str | None = None`
   - `rx_port: str | None = None`
   - Property `effective_tx_port`: Ưu tiên `tx_port`, nếu không có thì lấy `port`.
   - Property `effective_rx_port`: Ưu tiên `rx_port`, nếu không có thì lấy `port`.
2. **Cập nhật `parse_launch_args`:**
   - Thêm cờ `--tx-port` (e.g. `--tx-port COM3`).
   - Thêm cờ `--rx-port` (e.g. `--rx-port COM4`).
   - Giữ cờ `--port` (nếu chỉ truyền `--port COM3`, cả TX và RX đều nhận giá trị `COM3`).
3. **Cập nhật các script thực thi:**
   - `scripts/Start_Com_Pair.cmd`: Hỗ trợ truyền 4 cổng hoặc 2 cặp cổng cho 2 instance:
     - Instance A: `--tx-port COM3 --rx-port COM4`
     - Instance B: `--tx-port COM5 --rx-port COM6`
   - Cập nhật tài liệu `runbook.txt` hướng dẫn người dùng chạy với 2 cổng COM.

---

## 3. PHÂN TÍCH CHUYÊN SÂU YÊU CẦU 2: CẤU HÌNH BAUDRATE DẠNG MẢNG / DANH SÁCH ĐỘNG

### 3.1. Bối Cảnh Kỹ Thuật: Switch Gạt Baudrate Phần Cứng Trên Board IoT

Trên bo mạch vi điều khiển của Team IoT, đường UART được điều khiển qua thạch anh và các bộ chia xung. Để thuận tiện điều chỉnh tốc độ quang phù hợp với cự ly phát (khoảng cách xa cần baudrate thấp để tăng SNR, khoảng cách gần có thể chạy baudrate cao), phần cứng bố trí switch gạt (DIP Switch 2-pin hoặc 3-pin).
Ví dụ:
- Switch `[0, 0]` -> `9600 bps` (Chế độ cự ly xa, độ nhạy cao).
- Switch `[0, 1]` -> `57600 bps` (Chế độ cân bằng).
- Switch `[1, 0]` -> `115200 bps` (Chế độ tiêu chuẩn mặc định).
- Switch `[1, 1]` -> `230400 bps` hoặc `921600 bps` (Chế độ tốc độ cao).

Do đó, ứng dụng phần mềm không được ép cố định một vài số cứng trong HTML mà phải có **danh sách mảng cấu hình tập trung**, có thể tùy biến dễ dàng qua cấu hình và hiển thị động trên giao diện người dùng.

---

### 3.2. Mô Hình Dữ Liệu & Khả Năng Mở Rộng Danh Sách Baudrate

1. **Định nghĩa Hằng số Mặc định trong `src/ir_terminal/protocol/constants.py`:**
   ```python
   DEFAULT_BAUDRATE: int = 115200
   SUPPORTED_BAUDRATES: tuple[int, ...] = (
       9600,
       19200,
       38400,
       57600,
       115200,
       230400,
       460800,
       921600,
   )
   ```
2. **Quản lý cấu hình qua `StorageService` / `settings.json`:**
   - Trong `data/settings.json`, bổ sung key:
     ```json
     {
       "supported_baudrates": [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600],
       "default_baudrate": 115200
     }
     ```
   - Nếu trong `settings.json` có khai báo mảng tùy biến (do kỹ sư phòng lab tự thêm các tốc độ đặc thù như 14400, 28800), hệ thống sẽ nạp mảng này; nếu không, sẽ sử dụng `SUPPORTED_BAUDRATES` mặc định.

---

### 3.3. Cơ Chế Đồng Bộ Giữa Backend và UI Controller

1. **Expose qua `BackendBridge`:**
   - Bổ sung slot:
     ```python
     @Slot(result=str)
     def getAvailableBaudrates(self) -> str:
         """Returns JSON array of supported baudrates."""
         return json.dumps(list(self._storage.get_setting("supported_baudrates", SUPPORTED_BAUDRATES)))
     ```
   - Trong `getLaunchConfig()`, đính kèm luôn `supported_baudrates` và `default_baudrate`.
2. **Dynamic Rendering trên Web UI (`bridge_client.js`):**
   - Loại bỏ 4 nút bấm cứng trong `view_hardware.html`.
   - Tạo hàm JavaScript `renderBaudrateSelector(baudrates, activeBaud)`:
     - Tự động duyệt qua mảng số nguyên và tạo các nút bấm chọn tốc độ (Segmented control).
     - Gắn sự kiện click để lưu `selectedBaud` và cập nhật class active/inactive.
     - Thiết kế responsive dạng grid hoặc flex-wrap gọn gàng, hỗ trợ hiển thị đẹp mắt từ 4 đến 10 tốc độ khác nhau.

---

## 4. PHÂN TÍCH CHUYÊN SÂU YÊU CẦU 3: RÀ SOÁT & TỐI GIẢN HÓA GIAO DIỆN KỸ THUẬT HIỆN ĐẠI

### 4.1. Audit Hiện Trạng Giao Diện & Điểm Gây Nhiễu Thị Giác (Visual Noise)

Qua khảo sát toàn bộ mã nguồn giao diện tại `src/ir_terminal/ui/web/` và các thành phần trong `components/`, chúng tôi phát hiện nhiều chi tiết thừa, lặp lại và màu sắc sặc sỡ không phù hợp với phần mềm kỹ thuật công nghiệp:

| Vị trí | Thành phần hiện tại | Vấn đề & Noise thị giác | Giải pháp tối giản hóa |
| :--- | :--- | :--- | :--- |
| **Top Chrome Header** | `<div class="bg-surface-container-high/40 text-on-surface-variant font-bold">880nm NIR Direct LOS</div>` | Trùng lặp thông tin bước sóng ở header trong khi thanh bên và các card đã có. | Bỏ badge tĩnh này, thay bằng hiển thị phiên bản gọn gàng hoặc mã trạm (`Node A` / `Node B`). |
| **Top Chrome Ribbon** | `<span id="ribbon-dot" class="status-pulse">` | Animation nhấp nháy `status-pulse` liên tục 2s gây mỏi mắt và phân tâm. | Giữ dot trạng thái tĩnh (Xanh khi Connected, Xám khi Standby), không dùng pulse liên tục. |
| **Sidebar Footer** | Badge `880nm` + `HEALTH 99.8% ONLINE` | Số liệu giả định cố định (fake KPI), badge màu tương phản mạnh không cần thiết. | Đơn giản hóa thành trạng thái kết nối chuẩn: `ONLINE` / `OFFLINE` với font mono thanh mảnh. |
| **Hardware View Header** | 2 badge bên phải: `880nm NIR Direct LOS` và `BER < 10^-9` | Nhãn tĩnh, chiếm diện tích, tạo cảm giác như trang web quảng cáo thay vì công cụ đo kiểm. | Loại bỏ cả 2 badge; chuyển các thông số bước sóng và BER xuống bảng thông số kỹ thuật bên dưới. |
| **Hardware View Card 1** | Port Selector chỉ có 1 cổng COM duy nhất | Không đáp ứng yêu cầu mới (cần 2 cổng COM: TX và RX). | Thay bằng **Cặp chọn cổng kép: COM TX & COM RX** với nhãn kỹ thuật rõ ràng. |
| **Hardware View Card 1** | Badge `Port Standby` / `Port Active` | Viền pill badge màu xanh/xám chiếm diện tích tiêu đề card. | Tinh giản thành một chấm trạng thái nhỏ kèm nhãn text thanh mảnh. |
| **Hardware View Bottom** | Card "Protocol Layer Verification & Architecture Health" với badge to: `CLEAN LAYERED ARCHITECTURE • ACTIVE` | Khẩu hiệu tiếp thị tĩnh, 3 tile lặp lại thông tin không tương tác được. | Loại bỏ khẩu hiệu; thay bằng khối thông số kỹ thuật mạch vật lý tinh tế: `Physical Layer Specifications (UART 8-N-1, MCP2122 3/16 Pulse, TFDU4101 NIR)`. |
| **File Transfer View** | Header strip có 2 badge to: `880nm NIR Direct LOS` và `Link State: Locked (BER < 10^-9)` | Đập vào mắt người dùng các khối màu vàng/xanh lớn gây phân tâm khỏi khu vực kéo thả file. | Loại bỏ các khối badge màu; giữ header phẳng, sạch sẽ, tập trung vào chức năng truyền file. |
| **File Transfer Table** | Badge `Auto-Checksum Verification Active` | Badge xanh lá cồng kềnh phía trên bảng lịch sử truyền file. | Chuyển thành icon xác thực CRC-32 nhỏ gọn ngay tiêu đề bảng. |
| **Chat View Header** | Badge `Half-Duplex Synchronized` | Khối màu tím/xanh tương phản cao trên đầu thanh chat. | Tối giản thành thông tin phụ dạng text `Half-Duplex • 880nm` nhỏ gọn. |
| **Chat View Input** | Badge `[ ] BURST_TX` màu xanh đậm bên trong ô input | Chiếm không gian gõ phím, màu xanh đậm tương phản quá gắt. | Thay bằng prefix prompt dạng console tối giản `TX >` hoặc icon gửi tiêu chuẩn. |
| **Telemetry View** | Chấm tròn xanh nhấp nháy `animate-ping` ở `Live Capture Active` | Hiệu ứng ping động liên tục làm người dùng khó chịu khi theo dõi đồ thị. | Chuyển thành chấm tròn tĩnh màu xanh dịu chuẩn kỹ thuật. |
| **Telemetry Sniffer** | Các badge loại khung (`SYN/ACK`, `BURST_DATA`, `CRC_OK`, `ERROR_FRAMES`) | Nền pill màu tím, xanh lá, đỏ đục xen kẽ trong từng dòng bảng sniffer gây rối bảng dữ liệu. | Chuẩn hóa bảng Sniffer: Dùng text monospaced có màu sắc ngữ nghĩa tinh tế (Neutral cho DATA, Green cho ACK, Red cho ERR), nền trong suốt. |

---

### 4.2. Định Hình Phong Cách Giao Diện Kỹ Thuật (Modern Technical Instrument)

Giao diện mới được quy chuẩn theo phong cách **Modern Technical Precision** (Lấy cảm hứng từ giao diện thiết bị đo logic của Saleae, Wireshark và Xcode Instruments):
1. **Bảng màu Tối giản & Ngữ nghĩa (Strict Semantic Color Palette):**
   - **Màu nền ứng dụng:** `#F4F5F7` (Xám bạc kỹ thuật, chống chói mắt).
   - **Màu thẻ card / panel:** `#FFFFFF` với viền siêu mảnh `border border-black/[0.08]` và đổ bóng nhẹ `shadow-[0_1px_3px_rgba(0,0,0,0.04)]`.
   - **Màu chữ chính:** `#1E293B` (Slate-800, độ tương phản sắc nét).
   - **Màu chữ phụ / nhãn:** `#64748B` (Slate-500).
   - **Màu chỉ định trạng thái (Duy nhất 3 trạng thái):**
     - **Active / Synced:** Chấm tròn `#10B981` (Emerald), text xanh dịu, không viền rườm rà.
     - **Standby / Offline:** Chấm tròn `#94A3B8` (Slate-400), text xám trung tính.
     - **Error / Fault:** Chấm tròn `#EF4444` (Red), text đỏ cảnh báo.
   - **Màu hành động chính (Primary Action):** `#2563EB` (Cobalt Blue) cho các nút thao tác chính (`Connect`, `Send`, `Transmit`).
2. **Typography Sắc Nét:**
   - Tiêu đề: Font `Plus Jakarta Sans`, cân bằng độ đậm `font-semibold`, giảm letter-spacing nhẹ.
   - Nhãn tham số & Dữ liệu kỹ thuật: Font Monospace `Inter / JetBrains Mono` cho toàn bộ cổng COM, baudrate, hex stream, CRC-32 và timestamp.
3. **Loại bỏ Hoàn toàn Pill Badges Thừa:**
   - Thay thế các khối badge `rounded-full px-2.5 py-1 bg-...` bằng cấu trúc **Key-Value Metadata Row** sạch sẽ:
     ```html
     <div class="flex items-center gap-3 text-xs font-mono text-slate-500">
       <span>CH: <strong class="text-slate-800">880nm NIR</strong></span>
       <span class="text-slate-300">•</span>
       <span>MOD: <strong class="text-slate-800">MCP2122</strong></span>
       <span class="text-slate-300">•</span>
       <span>LINK: <strong class="text-emerald-600">CONNECTED</strong></span>
     </div>
     ```

---

### 4.3. Kế Hoạch Tinh Chỉnh Chi Tiết Cho Từng View

#### 1. Hardware View (`src/ir_terminal/ui/web/components/view_hardware.html`):
- **Cụm kết nối Serial:**
  - Thiết kế dạng lưới 2 cột hoặc 2 hàng liền kề:
    - **Cột 1: Cổng TX (Transmitter):** Dropdown chọn COM phát (ví dụ: `COM3 (STM32 TX Port)`).
    - **Cột 2: Cổng RX (Receiver):** Dropdown chọn COM thu (ví dụ: `COM4 (STM32 RX Port)`).
    - Có nút tiện ích nhỏ `[Sync / Same Port]` hoặc checkbox `Use single port for both TX & RX` khi người dùng muốn dùng chung 1 cổng COM cho chế độ loopback/unified.
- **Baudrate Selector:**
  - Thay khối 4 nút tĩnh bằng container dynamic `#baudrate-selector-container`.
  - Hiển thị dải nút bấm thanh mảnh (segmented pill bar), mỗi nút hiển thị tốc độ (9600, 19200, 57600, 115200, 230400, v.v.).
- **Tối giản hóa Card Optical Link & Protocol Health:**
  - Giữ lại KPI RTT đo lường thực tế từ lệnh Ping Echo.
  - Loại bỏ các badge trang trí `NIR INFRARED`, `SYNCED`, `CLEAN LAYERED ARCHITECTURE`.
  - Thay bằng bảng thông số kỹ thuật rõ ràng, chuyên nghiệp.

#### 2. File Transfer View (`src/ir_terminal/ui/web/components/view_file.html`):
- Xóa bỏ toàn bộ các khối badge màu vàng/xanh ở thanh header.
- Dropzone truyền file thiết kế phẳng, viền nét đứt tinh tế màu slate-300, icon tối giản.
- Bảng lịch sử truyền file hiển thị các cột: File Payload, Size, CRC-32 Checksum, State, Action. State chỉ dùng chấm tròn nhỏ (Xanh = Hoàn tất, Vàng = Đang gửi, Đỏ = Lỗi) kèm chữ, không dùng khối badge to.

#### 3. Chat View (`src/ir_terminal/ui/web/components/view_chat.html`):
- Header chat tinh giản: Tiêu đề "Realtime Optical Messaging", thông tin kênh hiển thị dạng text `880nm Infrared • Direct LOS`.
- Ô nhập liệu tin nhắn: Nền trắng sạch, viền slate-200 bo góc nhẹ `rounded-lg`, tiền tố console `TX >` font mono thanh thoát, nút gửi gọn gàng.

#### 4. Telemetry View (`src/ir_terminal/ui/web/components/view_telemetry.html`):
- Xóa bỏ hiệu ứng `animate-ping` gây mỏi mắt.
- Bảng Sniffer: Thiết kế dạng bảng log viễn thông chuẩn cao cấp (Dark text trên nền trắng/slate nhạt), các opcode (`DATA`, `ACK`, `PING`, `PONG`) hiển thị font monospace gọn gàng, có màu chữ phân biệt nhẹ nhàng thay vì nền badge màu đậm.

---

## 5. KẾ HOẠCH TRIỂN KHAI TỪNG BƯỚC (STEP-BY-STEP IMPLEMENTATION PLAN)

```
+---------------------------------------------------------------------------------------------------------+
|                                  LỘ TRÌNH 5 GIAI ĐOẠN TRIỂN KHAI                                        |
|                                                                                                         |
|   +-----------------------+     +------------------------+     +-----------------------+                |
|   | Giai đoạn 1           |     | Giai đoạn 2            |     | Giai đoạn 3           |                |
|   | Core Transport &      | --> | Protocol, Settings &   | --> | Backend Bridge &      |                |
|   | DualSerialTransport   |     | Dynamic Baudrates      |     | IPC Signaling         |                |
|   +-----------------------+     +------------------------+     +-----------------------+                |
|                                                                            |                            |
|                                                                            v                            |
|   +-----------------------+     +------------------------+     +-----------------------+                |
|   | Giai đoạn 5           |     | Giai đoạn 4B           |     | Giai đoạn 4A          |                |
|   | E2E Testing, Runbooks | <-- | Web UI Modernization   | <-- | Dual COM Connect      |                |
|   | & Release Verif.      |     | (De-noising Badges)    |     | UI Component          |                |
|   +-----------------------+     +------------------------+     +-----------------------+                |
+---------------------------------------------------------------------------------------------------------+
```

### 5.1. Giai Đoạn 1: Core Transport Layer & `DualSerialTransport`

- [ ] **Bước 1.1: Tạo lớp `DualSerialTransport` (`src/ir_terminal/core/dual_serial_transport.py`)**
  - Kế thừa `ITransport`.
  - Quản lý `_tx_serial` và `_rx_serial`.
  - Xử lý mở/đóng an toàn, chống leak file descriptor khi 1 trong 2 cổng lỗi.
  - Hiện thực `write()` vào cổng TX, `read()` và `in_waiting` từ cổng RX.
  - Hỗ trợ trường hợp `tx_port == rx_port` dùng chung 1 instance `serial.Serial`.
- [ ] **Bước 1.2: Cập nhật `src/ir_terminal/core/transport_factory.py`**
  - Mở rộng hàm `create_transport(tx_port, rx_port=None, baudrate=115200, ...)` để khởi tạo `DualSerialTransport` cho thiết bị phần cứng.
  - Giữ nguyên cơ chế tạo `MockSerialPort` và `SocketTransport`.
- [ ] **Bước 1.3: Cập nhật `SerialWorker` (`src/ir_terminal/core/serial_worker.py`)**
  - Bổ sung slot `open_ports(tx_port: str, rx_port: str, baudrate: int)`.
  - Định tuyến `open_port(port_name, baudrate)` sang `open_ports(port_name, port_name, baudrate)`.
  - Cập nhật signal `connected` phát thông tin 2 cổng.
- [ ] **Bước 1.4: Cập nhật `SerialManager` (`src/ir_terminal/core/serial_manager.py`)**
  - Bổ sung `_sig_open_dual = Signal(str, str, int)` và method `connect_ports(tx_port, rx_port, baudrate)`.
  - Giữ `connect_port(port_name, baudrate)` tương thích ngược.
  - Bổ sung properties `current_tx_port` và `current_rx_port`.
- [ ] **Bước 1.5: Viết Unit Tests kiểm thử Core Transport mới**
  - Tạo `tests/unit/test_dual_serial_transport.py`: Kiểm thử mock kịch bản mở 2 cổng khác nhau, ghi TX, đọc RX, kiểm tra rollback khi cổng thứ 2 lỗi, kiểm tra chế độ single port.

---

### 5.2. Giai Đoạn 2: Configuration & Dynamic Baudrate Management

- [ ] **Bước 2.1: Bổ sung danh sách Baudrate chuẩn trong `src/ir_terminal/protocol/constants.py`**
  - Khai báo `SUPPORTED_BAUDRATES = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)`.
- [ ] **Bước 2.2: Nâng cấp `LaunchConfig` và `parse_launch_args` (`src/ir_terminal/config.py`)**
  - Bổ sung các cờ CLI: `--tx-port`, `--rx-port`.
  - Thêm property `effective_tx_port` và `effective_rx_port`.
  - Bổ sung trường `supported_baudrates: tuple[int, ...]`.
- [ ] **Bước 2.3: Tích hợp với `StorageService` (`src/ir_terminal/services/storage_service.py`)**
  - Hỗ trợ lưu trữ và đọc key `"supported_baudrates"` từ `settings.json`.
- [ ] **Bước 2.4: Viết Unit Tests cấu hình**
  - Cập nhật `tests/unit/test_config.py` kiểm tra phân tích cú pháp CLI `--tx-port`, `--rx-port`, và các giá trị fallback.

---

### 5.3. Giai Đoạn 3: Backend Bridge & Signal Routing

- [ ] **Bước 3.1: Nâng cấp `BackendBridge` (`src/ir_terminal/ui/backend_bridge.py`)**
  - Bổ sung `@Slot(str, str, int) def connectDualPorts(self, tx_port: str, rx_port: str, baudrate: int) -> None`.
  - Bổ sung `@Slot(result=str) def getAvailableBaudrates(self) -> str`.
  - Bổ sung `@Slot(result=str) def getConnectedPorts(self) -> str` (trả về JSON `{ "tx_port": ..., "rx_port": ..., "baudrate": ... }`).
  - Cập nhật slot `connectPort(port_name, baudrate)` ủy quyền sang `connectDualPorts`.
  - Nâng cấp signal `portConnected` phát tín hiệu `(tx_port, rx_port, baudrate)`.
- [ ] **Bước 3.2: Cập nhật Unit Tests cho BackendBridge**
  - Trong `tests/unit/test_backend_bridge.py`: Bổ sung kiểm thử gọi slot `connectDualPorts`, `getAvailableBaudrates`, và xác thực signal phát ra đúng định dạng.

---

### 5.4. Giai Đoạn 4: Web UI Modernization & Dual COM Connect Component

- [ ] **Bước 4.1: Nâng cấp giao diện kết nối trong `components/view_hardware.html`**
  - Thiết kế lại Card "Serial Connection":
    - Dropdown 1: `Target Port TX (Transmitter - Phát quang)` (id: `select-port-tx`).
    - Dropdown 2: `Target Port RX (Receiver - Thu quang)` (id: `select-port-rx`).
    - Nút checkbox hoặc switch nhỏ: `Link TX & RX to same port`.
    - Container chọn Baudrate động: `<div id="baudrate-selector-container"></div>`.
- [ ] **Bước 4.2: Tối giản hóa & Dọn dẹp Noise trên toàn bộ các View Component**
  - Trong `components/chrome_header.html`: Loại bỏ badge bước sóng tĩnh, loại bỏ pulse nhấp nháy.
  - Trong `components/sidebar_nav.html`: Đơn giản hóa widget trạng thái phần cứng, bỏ số liệu fake `HEALTH 99.8%`.
  - Trong `components/view_hardware.html`: Loại bỏ badge `BER < 10^-9`, `880nm NIR Direct LOS`, bỏ card khẩu hiệu `CLEAN LAYERED ARCHITECTURE • ACTIVE`, thay bằng bảng thông số kỹ thuật tối giản.
  - Trong `components/view_file.html`: Xóa bỏ 2 badge lớn ở header, bỏ badge xanh `Auto-Checksum Verification Active` ở bảng.
  - Trong `components/view_chat.html`: Bỏ badge tím `Half-Duplex Synchronized`, bỏ badge `[BURST_TX]` ở thanh gõ tin nhắn.
  - Trong `components/view_telemetry.html`: Xóa bỏ hiệu ứng nhấp nháy `animate-ping`, chuẩn hóa màu sắc bảng Sniffer dạng text monospaced thanh lịch.
- [ ] **Bước 4.3: Nâng cấp logic JavaScript client (`src/ir_terminal/ui/web/js/bridge_client.js`)**
  - Thêm logic load danh sách baudrate động qua `bridge.getAvailableBaudrates()` và render các nút chọn.
  - Cập nhật `refreshPortList()`: Điền đồng thời danh sách cổng COM quét được vào cả 2 dropdown `select-port-tx` và `select-port-rx`. Tự động pre-select thông minh (nếu có 2 cổng COM vật lý: gán cổng 1 cho TX, cổng 2 cho RX).
  - Cập nhật sự kiện nút **Connect**: Lấy giá trị từ cả 2 dropdown và gọi `bridge.connectDualPorts(txPort, rxPort, selectedBaud)`.
  - Cập nhật xử lý sự kiện `bridge.portConnected`: Hiển thị rõ ràng trên thanh ribbon header và footer: `TX: COM3 • RX: COM4 (115200 bps)`.
- [ ] **Bước 4.4: Đồng bộ mã nguồn HTML qua `scripts/build_modular_web.py`**
  - Chạy `python scripts/build_modular_web.py` để biên dịch các component từ `components/` vào `index.html`.
  - Kiểm tra tính toàn vẹn của layout, font chữ, caret dropdown `appearance-none` trên cả Windows và Linux WebEngine.

---

### 5.5. Giai Đoạn 5: Launcher Scripts, Mock Simulation & Integration Tests

- [ ] **Bước 5.1: Cập nhật Launcher Scripts**
  - Cập nhật `scripts/Start_Com_Pair.cmd`: Hỗ trợ truyền tham số 4 cổng (ví dụ: `Start_Com_Pair.cmd COM3 COM4 COM5 COM6`).
  - Cập nhật `scripts/run_dual_mock.sh`: Đảm bảo kiểm thử môi trường giả lập dual-instance vẫn hoạt động trơn tru.
  - Cập nhật `runbook.txt`: Bổ sung hướng dẫn chi tiết cách chạy với 2 cổng COM cho kỹ thuật viên và tester.
- [ ] **Bước 5.2: Cập nhật & Bổ sung Integration Tests**
  - Cập nhật `tests/integration/test_dual_link_e2e.py` và `test_system_e2e_full_lifecycle.py`.
  - Tạo test integration mới: `tests/integration/test_dual_com_bidirectional_e2e.py`:
    - Mô phỏng 2 instance Node A và Node B:
      - Node A gửi dữ liệu ra TX_A -> đẩy sang RX_B của Node B.
      - Node B nhận dữ liệu, xử lý và gửi ACK từ TX_B -> đẩy về RX_A của Node A.
      - Kiểm thử trọn vẹn luồng gửi file nhị phân lớn và chat UTF-8.
- [ ] **Bước 5.3: Chạy toàn bộ Test Suite & Đo kiểm hồi quy**
  - Chạy `QT_QPA_PLATFORM=offscreen pytest` bảo đảm 100% test cases (cả unit và integration) đều PASS.

---

## 6. CHIẾN LƯỢC KIỂM THỬ & TIÊU CHÍ NGHIỆM THU (ACCEPTANCE CRITERIA)

### 6.1. Ma Trận Kịch Bản Kiểm Thử (Test Matrix)

| Test ID | Hạng mục kiểm thử | Kịch bản thực hiện | Kết quả mong đợi |
| :--- | :--- | :--- | :--- |
| **TC-DCOM-01** | Dual COM Port Open & Close | Cấu hình TX = COM3, RX = COM4. Gọi `open_ports()`. | Cả 2 cổng UART mở thành công, `is_open == True`. Khi `close_port()` cả 2 cổng được đóng sạch sẽ. |
| **TC-DCOM-02** | Rollback on Error | Cấu hình TX = COM_VALID, RX = COM_INVALID (cổng không tồn tại hoặc bị chiếm). | Báo lỗi kết nối rõ ràng, cổng TX đã mở trước đó được tự động đóng ngay lập tức, không rò rỉ tài nguyên. |
| **TC-DCOM-03** | Backward-Compatible Single Port | Cấu hình TX = COM3, RX = COM3 (hoặc dùng `MOCK_COM_LOOPBACK`). | Hệ thống nhận diện mở 1 handle duy nhất, truyền nhận hoạt động bình thường như phiên bản trước. |
| **TC-DCOM-04** | Bidirectional Data Flow | Node A (TX: COM3, RX: COM4), Node B (TX: COM5, RX: COM6). Board A nối chéo Board B. | App A gửi text "Hello Node B" -> App B nhận đúng 100%. App B gửi ACK -> App A nhận được ACK và dừng retry. |
| **TC-DCOM-05** | Large File Transfer across Dual COM | Gửi file nhị phân 5 MB với chunk 240 bytes và CRC-32 qua cặp cổng kép. | Toàn bộ các chunk truyền thành công qua cổng TX của bên gửi và cổng RX của bên nhận; file đích có mã băm SHA-256 / CRC-32 trùng khớp 100%. |
| **TC-BAUD-01** | Dynamic Baudrate List API | Gọi `bridge.getAvailableBaudrates()`. | Trả về chuỗi JSON chứa đầy đủ các tốc độ (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600). |
| **TC-BAUD-02** | UI Baudrate Selector Interaction | Người dùng click chọn baudrate `57600` trên giao diện. | Nút `57600` chuyển trạng thái active, biến `selectedBaud = 57600`. Khi nhấn Connect, cổng mở với tốc độ 57600 bps. |
| **TC-UI-01** | Visual Noise Removal Audit | Kiểm tra toàn bộ 4 view và header. | Không còn bất kỳ badge nào mang chữ "CLEAN LAYERED ARCHITECTURE", không lặp lại "880nm NIR Direct LOS", không còn animation `status-pulse` / `animate-ping`. |
| **TC-UI-02** | Dual Port Connect UI | Mở giao diện Hardware View, cắm 2 cổng COM vào máy. Nhấn Rescan. | Cả 2 dropdown TX và RX đều hiển thị danh sách cổng. Người dùng chọn được cổng phát và cổng thu độc lập. |
| **TC-UI-03** | Ribbon & Status Synchronization | Kết nối thành công TX: COM3 và RX: COM4 @ 115200 bps. | Header hiển thị: `STM32 Optical Bridge • TX: COM3 • RX: COM4 (115200 bps)`. Footer hiển thị tương ứng. |

### 6.2. Tiêu Chí Nghiệm Thu Dự Án (Definition of Done)
1. **Chức năng Dual COM:** Hỗ trợ cấu hình và truyền nhận trơn tru qua 2 cổng COM riêng biệt trên cả CLI, Python Core và Web UI.
2. **Chức năng Dynamic Baudrate:** Danh sách tốc độ baudrate được quản lý dạng mảng, mở rộng linh hoạt, đồng bộ chính xác với switch gạt trên board IoT.
3. **Giao diện người dùng:** Đáp ứng tiêu chuẩn thiết kế phẳng kỹ thuật hiện đại, loại bỏ toàn bộ visual noise, font chữ sắc nét, độ tương phản cao, thao tác thuận tiện.
4. **Chất lượng mã nguồn:**
   - Tuyệt đối **không viết comment vào code** khi triển khai.
   - Toàn bộ test suite cũ và mới (unit tests, integration tests) đều đạt kết quả PASS (tỷ lệ 100%).
   - Tương thích đa nền tảng (Windows và Linux).

---

## 7. QUẢN TRỊ RỦI RO & CƠ CHẾ KHÔI PHỤC (RISK MITIGATION & ROLLBACK)

| Rủi ro tiềm ẩn | Mức độ | Biện pháp phòng ngừa & Xử lý |
| :--- | :---: | :--- |
| **Rò rỉ cổng COM khi mở 1 cổng thành công, 1 cổng thất bại** | Cao | Trong `DualSerialTransport.open()`, sử dụng cấu trúc `try...except` nghiêm ngặt: Nếu mở `_rx_serial` thất bại, ngay lập tức gọi `_tx_serial.close()` trong khối `except` trước khi re-raise ngoại lệ. |
| **Người dùng vô tình chọn nhầm chiều: TX gán vào cổng thu, RX gán vào cổng phát** | Trung bình | - Trên giao diện hiển thị nhãn gợi ý rõ ràng: `COM TX (Transmitter - Gửi)` và `COM RX (Receiver - Nhận)`.<br>- Nếu gửi lệnh Ping mà sau 1.5s không có Pong, giao diện hiển thị modal gợi ý: *"Không nhận được phản hồi. Vui lòng kiểm tra cáp quang hoặc thử đổi chéo cổng TX và RX."* |
| **Mất kết nối đột ngột trên 1 trong 2 cổng COM (Cáp lỏng, rút USB giữa chừng)** | Cao | Trong `SerialWorker._poll_serial()`, nếu `read()` hoặc `in_waiting` của cổng RX phát sinh `SerialException`, hoặc khi gửi dữ liệu qua cổng TX phát sinh lỗi, worker lập tức phát tín hiệu `error_occurred`, đóng cả 2 cổng và chuyển trạng thái UI sang `Disconnected`. |
| **Xung đột khi chạy nhiều tiến trình trên cùng 1 máy tính** | Trung bình | Duy trì cơ chế khóa file nguyên tử `InstanceLock` (`data/instance_A/app.lock` và `data/instance_B/app.lock`) đã được kiểm thử vững chắc. |
| **Gãy giao diện do biên dịch component thiếu file** | Thấp | Script `scripts/build_modular_web.py` có cơ chế kiểm tra `exists()` và validate cú pháp trước khi xuất file `index.html`. Luôn kiểm tra giao diện bằng headless test trước khi release. |

---
*Tài liệu kế hoạch được phê duyệt cho giai đoạn nâng cấp phần cứng và giao diện LabHongNgoai 10/2026.*
