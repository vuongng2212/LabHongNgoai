# CHECKLIST CHUẨN BỊ BUỔI RETRO UI/UX VÀ LẤY REQUIREMENT SÂU VỚI KHÁCH HÀNG
**Mục đích:** Bộ câu hỏi và khung làm việc giúp PM & Software Engineer chốt chính xác các yêu cầu chi tiết của khách hàng trong buổi retro sắp tới, tránh phát sinh trễ hạn trong 3 tuần MVP.

---

## 1. KHUNG GIAO DIỆN DESKTOP APP DỰ KIẾN (WIREFRAME CONCEPTS)

Desktop App được thiết kế theo phong cách Dark Mode chuyên nghiệp, chia thành 3 khu vực chính:

```text
+-------------------------------------------------------------------------------+
|  [IR-OWC Lab Terminal v1.0]                          [Port: COM3] [● CONNECTED]|
+-------------------------------------------------------------------------------+
| [💬 CHAT / TIN NHẮN]  |  [📁 TRUYỀN FILE / ẢNH]  |  [📊 THÔNG SỐ PHÒNG LAB]  |
+-------------------------------------------------------------------------------+
|                                                                               |
|  [Khu vực Chat / File]:                                                       |
|  - Danh sách tin nhắn / Logs                                                  |
|  - Khung Preview file/ảnh (Thumbnail, File size, SHA-256 Hash)                |
|  - Thanh Progress Bar (% hoàn thành, Tốc độ hiện tại: 9.8 KB/s, ETA: 12s)     |
|  - Nút điều khiển: [Gửi]  [Tạm dừng]  [Hủy phiên]                             |
|                                                                               |
|  [Trạng thái kênh truyền]:                                                    |
|  - Trạng thái quang: ● THÔNG TIA (LOS OK) / ⚠ BỊ CHẮN TIA (BLOCKED - PAUSED)   |
|  - Metrics nhanh: RTT = 42ms | Packet Loss = 1.2% | Retry Count = 3           |
+-------------------------------------------------------------------------------+
| Logs: [14:20:05] [TX] Sent Chunk 45/100 (128B) -> ACK received (40ms)        |
+-------------------------------------------------------------------------------+
```

---

## 2. CHECKLIST CÂU HỎI CẦN CHỐT VỚI KHÁCH HÀNG / PM TẠI BUỔI RETRO

### Nhóm 1: Yêu cầu về Nghiệp vụ & Dữ liệu (Business & Data Requirements)
1. **Loại dữ liệu chính cần demo trong buổi nghiệm thu:**
   * Khách hàng ưu tiên demo tin nhắn văn bản, truyền file ảnh (`.png`, `.jpg`) hay file tài liệu (`.pdf`, `.bin`)?
   * Kích thước file tối đa khách hàng kỳ vọng trong buổi bảo vệ/nghiệm thu là bao nhiêu (khuyến nghị phòng lab: dưới 1MB để thời gian truyền < 2 phút)?
2. **Kỳ vọng về UI/UX:**
   * Có cần hỗ trợ kéo thả file (Drag & Drop) vào cửa sổ không?
   * File nhận về có cần tự động mở xem trước (Auto Preview ảnh) ngay trên App sau khi truyền xong không?

### Nhóm 2: Yêu cầu về Báo cáo Thực nghiệm & Phòng Lab (Lab Diagnostics Requirements)
3. **Các chỉ số đo đạc cần xuất ra báo cáo:**
   * Khách hàng có cần tính năng xuất dữ liệu đo đạc (Throughput qua thời gian, tỷ lệ lỗi gói, số lần retry theo khoảng cách) ra file `.csv` hoặc `.xlsx` để làm biểu đồ báo cáo đề tài không?
   * Có cần vẽ đồ thị Throughput / BER thời gian thực ngay trên giao diện không?

### Nhóm 3: Kịch bản kiểm thử Edge Cases mà Khách hàng muốn thấy
4. **Kịch bản Demo vật cản:**
   * Khách hàng muốn App phản ứng thế nào khi ai đó lấy tay chắn chùm tia: Dừng tạm thời rồi truyền tiếp (Pause/Resume) hay Hủy phiên và yêu cầu gửi lại từ đầu? *(Software đề xuất: Auto Pause & Resume)*.
5. **Kịch bản kiểm tra toàn vẹn:**
   * Có cần nút bấm "Verify Integrity" để hiển thị mã SHA-256 đối chiếu giữa 2 máy trước hội đồng đánh giá không?

---

## 3. NGUYÊN TẮC TIẾP NHẬN YÊU CẦU MỚI TẠI BUỔI RETRO
* **Tiêu chí Scope Filter:**
  * Bất kỳ yêu cầu nào liên quan đến **Làm cho việc truyền nhận ổn định hơn, UI trực quan hơn để demo, hiển thị log/metrics rõ ràng** $\rightarrow$ Đưa ngay vào Sprint Tuần 2 (In-scope MVP).
  * Bất kỳ yêu cầu nào về **Mã hóa cao cấp phức tạp (như RSA/TLS quang), truyền video stream trực tiếp qua IrDA (vượt quá băng thông 115.2kbps)** $\rightarrow$ Đưa vào tài liệu định hướng Post-MVP trong báo cáo đề tài.
