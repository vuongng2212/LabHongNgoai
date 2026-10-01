# KẾ HOẠCH TRIỂN KHAI VÀ PHÂN CHIA GIAI ĐOẠN (ROADMAP & MVP PLAN)
**Thời gian:** 3 tuần chạy nước rút (Sprint Timeline)  
**Mục tiêu tối thượng của MVP:** Hệ thống "WORK" hoàn hảo trên môi trường thực tế — 2 PC truyền nhận dữ liệu (Text, File, Ảnh) ổn định, có khả năng tự phục hồi khi có vật cản chắn sáng, có bộ tài liệu runbook/specs rõ ràng để bàn giao.

---

## 1. PHÂN CHIA PHẠM VI: PRE-MVP vs POST-MVP

Để đảm bảo tiến độ trong 3 tuần mà không bị vỡ kế hoạch, phạm vi công việc được phân chia nghiêm ngặt:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            PHẠM VI TRONG MVP                                │
│                   (Tập trung cốt lõi: Làm cho nó WORK)                      │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. Core Serial Engine: Connect COM, auto-reconnect, hotplug, non-blocking.  │
│ 2. Framing & Reliability: Binary frame, CRC-32, Stop-and-Wait ARQ.          │
│ 3. Text Message Transfer: Chat thời gian thực 2 chiều (Half-Duplex safe).   │
│ 4. File / Image Transfer: Chia chunk (128B), SHA-256 verify, progress bar. │
│ 5. Physical Blockage Recovery: Tự động Pause khi mất tia, Resume khi thông. │
│ 6. Modular Clean Architecture: Dễ mở rộng sau khi client retro thêm spec.   │
│ 7. Giao diện Desktop trực quan: PySide6 dark-theme, tab Chat + File + Port. │
│ 8. Tài liệu bàn giao hoàn chỉnh: Specs, Protocol, IoT Runbook.              │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            PHẠM VI SAU MVP (POST-MVP)                       │
│                        (Tối ưu hóa & Tính năng nâng cao)                    │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. Tăng tốc độ: Nâng cấp lên Sliding Window / Go-Back-N (Window size 4-8).  │
│ 2. Nén dữ liệu: Tích hợp Zstandard/Gzip on-the-fly trước khi chunk file.   │
│ 3. Kênh ghép đa nhiệm (Multiplexing): Vừa truyền file vừa chat song song.   │
│ 4. Dashboard Lab chuyên sâu: Đồ thị BER/SNR/Throughput theo thời gian thực.  │
│ 5. Mã hóa bảo mật quang: AES-128 / AES-GCM mã hóa payload chống nghe lén.   │
│ 6. Điều khiển từ xa: Tích hợp điều khiển LCD STM32 từ PC.                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. TIMELINE 3 TUẦN CHẠY NƯỚC RÚT (3-WEEK SPRINT TIMELINE)

### TUẦN 1: CORE ENGINE, PROTOCOL & GIẢ LẬP MOCK (KHÔNG PHỤ THUỘC PHẦN CỨNG)
* **Mục tiêu:** Hoàn thiện 100% tầng Protocol, Flow Control, Unit Test và giả lập truyền nhận qua 2 cổng COM ảo (com0com / Virtual Serial).
* **Công việc cụ thể:**
  * **Ngày 1-2:** Thiết lập cấu trúc dự án Python/PySide6 chuẩn MVC/Clean Architecture. Viết module `Protocol` (Header, CRC32, Framing, Chunking).
  * **Ngày 3-4:** Viết `SerialWorker` (QThread), `RingBuffer`, cơ chế Stop-and-Wait ARQ (Timeout, ACK/NACK, Duplicate prevention).
  * **Ngày 5:** Viết bộ Unit Test & Mock Simulator giả lập rơi rớt gói (Drop rate 10-30%) và giả lập vật cản chắn sáng (Pause/Resume).
  * **Cuối tuần 1:** Chuẩn bị sẵn tài liệu specs & wireframe sơ bộ để họp retro với PM/Khách hàng.

### TUẦN 2: DESKTOP UI/UX & TÍCH HỢP FEEDBACK CLIENT SAU BUỔI RETRO
* **Mục tiêu:** Dựng hoàn chỉnh Desktop App với PySide6, tích hợp toàn bộ nghiệp vụ Chat + File Transfer và cập nhật các requirement mới từ buổi retro.
* **Công việc cụ thể:**
  * **Ngày 8-9:** Nhận requirement chi tiết từ buổi retro với khách hàng $\to$ Điều chỉnh Data Models và UI Components tương ứng.
  * **Ngày 10-11:** Xây dựng UI chính:
    * Panel cấu hình kết nối Serial (Port scan, Baudrate, Status indicator).
    * Panel Chat thời gian thực.
    * Panel Truyền File (Kéo thả file, Preview ảnh/file, Progress bar, Tốc độ KB/s, ETA, Nút Pause/Resume/Cancel).
  * **Ngày 12:** Ghép nối UI với Core Engine thông qua Qt Signals/Slots (đảm bảo UI 60 FPS mượt, không bị freeze khi stream I/O).

### TUẦN 3: TÍCH HỢP PHẦN CỨNG IOT THỰC TẾ, STRESS TEST & ĐÓNG GÓI BÀN GIAO
* **Mục tiêu:** Cắm thực tế vào mạch STM32 + MCP2122 + TFDU4101, cân chỉnh tham số vật lý và xuất file `.exe` độc lập.
* **Công việc cụ thể:**
  * **Ngày 15-16:** Tích hợp với board phần cứng của đội IoT trong phòng lab. Đo đạc RTT thực tế, cân chỉnh `TIMEOUT_MS` (200ms – 500ms) và `CHUNK_SIZE` (64B – 256B).
  * **Ngày 17-18:** Stress testing với các điều kiện môi trường:
    * Che chắn tia hồng ngoại đột ngột bằng bìa/tay $\to$ Verify tính năng Auto-Pause & Resume.
    * Đổi góc nghiêng LED ($15^\circ, 30^\circ, 45^\circ$) và khoảng cách ($0.5m \to 3m$).
    * Truyền file kích thước đa dạng (Text 10KB, Ảnh JPG 200KB, File nén 1MB).
  * **Ngày 19-20:** Đóng gói ứng dụng thành file thực thi độc lập (PyInstaller Standalone `.exe`).
  * **Ngày 21:** Hoàn thiện báo cáo, tài liệu Runbook và bàn giao cho khách hàng & phòng lab.

---

## 3. MA TRẬN RỦI RO & PHƯƠNG ÁN DỰ PHÒNG (RISK MANAGEMENT)

| Rủi ro kỹ thuật | Mức độ | Phương án xử lý của Software Team |
| :--- | :--- | :--- |
| **Phần cứng IoT bị trễ tiến độ làm mạch** | **Cao** | Software phát triển 100% độc lập bằng Virtual COM Port và cặp cáp USB-UART TTL nối chéo. Đến tuần 3 chỉ cần cắm vào mạch thật là chạy ngay. |
| **Khách hàng thay đổi / thêm requirement trong buổi retro** | **Trung bình** | Thiết kế Clean Architecture tách rời giữa UI và Core Protocol. Mọi thay đổi về định dạng dữ liệu chỉ cần sửa ở tầng Model/View mà không làm vỡ tầng Transport Serial. |
| **Môi trường quang quá nhiễu làm rớt gói liên tục** | **Trung bình** | Protocol hỗ trợ hạ kích thước `CHUNK_SIZE` xuống 64 bytes trực tiếp trên giao diện để tăng tỷ lệ truyền thành công. |
