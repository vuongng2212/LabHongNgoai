# SỔ TAY TÍCH HỢP VÀ BÀN GIAO CHO ĐỘI IOT (IOT INTEGRATION RUNBOOK)
**Mục tiêu:** Hướng dẫn kết nối, cấu hình phần cứng và quy trình test liên thông giữa Phần cứng STM32/IrDA và Phần mềm Desktop PC.

---

## 1. SƠ ĐỒ KẾT NỐI VẬT LÝ & TÍN HIỆU (HARDWARE INTERFACE)

```text
[PC A (Desktop App)]
       │
       │ (Cáp Micro-USB / Type-C)
       ▼
[STM32F407G-DISC1 (Node A)] ──(USART2/3: TX, RX, GND)──> [Mạch MCP2122 + TFDU4101]
                                                                    │
                                                            (Tia Hồng Ngoại 940nm)
                                                                    ▼
[STM32F407G-DISC1 (Node B)] <──(USART2/3: TX, RX, GND)── [Mạch MCP2122 + TFDU4101]
       ▲
       │ (Cáp Micro-USB / Type-C)
       │
[PC B (Desktop App)]
```

### 1.1. Cấu hình Cổng giao tiếp UART chuẩn:
* **Baudrate:** `115200` bps
* **Data bits:** `8`
* **Parity:** `None`
* **Stop bits:** `1`
* **Flow Control:** `None` (Software ARQ tự điều phối)

---

## 2. YÊU CẦU ĐỐI VỚI FIRMWARE STM32

1. **Chế độ Transparent Bridge:**
   * Mọi byte nhận từ `USB-VCP (Virtual COM Port)` $\rightarrow$ Chuyển thẳng ra `UART_TX` nối với MCP2122.
   * Mọi byte nhận từ `UART_RX` của MCP2122 $\rightarrow$ Chuyển thẳng lên `USB-VCP` gửi về PC.
   * Không chèn thêm byte header/footer tùy biến của MCU vào luồng dữ liệu này.
2. **Quản lý Bộ đệm (Buffer Size):**
   * Sử dụng DMA hoặc Circular Ring Buffer tối thiểu **1024 Bytes** trên UART RX/TX để tránh mất byte khi PC truyền khối lượng lớn.
3. **Chống Optical Echo (Nếu mạch phần cứng chưa cô lập):**
   * Khi STM32 đang truyền dữ liệu ra MCP2122, nếu mắt thu TFDU4101 thu lại chính xung phát đó, STM32 nên tạm thời bỏ qua (ignore) các byte nhận được từ RX trong khoảng thời gian TX đang diễn ra.

---

## 3. QUY TRÌNH KIỂM THỬ TỪNG BƯỚC (STEP-BY-STEP TESTING WORKFLOW)

### Bước 1: Loopback Test trên mạch STM32 (Không qua Quang)
1. Nối tắt chân `TX` và `RX` trên cổng UART của STM32 (sau khi đã tháo MCP2122).
2. Mở Desktop App, chọn cổng COM của STM32, gửi 1 tin nhắn test.
3. **Kết quả đạt:** App nhận lại đúng tin nhắn đó và hiển thị RTT < 5ms.

### Bước 2: Loopback Test qua Mạch Quang MCP2122 + TFDU4101
1. Cắm module MCP2122 và TFDU4101 vào STM32.
2. Đặt một tấm gương phản xạ trước đầu phát/thu TFDU4101 để tia hồng ngoại phản xạ ngược lại chính nó.
3. Gửi chuỗi dữ liệu 256 bytes từ Desktop App.
4. **Kết quả đạt:** Dữ liệu nhận lại khớp 100%, CRC-32 hợp lệ.

### Bước 3: Liên thông 2 PC qua Không gian Quang (End-to-End Test)
1. Đặt 2 board mạch đối diện nhau ở khoảng cách $0.5m - 1.0m$.
2. Mở Desktop App trên PC A và PC B, kết nối đúng cổng COM tương ứng.
3. Bấm nút **"Ping / Handshake"** trên PC A $\rightarrow$ Kiểm tra PC B có phản hồi PONG không.
4. Thử nghiệm **Gửi Tin Nhắn Chat** và **Gửi File Ảnh (50KB - 200KB)**.
5. Thử nghiệm **Vật cản:** Dùng 1 bìa giấy chắn chùm tia trong 3 giây khi đang truyền file $\rightarrow$ App phải hiển thị `PAUSED` $\rightarrow$ Rút bìa giấy ra $\rightarrow$ App tự động `RESUME` và file được truyền thành công 100%, verify SHA-256 khớp.
