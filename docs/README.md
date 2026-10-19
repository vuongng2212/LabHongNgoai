# CHỈ MỤC SPEC THỐNG NHẤT (SPEC INDEX — SINGLE SOURCE OF TRUTH)
**Dự án:** IR-OWC Lab Terminal — Desktop PC ↔ STM32F407 + MCP2122 + TFDU4101 @ 115200 8N1
**Ngày thống nhất:** 2026-10-19 — đối chiếu `LAB_BaoGia.xlsx` ↔ `codebase` ↔ `docs/` + `pytest 200 passed`.

> Quy tắc: khi có mâu thuẫn, thứ tự ưu tiên là:
> **1. `LAB_BaoGia.xlsx` (báo giá/phạm vi) → 2. Code thực tế (`src/`) → 3. `docs/PROTOCOL.md` (wire format) → 4. `docs/ARCHITECTURE.md / ACCEPTANCE.md`.**

Tài liệu trong `docs/`: `README.md` (index này) → `PROTOCOL.md` (wire + firmware) → `ARCHITECTURE.md` (code + quy chuẩn dev) → `ACCEPTANCE.md` (nghiệm thu HW + demo). Không còn file planning thừa.

---

## 1. Wire format chốt (đã implement, team IoT phải theo)

* Nguồn chuẩn: `docs/PROTOCOL.md` + `src/ir_terminal/protocol/constants.py` + `framer.py`.
* Frame: `[SOF 2B 0xAA55 LE] [SEQ 1B 0..255] [CMD 1B] [LEN 2B LE 0..512] [PAYLOAD N] [CRC32 4B LE IEEE 802.3 tính từ SEQ→PAYLOAD, không gồm SOF] [EOF 2B 0x55AA LE]`.
* Overhead: `6 + 6 = 12B`. Mặc định app: `chunk 240B` (`FileTransferService`), `MAX payload 512B` (chunk data tối đa `510B` legacy / `508B` large vì prefix `chunk_id`), `ACK timeout 250ms`, `retry tối đa 5 → FAILED`.
* **Không có giới hạn kích thước file ở tầng sản phẩm** (`MAX_FILE_SIZE_BYTES = 0` = unlimited). File nhỏ dùng payload legacy; khi cần > 65.535 chunk hoặc kích thước > `u32`, service tự chọn phần mở rộng large (`u64` size, `u32` chunk count/id). Giới hạn cấu trúc của extension là `0xFFFFFFFF × 508B ≈ 2.18 TB`.
* Opcode chốt: `0x02 DATA_TEXT | 0x01 DATA_CHUNK | 0x10 ACK | 0x11 NACK | 0x20 PING | 0x21 PONG | 0x30 TRANSFER_START | 0x31 TRANSFER_END | 0x32 TRANSFER_PAUSE/ABORT | 0x33 TRANSFER_RESUME (định nghĩa, chưa dùng cho auto-resume — Post-MVP) | 0x34 TRANSFER_START_LARGE | 0x35 DATA_CHUNK_LARGE | 0x36 TRANSFER_END_LARGE`.
* Payload legacy: `TRANSFER_START`: `file_size u32 + chunk_size u16 + total_chunks u16 + file_crc32 u32 + fname_len u8 + filename UTF-8`. `DATA_CHUNK`: `chunk_id u16 + data`. `TRANSFER_END`: `file_crc32 u32 + total_bytes u32`.
* Payload large: `TRANSFER_START_LARGE`: `file_size u64 + chunk_size u16 + total_chunks u32 + file_crc32 u32 + fname_len u8 + filename UTF-8` (header 19B). `DATA_CHUNK_LARGE`: `chunk_id u32 + data`. `TRANSFER_END_LARGE`: `file_crc32 u32 + total_bytes u64` (12B).
* `ACK/NACK`: `acked_seq u8 + status u8 (0x00 OK, 0x01 CRC, 0x02 ORDER, 0x03 TIMEOUT)`.
* Toàn vẹn file MVP = `CRC-32`. `SHA-256 end-to-end` là Post-MVP, chưa implement.

