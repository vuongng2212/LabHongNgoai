# NGHIỆM THU & BÀN GIAO (ACCEPTANCE RUNBOOK)

**Mục tiêu:** kiểm tra phần cứng STM32 + MCP2122 + TFDU4101 với giao thức
tương thích WinForm C#.
**Nguồn chuẩn:** `docs/PROTOCOL.md`, `docs/WFF.txt`.
**Index tài liệu:** `docs/README.md`.

---

## 1. SƠ ĐỒ KẾT NỐI

```text
[PC A] --USB--> [STM32 Node A] --UART--> [MCP2122 + TFDU4101]
                                                    |
                                            tia hồng ngoại 880 nm
                                                    |
[PC B] <--USB-- [STM32 Node B] <--UART-- [MCP2122 + TFDU4101]
```

### 1.1. Cấu hình cổng

| Cổng | Vai trò | Baudrate | Khung |
| :--- | :--- | :--- | :--- |
| COM1 | Dữ liệu quang | `2400`, `4800`, `9600`, `19200` | 8-N-1 |
| COM2 | Điều khiển MCU | `115200` cố định | 8-N-1 |

- Flow control: không dùng.
- Không có ARQ phần mềm; chất lượng kênh quyết định trực tiếp tỉ lệ mất chunk.
- App không dùng PING/PONG và không có khung chat. Nghiệm thu dựa trên truyền
  file và bộ đếm frame.

---

## 2. YÊU CẦU FIRMWARE STM32

1. **Transparent bridge:**
   - Byte nhận từ USB-VCP chuyển thẳng ra UART TX nối MCP2122.
   - Byte nhận từ UART RX của MCP2122 chuyển thẳng lên USB-VCP.
   - Không chèn header/footer riêng của MCU.

2. **Bộ đệm:**
   - DMA hoặc ring buffer tối thiểu 1024 byte mỗi chiều.
   - Khuyến nghị 2048 byte cho luồng quang.

3. **Nhận dạng khung:**
   - Sync `AA 55`, `sequence uint32 LE`, `length uint16 LE`, payload.
   - Chấp nhận `length` tới 256 cho DATA; không giả định khung đến liền nhau.

4. **Xử lý COM2:**
   - Nhận đúng chuỗi `00\r`, `01\r`, `10\r`, `11\r`.
   - Áp dụng Baudrate tương ứng cho UART nối MCP2122.
   - Không gửi phản hồi `BAUD_OK` hay `NOTIFY_BAUD`; giao thức không có kênh
     phản hồi.

5. **Chống optical echo:**
   - Nếu TFDU4101 thu lại tín hiệu của chính mạch, bỏ qua byte RX trong lúc TX.

---

## 3. QUY TRÌNH KIỂM THỬ

### Bước 1: Loopback UART trên STM32

1. Tháo MCP2122, nối tắt TX và RX của UART STM32.
2. Mở app, kết nối COM1 ở `19200`, COM2 để trống nếu board không cần điều
   khiển baud.
3. Gửi một file nhỏ (ví dụ 1 KB) từ tab Files.
4. **Đạt:** app nhận đủ packet, file nhận giống byte-for-byte với file gửi.
   So sánh bằng `sha256sum` hoặc `cmp` bên ngoài app.

### Bước 2: Loopback quang qua gương

1. Cắm MCP2122 + TFDU4101, đặt gương phản xạ trước module.
2. Gửi file 256 byte và file 100 KB.
3. **Đạt:** file nhận khớp hoàn toàn, không có trạng thái `PARTIAL`; log không
   có watchdog timeout.

### Bước 3: Liên thông hai PC

1. Đặt hai board đối diện, khoảng cách 0.5 m đến 1.0 m.
2. Mở app trên PC A và PC B, kết nối COM1 dữ liệu và COM2 điều khiển.
3. Đổi Baudrate quang từ PC A sang `9600`; kiểm tra PC B vẫn nhận đúng.
4. Gửi file 50 KB đến 200 KB theo cả hai chiều.
5. **Đạt:** hai file nhận khớp nguồn; trạng thái `SUCCESS`; lịch sử có record.

### Bước 4: Mất tia và mất gói

1. Chắn tia khoảng 1 đến 3 giây giữa phiên truyền.
2. **Kỳ vọng:** phiên nhận kết thúc bằng watchdog 3 giây với `FAILED`, hoặc
   nhận `END` với `PARTIAL` nếu chỉ mất một số chunk.
3. Bỏ vật cản, gửi lại file bằng tay.
4. **Đạt:** lần gửi lại `SUCCESS`; file `.partial` chỉ còn nếu AutoSave bật và
   phiên không hoàn tất.

---

## 4. KỊCH BẢN DEMO

| STT | Kịch bản | Thao tác | Kỳ vọng |
| :---: | :--- | :--- | :--- |
| 1 | Kết nối | Chọn COM1, COM2, Baudrate, bấm Connect | Header hiển thị COM1/COM2, telemetry bắt đầu đếm frame |
| 2 | Đổi Baudrate | Chọn `2400` đến `19200`, bấm áp dụng | COM2 nhận mã 2 ký tự, COM1 đổi tốc độ, không rớt kết nối |
| 3 | Truyền file | Chọn file, bấm Transmit | Tiến độ 0 đến 100 phần trăm, KB/s và ETA, kết thúc `SUCCESS` |
| 4 | Nhận file | Gửi từ máy còn lại | File xuất hiện trong thư mục nhận, tên và nội dung đúng |
| 5 | Hủy | Bấm Cancel khi đang truyền | Trạng thái `ABORTED`, file tạm xử lý theo AutoSave |
| 6 | Chắn tia | Chắn tia giữa phiên | `FAILED` hoặc `PARTIAL`, báo số packet thiếu trong lịch sử |
| 7 | Xuất báo cáo | Tab Analytics, Export CSV | File CSV telemetry và transfer history |

---

## 5. KIỂM TRA TỰ ĐỘNG

```bash
uv run pytest tests/ -v
uv run ruff check .
uv run mypy src/ tests/
```

Bộ test bao gồm byte-exact frame với WinForm C#, loopback paced streaming, và
vòng đời COM1/COM2 kèm lệnh baud.

---

## 6. PHẠM VI

- MVP gồm UI bốn màn hình, quản lý COM1/COM2, đổi Baudrate quang, truyền/nhận
  file open-loop, lịch sử, telemetry và CSV.
- Không thuộc phạm vi hiện tại: chat quang, PING/PONG, CRC/SHA-256, retransmit,
  auto-resume, BER thực đo, hotplug tự động.
- Nghiệm thu phần cứng thật vẫn cần board, firmware và log kèm theo.
