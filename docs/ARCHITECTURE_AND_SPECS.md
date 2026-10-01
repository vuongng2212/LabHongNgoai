# KIẾN TRÚC HỆ THỐNG VÀ ĐẶC TẢ GIAO THỨC TRUYỀN NHẬN HỒNG NGOẠI (IR-OWC)
**Dự án:** Thiết kế & Thi công mô hình truyền nhận dữ liệu giữa 2 máy tính qua Hồng ngoại cho phòng Lab  
**Vai trò:** Software Engineering Team  
**Phiên bản:** v1.0.0 (Tài liệu chuẩn hóa phục vụ MVP & Bàn giao IoT)

---

## 1. TỔNG QUAN KIẾN TRÚC (SYSTEM ARCHITECTURE)

Hệ thống được chia thành 4 tầng phân lớp rõ ràng (Layered Architecture):

```
+---------------------------------------------------------------+
|                    APPLICATION & UI LAYER                    |
|  - PySide6 GUI (Chat, File Transfer, Diagnostics Dashboard)   |
|  - Metrics Visualizer (Throughput, Packet Loss, BER)         |
+---------------------------------------------------------------+
                               │ (Qt Signals/Slots)
+---------------------------------------------------------------+
|                 SESSION & FLOW CONTROL LAYER                  |
|  - Session State Machine (IDLE, SENDING, RECEIVING, PAUSED)   |
|  - Stop-and-Wait ARQ Engine (Timeout, Retry, ACK/NACK)       |
|  - File Streaming / Chunking Engine + SHA-256 Verifier        |
|  - Priority Queue (Control Frame > Text > File Chunk)         |
+---------------------------------------------------------------+
                               │ (Byte Packets)
+---------------------------------------------------------------+
|                   DATA LINK / FRAMING LAYER                   |
|  - Length-based Frame Slicer & Parser (Sliding Window Scan)   |
|  - Integrity Checker: CRC-32 (IEEE 802.3 standard)           |
|  - Ghost Byte & Ambient Noise Filter                         |
|  - Duplicate Packet Filter (SEQ_NUM tracking)                 |
+---------------------------------------------------------------+
                               │ (Raw Bytes Stream)
+---------------------------------------------------------------+
|                   SERIAL I/O DRIVER LAYER                     |
|  - QThread Background Worker with Thread-safe Ring Buffer     |
|  - Auto-reconnect & Port Hotplug Watchdog                     |
|  - pyserial Engine (Direct COM / USB-UART STM32)             |
+---------------------------------------------------------------+
                               │ (USB-UART / CDC)
+---------------------------------------------------------------+
|                   PHYSICAL HARDWARE LAYER                     |
|  - STM32F407G-DISC1 (Transparent UART Bridge)                 |
|  - MCP2122 (IrDA Encoder/Decoder) + 74HC4060/4052 (Clock Gen) |
|  - TFDU4101 (Optical IR Transceiver - 940nm)                  |
+---------------------------------------------------------------+
```

---

## 2. ĐẶC TẢ GIAO THỨC TRUYỀN DỮ LIỆU (FRAME PROTOCOL SPECIFICATION)

Để đảm bảo chống nhiễu quang học và hỗ trợ nhiều loại dữ liệu, mọi gói tin trao đổi qua Serial đều tuân thủ cấu trúc khung nhị phân chuẩn dưới đây.

### 2.1. Cấu trúc Binary Frame

| Trường dữ liệu | Kích thước | Kiểu dữ liệu | Mô tả chi tiết |
| :--- | :--- | :--- | :--- |
| **PREAMBLE** | 2 Bytes | `0xAA 0x55` | Byte đồng bộ bắt đầu frame, dùng để định vị đầu khung khi bị trôi byte do nhiễu. |
| **FRAME_TYPE** | 1 Byte | `uint8` | Phân loại mục đích của gói tin (xem bảng Frame Types). |
| **SEQ_NUM** | 2 Bytes | `uint16` (Big-Endian) | Số thứ tự của packet (từ `0` đến `TOTAL_CHUNKS - 1`). Dùng cho ACK/NACK và chống duplicate. |
| **TOTAL_CHUNKS**| 2 Bytes | `uint16` (Big-Endian) | Tổng số chunk trong transaction. Với Chat/Control: đặt là `1`. |
| **PAYLOAD_LEN** | 2 Bytes | `uint16` (Big-Endian) | Độ dài thực tế của payload (tối đa 256 bytes ở MVP). |
| **PAYLOAD** | $N$ Bytes | `raw binary` | Dữ liệu thực tế ($N = \text{PAYLOAD\_LEN}$, $0 \le N \le 256$). |
| **CRC32** | 4 Bytes | `uint32` (Big-Endian) | Mã kiểm lỗi tính trên toàn bộ `[FRAME_TYPE + SEQ_NUM + TOTAL_CHUNKS + PAYLOAD_LEN + PAYLOAD]`. |
| **POSTAMBLE** | 2 Bytes | `0x55 0xAA` | Byte đánh dấu kết thúc frame. |

* **Header Overhead:** 9 Bytes (Preamble 2B + Type 1B + Seq 2B + Total 2B + Len 2B).
* **Footer Overhead:** 6 Bytes (CRC32 4B + Postamble 2B).
* **Tổng Overhead:** 15 Bytes/frame. Với chunk size = 128 Bytes $\to$ Hiệu suất truyền dẫn (Payload Efficiency) $\approx 89.5\%$.

---

### 2.2. Bảng phân loại Frame Type (Opcode)