## 2. Kiến trúc code thực tế (đã implement)

* `src/ir_terminal/app.py`: DI container `Storage → SerialManager → Chat/File/Telemetry → BackendBridge → WebMainWindow`.
* `core/pyserial_transport.py` (8N1, `timeout 0.05 / write 0.2`) + `core/serial_worker.py` poll `10ms` HW / `2ms` mock trên `QThread` (Worker-Object, không block UI). `core/serial_manager.py` điều phối thread. `MOCK_*` dùng `MockSerialPort` cho dev/test; `LINK_A/B` dùng `SocketTransport`.
* `protocol/framer.py:pack_frame/parse_frames_from_buffer`: quét SOF, bỏ nhiễu, check `LEN≤512`, check EOF, check CRC — frame lỗi bị bỏ qua, chờ frame tiếp theo.
* `services/file_transfer_service.py`: **streaming hai chiều**. TX tính CRC-32 bằng pass đọc `1 MiB` và đọc từng chunk từ file handle đang mở (RAM `O(chunk_size)`, không `read_bytes()` toàn file). RX ghi trực tiếp chunk theo thứ tự vào file tạm `.ir_rx_*.part` trong `transfers_dir`, flush mỗi chunk, verify incremental `CRC-32` + kích thước rồi `os.replace` atomic sang tên cuối. State `HANDSHAKING→TRANSFERRING→FINALIZING→COMPLETED/FAILED`. Duplicate `START/END` do mất ACK được re-ACK idempotent; duplicate chunk cũ được re-ACK, chunk lệch thứ tự bị NACK. **Hủy đối xứng**: một bên gửi `TRANSFER_PAUSE` payload `ABORT`, hai bên ACK, đóng handle, xóa `.part`/partial, ghi failure và reset về `ABORTED`. Hết retry → `FAILED` (chưa tự resume).
* `services/chat_service.py`: gửi/nhận `DATA_TEXT` UTF-8, lưu file JSON, phát `frame_inspected` (hex/metadata).
* `services/storage_service.py`: file JSON `chat / file_transfers / telemetry_samples` + export CSV.
* `services/telemetry_service.py`: throughput cửa sổ trượt `3s`, `RTT` từ PONG, `loss = lost/(sent+lost)` (loss chỉ tăng khi `transfer_failed`), sniffer `200 records`, carrier hiển thị cố định `880nm NIR`, `CRC32 Valid`.
* `ui/main_window_web.py + backend_bridge.py + ui/web/`: `QWebEngineView + QWebChannel bridge` (SPA Tailwind). Slots: `scanPorts/connectPort/sendPing/sendChat/startFileTransfer/exportTelemetryCsv/...`.
* `utils/path_helpers.py`: mọi dữ liệu ghi (`chat_history.json`, `file_transfers.json`, `telemetry_samples.json`, `settings.json`, `transfers/`, `logs/`) nằm dưới `<app root>/data/` — dev là repo root, bản frozen là thư mục chứa `.exe`; `IR_DATA_DIR` ghi đè, dual-mock tách `data/instances/<A|B>/`.
* ⚠️ Chưa có trong MVP: `hotplug watchdog/auto-reconnect` (mới chỉ `connect/disconnect` tay), `priority queue Control>Text>File` dùng chung (mới chỉ test concurrent mock), `chống trùng toàn phiên nâng cao` (hiện đã idempotent START/END/chunk theo `seq`/`chunk_id`), `SHA-256 end-to-end`, `auto-resume sau chắn tia`, `BER đo thực`.

## 3. Đối chiếu báo giá MVP (19 dòng — `LAB_BaoGia.xlsx` sheet `Báo giá MVP`)

