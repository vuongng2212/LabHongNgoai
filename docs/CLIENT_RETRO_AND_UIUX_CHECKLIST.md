# TÀI LIỆU HƯỚNG DẪN BUỔI BÁO CÁO NGHIỆM THU & DEMO VỚI KHÁCH HÀNG (CLIENT RETRO RUNBOOK)
**Dự án:** IR-OWC Lab Terminal v1.0 (Desktop Terminal Quang Không Dây Hồng Ngoại)  
**Khách hàng:** Phòng Lab Quang / Giảng viên hướng dẫn  

---

## 1. TỔNG QUAN HỆ THỐNG ĐÃ HOÀN THIỆN (FINAL DELIVERABLES)

Ứng dụng đã hoàn thành 100% phạm vi MVP theo thiết kế **Apple Modern Precision Light Theme**, hoạt động độc lập và ổn định trên nền tảng **PySide6 (Qt6) + QWebEngine + SQLite**:

1. **Screen 1 — Hardware Setup & Optical Alignment:**
   - Tự động nhận diện cổng UART 115,200 bps.
   - Vòng tròn căn chỉnh quang học (Optical Alignment Gauge 94%), đo độ trễ RTT Ping/Pong ($<35\text{ms}$).
   - Đèn chỉ báo trạng thái **CARRIER LOCKED** (Bước sóng $\lambda = 880\text{nm}$ NIR).

2. **Screen 2 — Dedicated File Transfer Station:**
   - Kéo thả file (Drag & Drop) hoặc chọn file qua Native Dialog.
   - Động cơ **Stop-and-Wait ARQ**: Cắt nhỏ file 240B/chunk, tự động retry khi timeout (250ms), tính toán tiến độ %, tốc độ truyền thực tế (KB/s), ETA và đối soát băm **CRC-32** toàn vẹn.
   - Bảng lịch sử truyền file lưu trữ bền vững trong SQLite kèm bộ lọc tìm kiếm.

3. **Screen 3 — Realtime Optical Chat Stream:**
   - Truyền nhận tin nhắn văn bản hai chiều qua tia hồng ngoại.
   - Khung **Raw Frame Inspector** hiển thị trực tiếp Hex Dump và Metadata của gói tin nhị phân.
   - Tự động nạp lại lịch sử chat khi mở app.

4. **Screen 4 — Lab Analytics & CSV Export:**
   - 4 thẻ KPI động: Throughput tức thời, BER ($10^{-10}$), Độ trễ RTT, Tổng số khối đã truyền.
   - Bảng Live Packet Sniffer thời gian thực kèm bộ lọc chip trạng thái.
   - Nút **Export CSV Telemetry** xuất file báo cáo thực nghiệm.

---

## 2. KỊCH BẢN DEMO TRỰC TIẾP TẠI BUỔI BÁO CÁO (DEMO AGENDA)

| STT | Kịch bản Demo | Thao tác thực hiện | Kỳ vọng hiển thị trên App |
| :---: | :--- | :--- | :--- |
| **1** | **Khởi tạo & Đo đạc Link quang** | Chọn cổng `COM` (hoặc `MOCK_COM_LOOPBACK`), bấm **Initialize Link**. | Header hiện **Connected (115200 bps)**, Gauge hiển thị **94%**, Badge chuyển **CARRIER LOCKED**, đo RTT Ping/Pong. |
| **2** | **Chat Quang Thời Gian Thực** | Chuyển sang Tab Chat, nhập text và bấm gửi. | Bóng chat màu xanh Cobalt xuất hiện bên phải, node đối tác phản hồi bên trái; khung Inspector hiển thị Hex Dump. |
| **3** | **Truyền File & Đối Soát Toàn Vẹn** | Chọn 1 file tài liệu/ảnh, bấm **Transmit Payload**. | Thanh tiến độ nhảy từ 0% $\to$ 100%, hiển thị tốc độ KB/s và ETA. Khi xong, xuất hiện dòng trạng thái **SUCCESS** kèm mã băm CRC-32. |
| **4** | **Thử Nghiệm Chắn Tia (LOS Resilience)** | Dùng bìa giấy chắn chùm tia hồng ngoại khi đang truyền. | Hệ thống phát hiện timeout, kích hoạt retry (tối đa 5 lần); khi bỏ vật cản, luồng truyền tự phục hồi thông suốt. |
| **5** | **Xuất Dữ Liệu Báo Cáo Thí Nghiệm** | Vào Tab Analytics, bấm **Export CSV Telemetry**. | Hệ thống xuất file `.csv` chứa toàn bộ mẫu đo kiểm phục vụ đưa vào Excel/MATLAB làm biểu đồ. |

---

## 3. DANH MỤC TÀI LIỆU BÀN GIAO CHO KHÁCH HÀNG

1. **`README.md`**: Hướng dẫn cài đặt, khởi chạy và đóng gói Standalone Executable.
2. **`docs/IOT_STM32_COMMUNICATION_SPECS.md`**: Bộ đặc tả giao thức nhị phân Little-Endian và mã nguồn C mẫu cho đội nhúng STM32.
3. **`docs/CONVENTIONS_AND_ARCHITECTURE.md`**: Bản quy chuẩn kiến trúc phần mềm, luồng Worker-Object và tiêu chuẩn Clean Code.
4. **`docs/screenshots/`**: Bộ ảnh chụp thực tế chất lượng cao của cả 4 màn hình.
