# CHỈ MỤC TÀI LIỆU (DOCS INDEX)

**Dự án:** IR-OWC Lab Terminal — Desktop PC ↔ STM32F407 + MCP2122 + TFDU4101.
**Giao thức:** tương thích byte-for-byte với ứng dụng WinForm C# trong
`docs/WFF.txt`.

Thứ tự ưu tiên khi có mâu thuẫn:

1. Hành vi firmware thực tế và `docs/WFF.txt`.
2. Code đang chạy trong `src/`.
3. `docs/PROTOCOL.md` (wire format).
4. `docs/ARCHITECTURE.md` và `docs/ACCEPTANCE.md`.

---

## 1. Wire format hiện hành

Nguồn chuẩn: `docs/PROTOCOL.md`, `src/ir_terminal/protocol/constants.py`,
`framer.py`.

- Khung: `AA 55` + `sequence uint32 LE` + `length uint16 LE` + payload.
- Overhead: 8 byte. Payload tối đa 65535 byte; chunk dữ liệu là 256 byte.
- `sequence`: `0xFFFFFFFF` = HEADER, `0xFFFFFFFE` = END, còn lại là chỉ số chunk.
- HEADER payload: `nameLength uint16 LE` + tên UTF-8 + `totalPackets int32 LE`.
- END payload rỗng.
- Không opcode, không CRC, không ACK/NACK, không retransmit, không PING.
- Gửi open-loop, mỗi khung cách nhau
  `ceil(frameLength * 10 * 1000 / baud) + 10` ms.
- Nhận ghi DATA tại `sequence * 256`; thiếu gói tạo lỗ hổng byte 0 và kết quả
  là `PARTIAL`.
- COM1 dữ liệu quang: `2400/4800/9600/19200` bps. COM2 điều khiển: `115200`
  bps cố định.

## 2. Kiến trúc code

- `app.py`: DI container `Storage → SerialManager → FileTransfer/Telemetry/Chat
  → BackendBridge → WebMainWindow`; xử lý CLI, single-instance lock và broker
  in-app cho chế độ hai node.
- `config.py`: `LaunchConfig` + `InstanceContext` cho `data/instances/<A|B>/`.
- `core/dual_serial_transport.py`: mở COM1 + COM2, gửi mã baud và cấu hình lại
  COM1; `core/serial_worker.py` chạy trên `QThread`, đọc COM1, gọi
  `framer.parse_stream` và phát `packet_received`.
- `core/serial_manager.py`: điều phối worker, expose `connect_ports`,
  `send_packet`, `send_baud_command`, `reconfigure` qua worker.
- `core/socket_transport.py` và `dual_socket_transport.py`: transport TCP cho
  chế độ hai node; `mock_serial.py`: giả lập loss/latency/LOS.
- `protocol/`: `constants.py`, `framer.py`, `packet.py`; không còn opcode,
  CRC-32 hay payload large-file.
- `services/file_transfer_service.py`: engine open-loop duy nhất; TX đọc file
  theo offset, RX ghi file tạm `.ir_rx_*.part`, watchdog 3 giây, AutoSave.
- `services/chat_service.py`: đóng băng; `send_message` luôn báo lỗi vì giao
  thức không có khung chat.
- `services/telemetry_service.py`: throughput, loss, sniffer frame kiểu
  `FILE_HEADER` / `DATA_CHUNK` / `FILE_END`; checksum hiển thị `OPEN-LOOP`.
- `ui/backend_bridge.py`: slot COM1/COM2, baud, transfer, thư mục nhận,
  AutoSave, CSV; `ui/web/` là SPA QWebChannel.

## 3. Dữ liệu runtime

Mọi dữ liệu nằm dưới `<app root>/data/`; `IR_DATA_DIR` ghi đè, instance A/B
tách sang `data/instances/<ID>/`.

```text
data/
├── chat_history.json
├── file_transfers.json
├── telemetry_samples.json
├── settings.json
├── transfers/
├── logs/
└── instances/<A|B>/
    ├── chat_history.json / file_transfers.json / settings.json
    ├── transfers/  logs/  app.lock
    └── webengine/{cache,storage}
```

## 4. Chạy nhanh

```bash
uv sync --extra dev
uv run python -m ir_terminal
uv run pytest tests/ -v
uv run ruff check .
uv run mypy src/ tests/
```

Hai node trên một PC:

```bash
./scripts/run_dual_mock.sh
```

hoặc:

```bash
uv run python -m ir_terminal --instance A --link-role host --auto-connect --window-pos left
uv run python -m ir_terminal --instance B --link-role join --auto-connect --window-pos right
```

## 5. Bản đồ tài liệu

| File | Nội dung |
| :--- | :--- |
| `docs/PROTOCOL.md` | Wire format, pacing, lệnh baud, mã C mẫu |
| `docs/ARCHITECTURE.md` | Tầng code, quy chuẩn dev, threading, test |
| `docs/ACCEPTANCE.md` | Kết nối phần cứng và runbook nghiệm thu |
| `docs/WFF.txt` | Mã nguồn WinForm C# tham chiếu của firmware team |
| `docs/BAUD_SWITCHING_PLAN.md` | Kế hoạch lịch sử, đã được thay bằng §5 PROTOCOL |
| `docs/FILE_TRANSFER_REFACTOR_PLAN.md` | Kế hoạch lịch sử của engine file cũ |
| `docs/HARDWARE_COM_UART_ANALYSIS.md` | Phân tích phần cứng lịch sử |
| `docs/DUAL_INSTANCE_VIRTUAL_COM_ANALYSIS.md` | Phân tích hai instance lịch sử |
| `docs/IOT_STM32_AUDIT_REPORT.md` | Audit firmware lịch sử |
| `docs/plans/` | Kế hoạch UI và dual-COM lịch sử |
| `docs/spec/` | Audit pipeline TX/RX lịch sử |

Các file ghi "lịch sử" vẫn giữ giá trị tham khảo về phần cứng và phân tích lỗi,
nhưng mọi mô tả ARQ/CRC/opcode trong đó không còn đúng với code hiện tại.