| STT | Đầu việc | Căn cứ code | Kết luận audit 2026-10-11 |
|-----|----------|-------------|---------------------------|
| 1–2 | UI 4 màn hình + bridge | `ui/web/components/*.html`, `main_window_web.py`, `app.py`, `backend_bridge.py`, `web/js/bridge_client.js` | Đã có |
| 3–4 | Serial thật + worker QThread | `core/serial_worker.py:PySerialTransport`, `serial_manager.py` | Đã có; chờ controller thật |
| 5 | Frame + CRC | `protocol/constants.py`, `framer.py`, `tests/unit/test_framer.py` | Đã có; team IoT chốt cùng wire format §1 |
| 6 | Mock | `core/mock_serial.py`, `test_mock_serial.py` | Đã có |
| 7–8 | Chat + lịch sử/hex | `services/chat_service.py`, `storage_service.py`, `chat_controller.js`, `test_chat_service.py` | Unit packet có; chưa E2E controller |
| 9–12 | File meta/chunk/ACK/timeout/RX/progress | `protocol/file_payloads.py`, `file_transfer_service.py`, `test_two_nodes_e2e_file_transfer.py` | Đã có, `240B/250ms/5 lần→FAILED`, CRC-32 |
| 13 | Lưu trữ file | `storage_service.py`, `test_storage_service.py` | Đã có |
| 14 | Sniffer/CSV | `telemetry_service.py`, `backend_bridge.py`, `test_telemetry_service.py` | UI/CSV có; `loss/BER` chưa phải số đo thực nghiệm (BER là text hiển thị `BER < 1e-9` trong HTML) |
| 15–16 | Test unit + 2-node mock + dual-link | `tests/unit/`, `tests/integration/` | Code có; **đã chạy 2026-10-19: 200 passed** |
| 17–18 | Build + docs | `scripts/build_standalone.py`, `ir_terminal.spec`, `README.md`, `docs/` | Script/doc có; build chưa xác minh trên máy sạch; wire spec đã thống nhất (§1) |
| 19 | Nghiệm thu 2 controller thật | cần board/firmware, chưa có log HW trong repo | **PENDING — phần còn lại của MVP (1.3 ngày / 1.950.000)** |

Tổng MVP `7.500.000 (5 ngày @1.500.000)`: code/tài liệu `3.7 ngày / 5.550.000` đã có, nghiệm thu HW `1.3 ngày / 1.950.000` đang chờ.

## 4. Post-MVP (báo riêng, không cộng vào MVP)

* Quan trọng (`6.000.000`): (1) tự resume sau chắn tia `2.400.000` — hiện `FAILED`, cần `PAUSE + ping định kỳ + resume đúng seq` + test che tia 3s; (2) SHA-256 toàn file `1.200.000` — hiện CRC-32, cần hash 2 đầu + hiển thị; (3) telemetry thực `2.400.000` — cần định nghĩa nguồn/công thức retry/loss/RTT, BER chỉ bật khi có counter firmware.
* Tiện ích (`2.250.000`): chống trùng chặt `750k`, hotplug/reconnect `750k` (gộp với hàng đợi `600k` theo sheet kế hoạch — xem file Excel để chốt ID). *Streaming file lớn `900k` nay đã có trong code (TX/RX streaming, không giới hạn dung lượng) — cần chốt lại phạm vi với khách hàng.*
* Điều kiện: BER cần dữ liệu firmware; resume cần thống nhất message/state với IoT.

## 5. Checklist nghiệm thu còn lại (theo `docs/ACCEPTANCE.md`)

1. Loopback STM32 (rút quang, nối tắt TX-RX): chat 1 tin → `RTT < 5ms`.
2. Loopback quang (gương trước TFDU4101): gửi `256B` → khớp 100%, CRC ok.
3. E2E 2PC `0.5–1m`: Ping→Pong, chat, file `50–200KB`, chắn bìa `3s` → hiện tại kỳ vọng `FAILED + gửi lại tay` (MVP); `PAUSED→RESUME tự động` là Post-MVP module 1.
4. Ghi log HW + CSV telemetry kèm theo để thanh toán hạng mục 19 (`LAB_BaoGia.xlsx` STT 19).