| Type ID | Tên Frame | Hướng truyền | Mục đích & Ý nghĩa |
| :--- | :--- | :--- | :--- |
| `0x01` | **HANDSHAKE_PING** | Hai chiều | Kiểm tra kết nối LOS (Line-of-Sight), đo Round-Trip-Time (RTT). Payload: Timestamp. |
| `0x02` | **HANDSHAKE_PONG** | Hai chiều | Phản hồi Ping kèm trạng thái thiết bị. |
| `0x10` | **TEXT_MESSAGE** | Hai chiều | Gửi tin nhắn chat. Payload: chuỗi UTF-8. |
| `0x20` | **FILE_META** | TX $\to$ RX | Báo thông tin file sắp gửi. Payload: `File Size (4B)` + `File SHA256 (32B)` + `Filename (UTF-8)`. |
| `0x21` | **FILE_DATA** | TX $\to$ RX | Gửi 1 chunk dữ liệu file. Payload: Raw bytes cắt từ đĩa. |
| `0x30` | **ACK** | RX $\to$ TX | Xác nhận đã nhận thành công `SEQ_NUM`. Payload: Rỗng (0B). |
| `0x31` | **NACK** | RX $\to$ TX | Báo gói `SEQ_NUM` bị lỗi CRC hoặc hỏng format. Yêu cầu TX gửi lại ngay. |
| `0x40` | **SESSION_PAUSE** | Hai chiều | Tạm dừng truyền do nghẽn buffer hoặc phát hiện mất tín hiệu quang. |
| `0x41` | **SESSION_RESUME**| Hai chiều | Phục hồi tiến trình truyền từ chunk bị gián đoạn. |
| `0x42` | **SESSION_ABORT** | Hai chiều | Hủy ngang transaction (người dùng bấm Cancel hoặc Timeout quá ngưỡng). |

---

## 3. CƠ CHẾ FLOW CONTROL & STATE MACHINE (ARQ ENGINE)

### 3.1. Quy trình Stop-and-Wait ARQ (MVP)

```text
[PC A - TX]                                        [PC B - RX]
     │                                                  │
     │── 1. FILE_META (Name, Size, SHA256) ────────────>│ (Validate meta, tạo file tạm .part)
     │<─ 2. ACK (Seq: 0) ───────────────────────────────│
     │                                                  │
     │── 3. FILE_DATA (Seq: 0, Total: N, 128B) ────────>│ (Verify CRC32 -> Write to disk)
     │<─ 4. ACK (Seq: 0) ───────────────────────────────│
     │                                                  │
     │── 5. FILE_DATA (Seq: 1, Total: N, 128B) ────────>│ ── X (BỊ VẬT CẢN CHẮN TIA)
     │    [Timer Timeout: 350ms]                        │
     │── 6. RETRY 1: FILE_DATA (Seq: 1, 128B) ─────────>│ (Vật cản đã được bỏ ra -> Nhận tốt)
     │<─ 7. ACK (Seq: 1) ───────────────────────────────│
     │                      ...                         │
     │── 8. FILE_DATA (Seq: N-1, Last Chunk) ──────────>│ (Ghi xong -> Verify SHA256)
     │<─ 9. ACK (Seq: N-1) ─────────────────────────────│ (Rename .part -> Tên gốc)
     │                                                  │
```

### 3.2. Xử lý Edge Cases cốt lõi

1. **Vật cản quang kéo dài (> 5 lần Retry):**
   * Khi gửi 1 chunk mà retry đủ 5 lần không có ACK: TX chuyển State sang `BLOCKED / PAUSED`.
   * TX không drop session mà gửi gói `HANDSHAKE_PING` chu kỳ 1.0 giây/lần.
   * Khi người dùng tháo vật cản $\to$ RX nhận được PING và trả lời PONG $\to$ TX tự động gửi lại đúng chunk đang kẹt và tiếp tục phiên truyền bình thường.
2. **Duplicate Packet (Gói tin trùng do mất ACK):**
   * Nếu RX nhận được packet có `SEQ_NUM == last_received_seq`, RX bỏ qua không ghi đè vào file đĩa mà chỉ gửi lại `ACK(seq)` ngay lập tức để giải phóng bên gửi.
3. **Ghost Bytes / Ambient Noise:**
   * Thuật toán State-Machine Parser quét mảng byte liên tục. Nếu byte hiện tại không khớp Preamble `0xAA 0x55`, byte đó bị loại bỏ tức thì mà không làm hỏng frame tiếp theo.

---

## 4. GIAO DIỆN HỢP ĐỒNG GIỮA SOFTWARE VÀ IOT (IOT CONTRACT)

Phía Software đưa ra các yêu cầu kỹ thuật cố định cho Firmware STM32:

1. **Chế độ hoạt động:** STM32 hoạt động như một **Transparent USB-CDC to UART Bridge**. Toàn bộ dữ liệu từ cổng ảo USB trên PC được đẩy thẳng ra chân TX UART nối với MCP2122 và ngược lại.
2. **Baudrate cố định:** **`115,200 bps`** (Data bits: 8, Parity: None, Stop bits: 1, No Flow Control).
3. **Xử lý Echo quang:** Firmware STM32 cần kiểm tra module TFDU4101. Nếu mắt thu RX tự nhận lại xung phát của chính mình khi TX, STM32 cần disable ngắt RX trong thời gian phát hoặc báo cho Software để Software kích hoạt bộ lọc Echo Filter.
4. **Buffer Size trên MCU:** STM32 cần cấp phát bộ đệm Ring Buffer tối thiểu **1024 Bytes** cho RX và TX để đảm bảo không bị mất byte khi PC burst dữ liệu.
