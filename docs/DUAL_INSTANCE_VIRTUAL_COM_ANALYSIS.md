# Phân tích kỹ thuật: Chạy 2 instance LabHongNgoai trên 1 PC Windows

**Ngày khảo sát:** 2026-10-20  
**Phạm vi:** đọc codebase `LabHongNgoai` tại commit `main` hiện tại; phân tích xung đột tài nguyên khi 2 app `.exe` chạy đồng thời; khảo sát các giải pháp Virtual COM Pair trên Windows; đề xuất kiến trúc và lộ trình.

---

## 0. Kết luận điều hành (TL;DR)

**Hiện trạng:** codebase **đã có một phần hạ tầng dual-instance**, nhưng chưa đủ để đóng gói cho client dùng 2 file `.exe` độc lập:

- Đã có: `--instance A|B` / `IR_INSTANCE` tách `data/instances/<A|B>/` (JSON, transfers, logs); `MockSerialPort` cho 1 app; `SocketTransport` + broker TCP `scripts/virtual_optical_link.py` cho 2 app.
- Chưa có: broker không được đóng gói vào `.exe`; không có cờ CLI chọn cổng/link/auto-connect; QtWebEngine vẫn dùng **chung một profile/cache** giữa 2 process; không có lock chống mở trùng instance; không có launcher Windows 1-click; `--instance` không ảnh hưởng tới danh tính UI, tiêu đề cửa sổ, hay cổng kết nối.

**Khuyến nghị:** chọn **Phương án C (Hybrid)**:

1. **Mặc định cho client/demo:** Phương án B — link TCP loopback nhúng sẵn trong app (`--link-role host|join`), **không cần driver, không cần admin, chạy được với Secure Boot bật**, 1 shortcut khởi động 2 node. Đây là giải pháp nên làm trước.
2. **Tuỳ chọn cho nghiệm thu phần cứng thật:** Phương án A — cặp COM ảo ngoài. Nếu bắt buộc phải là `COM11`/`COM12`, dùng **VSPD (Virtual Serial Port Driver – Eltima, WHQL, trả phí)** hoặc **VSPE (Eterlogic, ký số, trả phí)**. **Không khuyến nghị com0com cho máy client** vì rủi ro `Code 52`/Secure Boot và chính sách ký driver Windows 2026; com0com chỉ nên dùng trong lab có kiểm soát.
3. **Không build 2 bản `.exe` khác nhau.** Build **một bản** (one-folder PyInstaller) rồi dùng shortcut/tham số `--instance A|B` để tạo 2 node. Nếu buộc phải có 2 file `.exe` riêng, chỉ nên **copy cùng một build** sang 2 thư mục, không build từ 2 nhánh code.

**Điểm chặn lớn nhất cần xử lý ngay:** QtWebEngine profile/cache dùng chung giữa 2 process (dễ gây trắng trang, chậm tải, lỗi lock cookie/cache), và broker dual-link hiện không nằm trong bản đóng gói.

### 0.1. Trạng thái triển khai (2026-10-20)

Phương án C đã được hiện thực ở mức **zero-driver (Option B)** và đã kiểm thử:

| Hạng mục | Trạng thái | Vị trí |
|---|---|---|
| CLI đầy đủ + `LaunchConfig`/`InstanceContext` | ✅ | `src/ir_terminal/config.py` |
| Tách WebEngine profile/cache theo instance | ✅ | `ui/main_window_web.py`, `utils/path_helpers.get_webengine_dirs` |
| Logger trỏ đúng `instances/<ID>/logs` trước khi mở file | ✅ | `utils/logger.setup_logger` |
| Single-instance lock (`--allow-multiple`) | ✅ | `utils/instance_lock.py` |
| Broker đóng gói trong app (host in-process) | ✅ | `core/link_broker.py`; `scripts/virtual_optical_link.py` chỉ còn wrapper |
| Endpoint LINK động + retry + token + phát hiện mất link | ✅ | `socket_transport.py`, `transport_factory.py`, `serial_worker.py`, `serial_manager.py` |
| Auto-connect, title `[Node A - Host]`, chia trái/phải | ✅ | `app.py`, `ui/main_window_web.py`, `ui/web/js/bridge_client.js` |
| Launcher Windows double-click + copy cạnh `.exe` | ✅ | `scripts/Start_Dual_Demo.cmd`, `scripts/Start_Com_Pair.cmd`, `scripts/build_standalone.py` |
| Test mới | ✅ | `tests/unit/test_config.py`, `test_link_broker.py`, `test_instance_lock.py`, `tests/integration/test_dual_link_e2e.py` |

Kết quả kiểm tra: `ruff` sạch, `mypy src/ tests/` sạch, `pytest` **234 passed**.
Chạy thực 2 process offscreen xác nhận cả hai node tự kết nối và log tách riêng.

**Còn lại (không chặn demo):** nghiệm thu COM ảo VSPD/VSPE trên máy Windows thật; auto-resume sau chắn tia (Post-MVP); build `.exe` và xác minh double-click trên Windows sạch.

---

## 1. Khảo sát hiện trạng (As-Is)

### 1.1. Bản đồ thành phần liên quan

| Thành phần | File | Vai trò hiện tại |
|---|---|---|
| Entry point | `src/ir_terminal/__main__.py` | Gọi `run()` |
| Bootstrap/DI | `src/ir_terminal/app.py` | `create_app(instance)`, `run()`; parse `--instance`; gắn logger, services, window |
| Path helpers | `src/ir_terminal/utils/path_helpers.py` | `get_app_data_dir/get_transfers_dir/get_logs_dir`, `IR_DATA_DIR`, `IR_INSTANCE`, `is_dual_mock_enabled` |
| Logger | `src/ir_terminal/utils/logger.py` | Console + `RotatingFileHandler` + Qt signal handler |
| Serial manager | `src/ir_terminal/core/serial_manager.py` | QThread + worker lifecycle; API `connect_port/disconnect_port/get_port_list` |
| Serial worker | `src/ir_terminal/core/serial_worker.py` | Mở transport, poll 10ms HW / 2ms mock, parse frame, hotplug scan |
| Transport factory | `src/ir_terminal/core/transport_factory.py` | `MOCK*` → Mock; `LINK_*` → Socket; còn lại → PySerial |
| PySerial transport | `src/ir_terminal/core/pyserial_transport.py` | COM thật/virtual, 8N1, timeout 0.05 / write 0.2 |
| Socket transport | `src/ir_terminal/core/socket_transport.py` | TCP client tới broker, map cứng `LINK_A=6001`, `LINK_B=6002` |
| Mock transport | `src/ir_terminal/core/mock_serial.py` | Loopback nội bộ 1 process, auto PONG/ACK, mô phỏng loss/latency/LOS |
| Broker (mock) | `scripts/virtual_optical_link.py` | TCP relay A↔B, loss/latency/LOS, đọc stdin để toggle LOS |
| Launcher dual (dev) | `scripts/run_dual_mock.sh` | Bash: bật env `IR_ENABLE_DUAL_MOCK=1`, chạy broker + 2 instance |
| Storage | `src/ir_terminal/services/storage_service.py` | JSON `chat_history/file_transfers/telemetry_samples/settings.json`, ghi atomic bằng `.tmp` + `os.replace` |
| File transfer | `src/ir_terminal/services/file_transfer_service.py` + `services/file_transfer/` | ARQ, streaming, `transfers_dir`, publish file nhận |
| Web UI | `src/ir_terminal/ui/main_window_web.py` | `QWebEngineView` + `QWebChannel`; tạo `WebAppPage(self._view)` bằng **default profile** |
| Bridge | `src/ir_terminal/ui/backend_bridge.py` | Slots: `scanPorts/connectPort/sendPing/sendChat/startFileTransfer/...` |
| Build | `ir_terminal.spec`, `.github/workflows/*.yml` | PyInstaller one-folder, **chỉ 1 EXE**, không bundle broker |

### 1.2. Những gì đã có

**a) Cách ly dữ liệu theo instance (một phần).**  
`path_helpers.get_app_data_dir(instance)` (`path_helpers.py:89`) chèn `instances/<A|B>/` khi có `--instance` hoặc `IR_INSTANCE`:

```text
<app root>/data/
├── instances/A/{chat_history.json,file_transfers.json,telemetry_samples.json,settings.json,transfers/,logs/}
└── instances/B/{...}
```

`app.py:80-88` đã truyền data dir/transfers dir theo instance vào `StorageService` và `FileTransferService`. `app.py:39-60` có đoạn “re-point” file log handler sang log dir của instance sau khi `setup_logger()` chạy.

**b) Ba loại transport sau interface `ITransport`.**  
`transport_factory.create_transport()` (`transport_factory.py:15`):

- `MOCK*` → `MockSerialPort`: mô phỏng 1 process; `MOCK_COM_LOOPBACK` tự trả PONG/ACK.
- `LINK_A` / `LINK_B` → `SocketTransport`: kết nối TCP tới broker.
- Tên khác → `PySerialTransport`: mở COM thật hoặc COM ảo.

**c) Broker TCP cho 2 app.**  
`scripts/virtual_optical_link.py` mở 2 cổng `127.0.0.1:6001` (A) và `6002` (B), relay byte thô A↔B; có `--loss`, `--latency-ms`, `--los-block`. `SocketTransport` map endpoint cứng tại `socket_transport.py:25-28`.

**d) Launcher dev và test dual-link.**  
`scripts/run_dual_mock.sh` chạy broker + 2 app với `IR_ENABLE_DUAL_MOCK=1`. `tests/integration/test_dual_link_e2e.py` chạy 2 `SerialManager` qua broker in-process.

### 1.3. Những gì chưa có / lỗ hổng

| # | Lỗ hổng | Bằng chứng | Ảnh hưởng tới client |
|---|---|---|---|
| G1 | Broker không được đóng gói vào `.exe` | `ir_terminal.spec` chỉ `Analysis(['src/ir_terminal/__main__.py'])`; `scripts/virtual_optical_link.py` không được import/bundle; launcher là `.sh` | Client không có Python/`uv` → **không thể chạy dual-link từ bản phát hành** |
| G2 | Không có CLI chọn transport/cổng/link | `app.py:117+` chỉ parse `--instance` (dùng `parse_known_args`) | Không thể “1-click” chọn `COM11`, `LINK_A`, auto-connect |
| G3 | `IR_ENABLE_DUAL_MOCK` phải set bằng env | `path_helpers.py:144`; `serial_worker.py:397` | Double-click shortcut không set được env; `LINK_A/B` không hiện trong dropdown |
| G4 | QtWebEngine dùng default profile | `main_window_web.py:118-120`: `WebAppPage(self._view)` → default profile | 2 process tranh chấp cache/cookie DB → trắng trang/chậm/lỗi lock |
| G5 | Không có lock chống mở trùng instance | Không có `QLockFile`/`QSharedMemory`/named mutex trong repo | Mở 2 lần cùng `--instance A` → 2 app cùng mở `COM11`/cùng broker |
| G6 | `--instance` không ảnh hưởng danh tính/tiêu đề/cổng | `main_window_web.py:107` tiêu đề cố định; `chat_service.py:40-41` tên `Local Station Alpha`/`Remote Station Bravo` cố định | Demo 2 node khó phân biệt; chat 2 bên hiển thị sai vai |
| G7 | Port scan không thấy một số COM ảo | `serial_worker.py:406` chỉ dùng `serial.tools.list_ports.comports()` | com0com bản cũ có thể không nằm trong class “Ports” → app không thấy COM ảo |
| G8 | `SocketTransport` không reconnect/không báo mất peer | `socket_transport.py` set `_is_open=False` trong `_fill` nhưng không phát signal | UI vẫn “Connected” khi broker/peer chết; ping timeout khó hiểu |
| G9 | Broker dùng `SO_REUSEADDR` | `virtual_optical_link.py` `_accept_loop` | Trên Windows có thể bind trùng cổng với process khác, chọn sai broker |
| G10 | JSON store chỉ lock trong process | `storage_service.py:576` dùng `threading.Lock` | Nếu 2 app chung data root: mất update, trùng id, tranh `.tmp` |
| G11 | Log handler khởi tạo trước khi biết instance | `app.py:37` gọi `setup_logger()` rồi mới `get_logs_dir(inst)` | Cả 2 process mở `data/logs/app.log` trước khi re-point; rotation có thể lỗi |
| G12 | Tên instance chưa được validate | `get_instance_name` chỉ `.strip().upper()` | `--instance ../../x` tạo đường dẫn ngoài ý muốn |

### 1.4. Luồng transport hiện tại

```text
[Web UI]
   │ connectPort(port, baud)
   ▼
[BackendBridge] → [SerialManager] ──signal──▶ [SerialWorker] (QThread)
                                                     │ create_transport(port, baud)
                          ┌──────────────────────────┼──────────────────────────┐
                          ▼                          ▼                          ▼
                   PySerialTransport            MockSerialPort            SocketTransport
                   (COM thật/COM ảo)         (loopback 1 process)      (TCP client)
                                                                             │
                                                              ┌──────────────┴──────────────┐
                                                              ▼                             ▼
                                                     127.0.0.1:6001                127.0.0.1:6002
                                                     (LINK_A)                      (LINK_B)
                                                              └──── VirtualOpticalLink ────┘
                                                                    (script Python rời)
```

**Kết luận mục 1:** hạ tầng dual-instance hiện là **“dev-mode”**, chưa phải **“client-mode”**. Cần bổ sung lớp cấu hình khởi động (LaunchConfig), đóng gói broker/link, và cách ly WebEngine + lock instance.

---

## 2. Phân tích xung đột khi 2 app chạy trên 1 máy Windows

### 2.1. Ma trận tài nguyên

| # | Tài nguyên | Nếu 2 app dùng chung | Nếu có `--instance A/B` | Mức độ |
|---|---|---|---|---|
| 1 | `data/*.json` (chat/transfer/telemetry/settings) | Lost-update, trùng id, tranh `.tmp`, có thể corrupt | Mỗi instance 1 cây `instances/<id>/` → an toàn | 🔴 Critical nếu chung |
| 2 | `settings.json` | A đổi receive dir ảnh hưởng B; ghi đè lẫn nhau | Tách riêng | 🔴 Critical nếu chung |
| 3 | `transfers/` nhận file | Cùng tên file → ghi đè/đua `os.replace`; `.part` dùng `mkstemp` nên tên tạm an toàn | Tách riêng | 🟠 High |
| 4 | `logs/app.log` | 2 `RotatingFileHandler` cùng file; rotation Windows có thể fail; log xen kẽ | Tách riêng (nhưng cần sửa thứ tự khởi tạo) | 🟠 High |
| 5 | QtWebEngine profile/cache/cookie | Lock cache/cookie SQLite; trắng trang, chậm, lỗi `Unable to move the cache` | **Hiện tại vẫn chung** → cần sửa code | 🔴 Critical |
| 6 | Single-instance lock | Không có lock → mở trùng A | Vẫn không có lock → mở trùng A/B | 🟠 High |
| 7 | Cổng COM (thật/ảo) | Windows mở COM độc quyền; app thứ 2 `Access is denied` | Vẫn phải chọn đúng 2 đầu cặp COM | 🔴 Critical nếu chọn trùng |
| 8 | TCP broker `6001/6002` | 2 broker hoặc process khác chiếm cổng; `SO_REUSEADDR` làm hành vi khó đoán | Link chưa được cấu hình theo instance | 🟠 High |
| 9 | Danh tính UI/chat | Cả 2 node tự nhận `Local Station Alpha`, nhận `Remote Station Bravo` | Hiện chưa đổi theo instance | 🟡 Medium |
| 10 | `QApplication`/`QStandardPaths` | Cùng `applicationName` → cùng thư mục `%LOCALAPPDATA%` cho WebEngine | Chưa đổi | 🔴 Critical (liên quan #5) |
| 11 | GPU/QtWebEngineProcess | Mỗi process tự spawn; không xung đột trực tiếp | – | 🟢 Low |

### 2.2. Phân tích chi tiết các xung đột chính

#### (1) JSON store: atomic ≠ serializable giữa 2 process

`StorageService` ghi kiểu “read-modify-write”:

```python
# storage_service.py (khái quát)
with self._lock:                     # chỉ có tác dụng trong 1 process
    records = self._load_list(filename)   # đọc cả file
    records.append(record)                # sửa trong RAM
    self._save_list(filename, records)    # ghi .tmp rồi os.replace
```

Khi 2 process cùng `data/`:

- A đọc list 100 phần tử, B đọc list 100 phần tử.
- A ghi id 101, B cũng ghi id 101 → **last-writer-wins**, mất bản ghi của bên kia.
- Cả hai dùng cùng `settings.json.tmp` → có thể `PermissionError`/`FileNotFoundError` khi `replace`.
- `os.replace` đảm bảo “không thấy file nửa vời”, **không** đảm bảo cộng dồn giữa 2 process.

→ Bắt buộc tách instance dir, hoặc thêm file-lock liên process (không khuyến nghị vì tăng phức tạp).

#### (2) Log: mở cùng file, rotation trên Windows

`setup_logger()` (`logger.py:174`) tạo `RotatingFileHandler(data/logs/app.log)`. `app.py:37` gọi **trước khi biết instance**, sau đó mới cố re-point (`app.py:39-60`). Hệ quả:

- Cả 2 process đã mở `data/logs/app.log` trong vài chục ms đầu.
- Khi rotate, process giữ handle có thể làm `rename` thất bại trên Windows, log bị mất/xen kẽ.

**Cách sửa đúng:** tính `get_logs_dir(inst)` **trước**, rồi gọi `setup_logger(log_dir=...)`; bỏ đoạn re-point.

#### (3) QtWebEngine: xung đột “ẩn” nhưng nghiêm trọng nhất

`main_window_web.py:118-120` tạo page bằng default profile:

```python
self._view = QWebEngineView(self)
self._page = WebAppPage(self._view)      # ← default QWebEngineProfile
self._view.setPage(self._page)
```

Qt yêu cầu storage name/path của profile **duy nhất trong vòng đời ứng dụng**; nếu 2 process cùng dùng default profile, chúng cùng trỏ vào:

```text
%LOCALAPPDATA%\LabHongNgoai\IR-OWC Lab Terminal\QtWebEngine\Default\
├── Cache\
├── Cookies (SQLite)
└── ...
```

Triệu chứng thường gặp (đã được báo cáo trong Qt forum và dự án khác):

- WebEngine load chậm/trắng trang ở process thứ hai.
- Log Chromium: `Unable to move the cache: Access is denied`, `database is locked`.
- Cookie/localStorage hỏng hoặc mất.

→ Phải tạo `QWebEngineProfile` riêng cho từng instance (và/hoặc từng data root), trỏ `cachePath`/`persistentStoragePath` vào `data/instances/<id>/webengine/`.

#### (4) Không có lock instance

Repo không có `QLockFile`, `QSharedMemory`, named mutex hay `QLocalServer`. Với dual-link/COM:

- Người dùng double-click 2 lần cùng shortcut A → 2 app A cùng mở `LINK_A`/`COM11`; broker có thể ngắt kết nối cũ, dữ liệu chồng chéo.
- Không có cảnh báo “Instance A already running”.

→ Thêm `QLockFile(data_dir/"app.lock")` theo instance (khác nhau giữa A/B), có cờ `--allow-multiple` cho debug.

#### (5) Cổng COM độc quyền

Windows mở COM theo kiểu exclusive. Với cặp COM ảo:

- A mở `COM11`, B mở `COM12` → OK.
- Nếu cả 2 chọn `COM11` → app thứ 2 nhận `PermissionError: could not open port 'COM11': Access is denied`.

App hiện **không kiểm tra** hai instance đang dùng cùng cổng, cũng không tự gán cổng theo instance. Cần: `--port`/`--instance` gợi ý cổng, cảnh báo khi chọn trùng, thông điệp lỗi rõ ràng.

#### (6) Broker TCP và `SO_REUSEADDR`

`virtual_optical_link.py` set `SO_REUSEADDR` khi bind `127.0.0.1:6001/6002`. Trên Windows, `SO_REUSEADDR` cho phép bind trùng trong một số điều kiện, dẫn tới 2 broker cùng “thành công” nhưng chia connection không đoán được. Nên dùng `SO_EXCLUSIVEADDRUSE` trên Windows và báo lỗi rõ nếu cổng bận.

Thêm nữa, `SocketTransport` coi “TCP connect tới broker thành công” là “link UP”, dù **peer chưa kết nối**. Vì vậy A có thể hiện `Connected` trong khi B chưa mở; ping sẽ timeout. Cần handshake “peer ready” hoặc chỉ báo link UP khi broker xác nhận đủ 2 đầu.

#### (7) Không reconnect / không báo mất peer

`SocketTransport._fill()` khi `recv` trả `b""` hoặc lỗi thì set `_is_open = False` nhưng **không phát** `disconnected`/`error_occurred`. `SerialWorker._poll_serial` thấy `is_open == False` thì return im lặng. UI tiếp tục hiển thị “Connected”. Đây là lỗi UX nghiêm trọng khi demo.

### 2.3. Các kịch bản chạy thực tế

| Kịch bản | Data root | JSON/transfers/log | WebEngine | COM/Link | Kết luận |
|---|---|---|---|---|---|
| Mở cùng 1 `.exe` 2 lần, không tham số | Chung `<folder exe>/data` | **Chung** | **Chung** | Có thể chọn trùng | 🔴 Không dùng được |
| 2 bản `.exe` ở 2 thư mục khác nhau, không tham số | Mỗi bản 1 data root | Tách theo thư mục | **Vẫn chung** (cùng app name) | Có thể chọn trùng | 🟠 WebEngine + COM vẫn rủi ro |
| Cùng 1 build, `--instance A` / `--instance B` | `data/instances/A|B` | Tách | **Hiện vẫn chung** | Chưa auto | 🟠 Gần đúng; còn WebEngine + link |
| C + `--instance A/B` + `--link-role host/join` + sửa WebEngine profile + lock | Tách hoàn toàn | Tách | Tách | Tự động | ✅ Mục tiêu |
| C + `--instance A/B` + `--tx-port/--rx-port` (4 cổng) | Tách hoàn toàn | Tách | Tách | Người dùng chọn đúng cặp COM ảo TX/RX cho từng node | ✅ Cho nghiệm thu driver |

### 2.4. Checklist cách ly tối thiểu cho “client mode”

- [ ] `data/instances/<id>/` cho JSON, transfers, logs, webengine.
- [ ] Log handler tạo đúng instance dir ngay từ đầu.
- [ ] `QWebEngineProfile` riêng theo instance + data root.
- [ ] Lock instance theo data dir.
- [ ] Cổng/link cấu hình theo instance, có auto-connect.
- [ ] Danh tính UI/chat theo instance; tiêu đề cửa sổ ghi rõ node.
- [ ] Broker/link nằm trong bản build, không phụ thuộc Python ngoài.
- [ ] Cảnh báo/validate khi chọn cùng COM hoặc trùng cổng TCP.
- [ ] Sanitize tên instance.

---

## 3. Khảo sát giải pháp “cặp COM ảo” (Virtual COM Pair) trên Windows

### 3.1. Yêu cầu kỹ thuật cho client

- 2 process độc lập, mỗi process mở 1 cổng COM (ví dụ `COM11`/`COM12`), byte gửi ở đầu này nhận ở đầu kia.
- Không yêu cầu quyền admin ở mỗi lần chạy (cài đặt một lần có thể chấp nhận).
- Hoạt động trên Windows 10/11 **có Secure Boot**.
- Cài đặt/khởi động đơn giản, ổn định, ít can thiệp hệ thống.
- Lý tưởng: hỗ trợ mô phỏng mất gói/độ trễ/chắn tia để test ARQ.

### 3.2. com0com (Null-modem emulator) — mã nguồn mở

| Tiêu chí | Đánh giá |
|---|---|
| Bản chất | Kernel-mode driver tạo cặp cổng COM ảo |
| License | **GPLv2** — hạn chế nếu bundle vào sản phẩm thương mại |
| Chi phí | Miễn phí |
| Cài đặt | Chạy installer, cần **admin**; tạo cặp bằng `setupc.exe` hoặc GUI `setupg.exe` |
| Driver signing | Bản “signed 3.0.0.0” vẫn bị báo `Code 52` trên nhiều máy Windows 10/11 có Secure Boot; bản 2.2.2.0 đôi khi chạy được nhưng không đảm bảo |
| Secure Boot | Thường phải **tắt Secure Boot** hoặc bật test-signing — rủi ro bảo mật, có thể bị chính sách công ty chặn |
| Chính sách 2026 | Theo Microsoft, từ bản cập nhật bảo mật 4/2026, driver ký cross-cert **không còn được tin mặc định**; driver phải qua WHCP/Dev Portal |
| Điểm mạnh | Miễn phí; byte đi qua đúng driver COM; có thể chỉnh `EmuBR`/`EmuOverrun`; có `com2tcp`/`hub4com` kèm theo |
| Điểm yếu | Rủi ro cài đặt cao; không phù hợp máy client; khó hỗ trợ từ xa; không mô phỏng LOS dễ dàng |
| Kết luận | **Chỉ dùng lab có kiểm soát**; không bundle, không yêu cầu client tắt Secure Boot |

**Lệnh tạo cặp kiểu mẫu (chạy admin, trong thư mục cài com0com):**

```bat
setupc.exe install PortName=COM11,EmuBR=yes,EmuOverrun=yes PortName=COM12,EmuBR=yes,EmuOverrun=yes
setupc.exe list
```

- `EmuBR=yes`: mô phỏng đúng tốc độ baud (mặc định com0com chạy hết tốc lực, không thực tế với 115200).
- `EmuOverrun=yes`: tránh việc bên ghi bị chặn khi bên đọc chậm.
- `PortName=COM#` để để Windows tự chọn số COM; `RealPortName=COM11` để đổi tên sau.
- Nếu `list_ports.comports()` không thấy cổng (một số bản com0com nằm ở class riêng), app cần đọc thêm registry `HARDWARE\DEVICEMAP\SERIALCOMM` hoặc cho nhập tay tên COM.

### 3.3. VSPD (Virtual Serial Port Driver — Eltima / Electronic Team) — thương mại

| Tiêu chí | Đánh giá |
|---|---|
| Bản chất | Kernel driver thương mại tạo cặp COM ảo null-modem |
| Giá tham khảo (2026-10) | Standard từ **~$89.95**; bản Pro giá cao hơn (xem trang purchase); có **14 ngày trial** |
| Driver signing | WHQL, ký số; release notes ghi driver đã WHQL và **hỗ trợ Secure Boot** |
| Hệ điều hành | Windows 7/8/10/11 (32/64), Windows on ARM, Server |
| Cài đặt | Installer + GUI; tạo cặp COM real-time, **không cần reboot** |
| Điểm mạnh | Ổn định, GUI dễ dùng, hỗ trợ tốt, mô phỏng control lines, baudrate, break |
| Điểm yếu | Trả phí; license theo máy; muốn phân phối kèm app cần **OEM/SDK license** |
| Kết luận | **Lựa chọn số 1 cho Phương án A** nếu client cần `COM11/COM12` thật và máy có Secure Boot |

### 3.4. VSPE (Virtual Serial Ports Emulator — Eterlogic) — thương mại

| Tiêu chí | Đánh giá |
|---|---|
| Bản chất | Kernel driver + công cụ tạo cặp/redirect/bridge |
| Giá tham khảo (2026-10) | Bản 64-bit cần license **~$49.95**; bản 32-bit miễn phí (không còn phổ biến) |
| Driver signing | Có ký số; hỗ trợ Windows 10/11 x64/ARM64; bản cũ x64 (Win7/8) cần test-signing |
| Trial | Có bản trial giới hạn (tối đa 5 thiết bị, không load config) |
| Điểm mạnh | Rẻ hơn VSPD; hỗ trợ pair, TCP/UDP bridge, named pipe client/server |
| Điểm yếu | Giao diện/khoá license; cần cài driver admin |
| Kết luận | **Thay thế tiết kiệm chi phí cho VSPD**; phù hợp nếu client cần bridge TCP/COM |

### 3.5. hub4com / com2tcp (trong dự án com0com)

- Là **user-mode EXE**, không phải driver tạo cổng COM.
- `com2tcp` chuyển COM ↔ TCP (client/server, RFC2217); `hub4com` gộp/tách nhiều cổng.
- Muốn có “COM ảo local” vẫn cần com0com/VSPD ở một đầu.
- Hữu ích cho kịch bản **2 PC**: mỗi PC có 1 cặp COM ảo, `com2tcp` nối đầu còn lại qua TCP. Không giải quyết trực tiếp case “2 app 1 PC không driver”.

### 3.6. socat / WinSocat / WSL

- `socat` trên Linux/macOS tạo PTY ảo; trên Windows qua Cygwin có thể mở `/dev/ttyS0` nhưng **không tạo được cổng COM ảo cấp hệ điều hành** để app khác nhìn thấy như `COM11`.
- WSL2 dùng PTY/vsock; cổng PTY trong WSL **không xuất hiện** trong Device Manager của Windows.
- `WinSocat` (socat-like cho Windows) có hỗ trợ `sp:COM5`, `NPIPE`, `TCP`… nhưng vẫn cần **một cặp cổng COM** từ com0com/VSPD nếu muốn hai app nói chuyện như COM.
- Kết luận: **không phải giải pháp cho case này**.

### 3.7. Giải pháp phần cứng: 2 USB-UART + cáp null-modem

- Dùng 2 adapter USB-TTL (CH340/CP2102/FTDI), nối chéo `TX↔RX`, chung `GND`.
- Không cần driver ảo ngoài driver USB-UART; không đụng Secure Boot.
- Ưu: test đúng `PySerialTransport`, đúng timing/baud, phần cứng thật.
- Nhược: cần mua/đấu dây; mỗi lần chỉ có 1 cặp; không mô phỏng loss/LOS theo ý muốn.
- Vai trò: **phương án dự phòng** khi máy khách bị chặn driver ảo.

### 3.8. In-app TCP loopback (đã có một phần trong code)

- `SocketTransport` + broker TCP đã tồn tại; chỉ cần đóng gói và cấu hình.
- Ưu: **không driver, không admin, chạy với Secure Boot**, mô phỏng được loss/latency/LOS, dễ log/sniffer, cùng `ITransport` nên Chat/File/ARQ không đổi.
- Nhược: **không phải COM thật**; bỏ qua `pyserial`/`list_ports`/driver layer. Nếu khách yêu cầu literal `COM11/COM12` thì không đáp ứng.
- Có thể dùng TCP loopback hoặc **named pipe** (Windows) để tránh cổng/firewall và có ACL; TCP loopback dễ chạy cross-platform và dễ debug hơn.

### 3.9. Bảng so sánh tổng hợp

| Tiêu chí | com0com | VSPD | VSPE | hub4com/com2tcp | socat/WSL | HW USB-UART | In-app TCP |
|---|---|---|---|---|---|---|---|
| Là driver tạo COM ảo | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ (COM thật) | ❌ |
| Không cần admin | ❌ | ❌ (cài 1 lần) | ❌ (cài 1 lần) | ✅ | ✅ | ✅ | ✅ |
| Chạy với Secure Boot | ❌ thường fail | ✅ WHQL | ✅ (bản mới) | – | – | ✅ | ✅ |
| Chi phí | Free (GPL) | ~$89.95 | ~$49.95 | Free | Free | ~$5–15 HW | Free |
| Độ ổn định trên Win11 | Thấp | Cao | Cao | Trung bình | – | Cao | Cao |
| Mô phỏng loss/LOS/latency | Hạn chế | Hạn chế | Hạn chế | Có (TCP layer) | – | Không (phải rút dây) | ✅ tốt |
| Đúng đường pyserial | ✅ | ✅ | ✅ | ✅ nếu qua COM | ❌ | ✅ | ❌ |
| 1-click cho client | ❌ | Trung bình | Trung bình | ❌ | ❌ | ❌ | ✅ |
| Phù hợp case này | Lab | Nghiệm thu HW | Nghiệm thu HW | 2 PC/remote | Không | Dự phòng | **Mặc định** |

### 3.10. Kết luận khảo sát

1. **com0com không còn là lựa chọn an toàn cho máy client Windows 10/11 Secure Boot**; tình trạng `Code 52` phổ biến và chính sách ký driver 2026 càng siết.
2. Nếu bắt buộc “virtual COM pair thật”, chọn **VSPD** (ổn định, WHQL) hoặc **VSPE** (rẻ hơn). Cài 1 lần, chạy nhiều lần, không cần tắt Secure Boot.
3. Nếu không bắt buộc literal COM, **in-app TCP loopback là tối ưu** cho demo/nghiệm thu chức năng và mô phỏng lỗi.
4. `socat`/WSL không giải quyết được; `hub4com`/`com2tcp` chỉ hỗ trợ kịch bản qua mạng.
5. Nên coi virtual COM pair là **kênh kiểm chứng phần cứng**, không phải kênh demo mặc định.

---

## 4. Phương án A / B / C và trade-offs

### 4.1. Phương án A — External Virtual COM Pair (com0com / VSPD / VSPE)

**Workflow (VSPD/VSPE — khuyến nghị trong nhóm A):**

1. Cài driver một lần (admin), tạo cặp `COM11` ↔ `COM12`.
2. Xác nhận trong Device Manager → `Ports (COM & LPT)`.
3. Khởi động `IR_OWC_Lab_Terminal.exe --instance A --port COM11 --auto-connect`.
4. Khởi động `IR_OWC_Lab_Terminal.exe --instance B --port COM12 --auto-connect`.
5. Test Ping/Chat/File như 2 PC thật.

**Workflow (com0com — chỉ lab):**

```bat
:: Chạy admin, trong thư mục cài com0com
setupc.exe install PortName=COM11,EmuBR=yes,EmuOverrun=yes PortName=COM12,EmuBR=yes,EmuOverrun=yes
setupc.exe list
:: Nếu Code 52: KHÔNG tắt Secure Boot trên máy client; chuyển sang VSPD/VSPE hoặc Phương án B
```

**Ưu điểm**
- Đúng đường `PySerialTransport` → `list_ports` → `pyserial` → driver; độ trung thực cao nhất.
- Không cần sửa kiến trúc transport; chỉ cần thêm CLI `--port` + auto-connect + hiển thị đúng.
- Có thể dùng chung cho cả kịch bản cắm board thật (cùng code path).

**Nhược điểm**
- Cài driver/admin; Secure Boot/driver signing; VSPD/VSPE tốn phí; com0com rủi ro cao.
- Khó mô phỏng LOS/latency/loss theo ý muốn (phải can thiệp dây/thiết bị).
- Hỗ trợ từ xa khó: lỗi driver, cổng trùng tên, driver cũ còn sót.
- com0com GPL → không bundle vào sản phẩm đóng.

**Độ phức tạp:** trung bình (cài đặt) + thấp (code app: chỉ cần `--port`/auto-connect/validate).
**Rủi ro chính:** driver signing & môi trường máy khách.

### 4.2. Phương án B — In-app Dual Instance / Socket Tunnel Loopback

**Mục tiêu:** 2 instance độc lập, kết nối qua tunnel nội bộ do app tự dựng, **không cần driver/admin**.

**Kiến trúc đề xuất:**

```text
IR_OWC_Lab_Terminal.exe --instance A --link-role host --link-base-port 6001 --auto-connect
IR_OWC_Lab_Terminal.exe --instance B --link-role join --link-base-port 6001 --auto-connect

Node A                                          Node B
┌─────────────────────────────┐                ┌─────────────────────────────┐
│ SerialWorker                │                │ SerialWorker                │
│  └ SocketTransport (LINK_A) │                │  └ SocketTransport (LINK_B) │
│        │ 127.0.0.1:6001      │                │        │ 127.0.0.1:6002      │
│        ▼                    │                │        ▼                    │
│  LinkBroker (thread)        │◀──────────────▶│  (chỉ client)               │
│  TCP relay + loss/LOS       │   byte relay   │                             │
└─────────────────────────────┘                └─────────────────────────────┘
```

**Biến thể (khuyến nghị cho MVP):** broker nhúng trong **Node A** (host). Node B chỉ kết nối. Không cần process thứ 3, không cần Python ngoài.

**Biến thể chắc chắn hơn (nếu cần độc lập hoàn toàn):** tách `--broker-only` thành process riêng (cùng file `.exe` hoặc EXE phụ), launcher khởi động trước. Broker sống độc lập khi đóng A/B.

**CLI đề xuất**

```text
--instance A|B                 (bắt buộc cho dual)
--transport auto|serial|link|mock
--port COM11                   (chọn cổng; bỏ trống thì dùng mock/link)
--baud 115200
--link-role off|host|join|auto
--link-base-port 6001
--link-host 127.0.0.1
--link-token <hex>             (tùy chọn, chống process lạ inject)
--auto-connect                 (tự connect sau khi UI sẵn sàng)
--allow-multiple               (debug; bỏ lock instance)
--broker-only                  (chạy link broker không UI)
--los / --loss / --latency-ms  (chỉ broker/mock)
```

**Ưu điểm**
- Zero-install, zero-admin, chạy với Secure Boot.
- Cùng giao diện `ITransport`, không sửa `ChatService`/`FileTransferService`.
- Mô phỏng tốt loss/latency/LOS; log/sniffer đồng nhất.
- Launcher 1-click; dễ tự động hoá test CI.

**Nhược điểm**
- Không phải COM thật, bỏ qua tầng pyserial/driver.
- Cần đóng gói broker, thêm CLI/UI, xử lý reconnect/peer-ready.
- TCP loopback cần chọn cổng, tránh trùng; giải pháp named pipe an toàn hơn nhưng phức tạp hơn.
- Nếu 2 app là 2 build khác nhau → cần handshake version.

**Độ phức tạp code:** trung bình (chủ yếu `app.py`, transport factory, worker, WebEngine profile, launcher).

### 4.3. Phương án C — Hybrid (khuyến nghị)

Hỗ trợ cả A và B, chọn bằng cấu hình:

| Chế độ | Cờ khởi động | Transport thực tế | Dùng khi |
|---|---|---|---|
| Demo/no-driver | `--instance A --link-role host --auto-connect` / `--instance B --link-role join --auto-connect` | `SocketTransport` (link nội bộ) | Khách demo, máy sạch, Secure Boot |
| Nghiệm thu COM ảo | `--instance A --port COM11 --auto-connect` / `--instance B --port COM12 --auto-connect` | `PySerialTransport` | Kiểm chứng driver/list_ports/pyserial |
| Board thật | `--instance A --port COM5` / `--instance B --port COM7` | `PySerialTransport` | 2 PC hoặc 2 board |
| 1 app mock | mặc định `MOCK_COM_LOOPBACK` | `MockSerialPort` | Phát triển UI/test nhanh |

**Ưu:** một codebase, một interface; B mặc định cho client; A cho acceptance.
**Nhược:** cần test cả 2 nhánh; tài liệu phải rõ “B không phải COM thật”.

### 4.4. Decision matrix (điểm 1–5, càng cao càng tốt)

| Tiêu chí (trọng số) | A-com0com | A-VSPD | A-VSPE | B in-app | C hybrid |
|---|---|---|---|---|---|
| Zero admin/driver (×3) | 1 | 2 | 2 | 5 | 5 |
| Secure Boot Win11 (×3) | 1 | 5 | 5 | 5 | 5 |
| Trung thực pyserial/COM (×2) | 5 | 5 | 5 | 2 | 5 |
| Mô phỏng loss/LOS/latency (×2) | 2 | 2 | 2 | 5 | 5 |
| 1-click/UX client (×3) | 1 | 3 | 3 | 5 | 5 |
| Chi phí/license (×2) | 5 | 2 | 4 | 5 | 4 |
| Công sức triển khai (×2) | 4 | 4 | 4 | 2 | 2 |
| Ổn định/hỗ trợ (×2) | 1 | 5 | 4 | 4 | 5 |
| **Tổng có trọng số** | **56** | **93** | **91** | **107** | **119** |

> Điểm chỉ mang tính định hướng; trọng số có thể chỉnh theo ưu tiên khách hàng.

### 4.5. Khuyến nghị chốt

- **Triển khai Phương án C**, mặc định **B** cho client.
- **A (VSPD/VSPE)** là kênh nghiệm thu tuỳ chọn; **không dùng com0com cho client**.
- **Không build 2 bản code độc lập**; dùng một build + 2 launch profile.

---

## 5. Kiến trúc đề xuất chi tiết (Option C)

### 5.1. `LaunchConfig` — một điểm cấu hình duy nhất

Tạo `src/ir_terminal/config.py`:

```python
from dataclasses import dataclass
from typing import Literal

TransportMode = Literal["auto", "serial", "link", "mock"]
LinkRole = Literal["off", "host", "join", "auto"]

@dataclass(frozen=True)
class LaunchConfig:
    instance: str | None = None          # A/B/... (đã sanitize)
    transport: TransportMode = "auto"
    port: str | None = None              # COM11 / LINK_A / MOCK_*
    baud: int = 115200
    link_role: LinkRole = "off"
    link_host: str = "127.0.0.1"
    link_base_port: int = 6001
    link_token: str | None = None
    auto_connect: bool = False
    allow_multiple: bool = False
    broker_only: bool = False
```

`run()` parse argparse đầy đủ (thay vì chỉ `--instance`), tạo `LaunchConfig`, rồi `create_app(config)`.

### 5.2. `InstanceContext` — cách ly tập trung

```python
@dataclass(frozen=True)
class InstanceContext:
    instance_id: str | None
    data_dir: Path
    transfers_dir: Path
    logs_dir: Path
    webengine_cache: Path
    webengine_storage: Path
    lock_path: Path
    display_name: str          # "Node A" / "Node B"
    local_station: str         # "Station Alpha"
    remote_station: str        # "Station Bravo"
```

- `instance_id` được validate `^[A-Za-z0-9_-]{1,16}$`; sai → lỗi rõ ràng.
- `data_dir = get_app_data_dir(inst)`.
- `webengine_* = data_dir/"webengine"/...` (hoặc `%LOCALAPPDATA%` riêng, miễn unique).
- `lock_path = data_dir/"app.lock"`.
- Truyền `InstanceContext` vào `create_app` và các service thay vì gọi rải rác `get_*_dir()`.

### 5.3. Sửa logging

```python
def create_app(config: LaunchConfig) -> ...:
    ctx = build_instance_context(config)
    logger, _ = setup_logger(log_dir=ctx.logs_dir)   # ← tạo đúng ngay từ đầu
    # xoá đoạn re-point handler hiện tại
```

### 5.4. Sửa QtWebEngine profile

```python
from PySide6.QtWebEngineCore import QWebEngineProfile

profile_key = f"ir-terminal-{ctx.instance_id or 'single'}-{ctx.data_dir_hash}"
self._profile = QWebEngineProfile(profile_key, self)
self._profile.setCachePath(str(ctx.webengine_cache))
self._profile.setPersistentStoragePath(str(ctx.webengine_storage))
self._page = WebAppPage(self._profile, self._view)
self._view.setPage(self._page)
```

Ghi chú:

- `profile_key` phải **không đổi** trong một process và **khác nhau** giữa 2 instance; thêm hash data dir để 2 bản build ở 2 thư mục cũng không đụng nhau.
- Giữ `self._profile` sống bằng page; không tạo profile mới mỗi lần mở page.
- Có thể dùng off-the-record profile nếu không cần cookie persistence, nhưng sẽ mất local storage (app hiện không dùng).

### 5.5. Link broker nhúng

Chuyển `scripts/virtual_optical_link.py` thành `src/ir_terminal/core/virtual_link.py`:

```python
class LinkBroker:
    def __init__(self, host, port_a, port_b, *, loss, latency_ms, token=None): ...
    def start(self) -> None: ...   # spawn accept + forward threads (daemon)
    def stop(self) -> None: ...    # set event, close listeners/sockets
    def set_los_blocked(self, blocked: bool) -> None: ...
```

Thay đổi quan trọng so với script cũ:

- Bỏ vòng `input()` stdin; LOS do UI/CLI điều khiển.
- Dùng `SO_EXCLUSIVEADDRUSE` trên Windows (hoặc kiểm tra bind fail → báo lỗi).
- Bind `127.0.0.1` mặc định; không bind `0.0.0.0`.
- Token handshake tùy chọn: client gửi `IRLINK 1 <role> <token>\n` trước khi broker relay; broker không forward phần handshake.
- `stop()` đóng listener + connection để app thoát sạch.

**Host mode:** `if config.link_role in ("host",)`: `broker = LinkBroker(...); broker.start()` trước khi tạo `SerialManager`. Sau đó `SerialWorker` mở `LINK_A` (port_a) như hiện tại.

**Join mode:** không start broker; `SocketTransport.open()` retry với backoff (ví dụ 20 × 500ms) để chịu được thứ tự mở app.

**Broker-only mode:** `--broker-only --link-base-port 6001` chạy chỉ broker (headless), log ra `data/broker.log`; launcher có thể start trước 2 app.

### 5.6. `SocketTransport` cần nâng cấp

| Vấn đề hiện tại | Sửa đề xuất |
|---|---|
| Endpoint cứng `LINK_PORT_MAP` | Truyền `link_endpoints` từ `LaunchConfig` vào `create_transport`/`SerialWorker` |
| Không reconnect | Thêm retry/open loop; hoặc `LinkReconnectManager` trong `SerialWorker` |
| Không báo mất peer | Khi `_fill` EOF/lỗi → phát callback/signal `link_lost`; worker emit `disconnected` + `error_occurred` |
| “Connected” khi peer chưa tới | Broker handshake `peer_ready`, hoặc app hiển thị “Waiting for peer” |
| Không phân biệt LINK trong UI | Đặt nhãn `LINK_A (host 127.0.0.1:6001)` trong dropdown |

### 5.7. Port scan và virtual COM

Nâng `scan_available_ports()`:

1. Hợp nhất `serial.tools.list_ports.comports()` với registry Windows:

```python
import winreg  # chỉ trên Windows
with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM") as key:
    # enumerate values → COM11, COM12, ...
```

2. Thêm lựa chọn “Nhập tay tên cổng” trong UI (fallback cho com0com class riêng).
3. Ưu tiên sort COM tự nhiên (`COM2` trước `COM10`).
4. Khi `--transport serial --port COM11`, không list link/mock để tránh chọn nhầm.

### 5.8. Auto-connect và UI bootstrap

- Python: sau `window.show()`, `QTimer.singleShot(600, lambda: bridge.connectPort(preferred_port, baud))` nếu `config.auto_connect`.
- Bridge expose `getLaunchConfig()` trả JSON `{instance, display_name, port, link_role, link_base_port, data_dir}` để JS:
  - cập nhật tiêu đề/badge “Node A / Node B”;
  - preselect đúng port trong dropdown;
  - hiển thị “Link: host 6001” hoặc “Port: COM11”.
- Sửa `refreshPortList()` để **giữ lựa chọn hiện tại** nếu vẫn tồn tại (hiện reset dropdown về option đầu).

### 5.9. Instance lock

```python
from PySide6.QtCore import QLockFile

lock = QLockFile(str(ctx.lock_path))
lock.setStaleLockTime(0)  # hoặc 30_000
if not lock.tryLock(100) and not config.allow_multiple:
    QMessageBox.warning(None, "Instance đang chạy",
        f"Node {ctx.instance_id} đã mở. Đóng app cũ hoặc dùng --allow-multiple.")
    return None
```

- Lock theo `data_dir` → A và B có lock khác nhau, không chặn nhau.
- Nếu mở cùng instance lần 2 → chặn, tránh tranh COM/broker.

### 5.10. Danh tính chat/UI

Truyền vào `ChatService`:

```python
chat_service = ChatService(
    serial_manager=serial_manager,
    storage_service=storage_service,
    local_name=ctx.local_station,     # "Station Alpha" cho A, "Station Bravo" cho B
    remote_name=ctx.remote_station,
)
```

Tiêu đề: `SpectraLink IR-OWC Workstation — Node A (LINK_A)` hoặc `... (COM11)`.

### 5.11. Cấu trúc thư mục sau triển khai

```text
<app root>/data/
└── instances/
    ├── A/
    │   ├── chat_history.json
    │   ├── file_transfers.json
    │   ├── telemetry_samples.json
    │   ├── settings.json
    │   ├── transfers/
    │   ├── logs/app.log
    │   ├── webengine/{cache,storage}/
    │   └── app.lock
    └── B/  (tương tự)
```

### 5.12. Đóng gói PyInstaller

- Broker nằm trong package (`ir_terminal.core.virtual_link`) → tự được PyInstaller thu thập khi import; **không** phụ thuộc `scripts/`.
- Nếu chọn broker process riêng: thêm EXE thứ hai trong `ir_terminal.spec` (ví dụ `IR_OWC_Link_Broker`) hoặc dùng `--broker-only` từ cùng EXE; thêm `console=True` cho bản broker để có log, nhưng có thể để headless và ghi file log.
- Launcher Windows: `.cmd`/shortcut hoặc EXE nhỏ. Ví dụ `.cmd`:

```bat
@echo off
set "APP=%~dp0IR_OWC_Lab_Terminal.exe"
start "IR-OWC Node A" "%APP%" --instance A --link-role host --link-base-port 6001 --auto-connect
timeout /t 2 /nobreak >nul
start "IR-OWC Node B" "%APP%" --instance B --link-role join --link-base-port 6001 --auto-connect
```

- Với COM ảo:

```bat
start "IR-OWC Node A" "%APP%" --instance A --transport serial --port COM11 --auto-connect
start "IR-OWC Node B" "%APP%" --instance B --transport serial --port COM12 --auto-connect
```

---

## 6. Lộ trình triển khai đề xuất

### Phase 0 — Hardening instance isolation (1–2 ngày)

| Việc | File | Test |
|---|---|---|
| `LaunchConfig` + argparse đầy đủ | `app.py`, `config.py` (mới) | parse args, default |
| `InstanceContext` + sanitize instance | `path_helpers.py`, `config.py` | `test_path_helpers.py` mở rộng |
| Logger tạo đúng instance dir | `app.py`, `logger.py` | log path theo instance |
| `QWebEngineProfile` riêng | `main_window_web.py` | mở 2 instance không lỗi cache |
| Instance lock | `app.py` | mở trùng A bị chặn |
| Danh tính chat/tiêu đề | `app.py`, `chat_service.py`, `main_window_web.py` | node A/B khác tên |

### Phase 1 — Dual link không driver (2–3 ngày)

| Việc | File | Test |
|---|---|---|
| Chuyển broker vào package | `core/virtual_link.py` (từ `scripts/`) | unit start/stop/bind conflict |
| Host/join/auto-connect CLI | `app.py`, `backend_bridge.py` | integration 2 managers |
| `SocketTransport` reconnect + peer status | `socket_transport.py`, `serial_worker.py` | mất broker → disconnected |
| Endpoint động theo config | `transport_factory.py`, `serial_worker.py` | LINK map per instance |
| Port scan registry + manual entry | `serial_worker.py`, UI | thấy COM ảo/COM 10+ |
| Launcher Windows + UI bootstrap | `scripts/`, `ui/web/js/bridge_client.js` | demo 1-click |
| Đóng gói broker | `ir_terminal.spec` | chạy trên máy sạch không Python |

### Phase 2 — Hỗ trợ Virtual COM Pair (1–2 ngày)

| Việc | File | Test |
|---|---|---|
| `--transport serial --port COM11` | `app.py`, UI | preselect/auto-connect |
| Validate cổng trùng/không mở được | `backend_bridge.py` | thông báo lỗi rõ |
| Runbook com0com/VSPD/VSPE | `docs/` | test tay trên Win10/11 Secure Boot |
| (Tuỳ chọn) phát hiện driver ảo | `serial_worker.py` | gợi ý khi Code 52 |

### Phase 3 — Mở rộng (tuỳ nhu cầu)

- Link qua 2 PC (`--link-host <ip>`), phù hợp kịch bản 2 PC sẵn có.
- Named pipe thay TCP loopback (bảo mật/ACL).
- Token handshake + version handshake giữa 2 node.
- `--broker-only` độc lập + tray/service.
- Tự động chọn vai host/join bằng named mutex.

### 6.1. Test plan / acceptance criteria

**Chức năng (Phương án B):**

- [ ] Trên máy Windows sạch, **không Python, không admin, Secure Boot ON**: chạy shortcut dual → cả 2 cửa sổ mở, mỗi node tự connect.
- [ ] Ping/Pong 2 chiều, RTT hiển thị.
- [ ] Chat 2 chiều, lịch sử lưu đúng file `instances/A|B/chat_history.json`.
- [ ] Truyền file 2 chiều (10 MB + 1 file > 100 MB), CRC-32 khớp, file nhận nằm trong `instances/<id>/transfers/`.
- [ ] Hủy giữa phiên: cả 2 về `ABORTED`, không còn `.part`.
- [ ] Mô phỏng chắn tia/loss: transfer fail đúng kỳ vọng, bỏ chắn → gửi lại thành công.
- [ ] Đóng/mở lại từng node; node còn lại nhận disconnected; mở lại reconnect.
- [ ] Mở trùng instance A → bị chặn, không mở cổng thứ 2.
- [ ] Không có log lỗi WebEngine (`Access is denied`, `database is locked`) trên cả 2 node.
- [ ] `%LOCALAPPDATA%` không còn profile dùng chung bị lock.

**Chức năng (Phương án A):**

- [ ] VSPD/VSPE tạo `COM11/COM12`; app A/B chọn đúng; ping/chat/file thành công.
- [ ] Log thể hiện `PySerialTransport` (không phải Socket).
- [ ] Chọn trùng COM → thông báo lỗi rõ, không crash.
- [ ] com0com (lab, Secure Boot off) chạy được hoặc ghi nhận `Code 52` và fallback.

**Phi chức năng:**

- [ ] Không dùng `0.0.0.0`; chỉ `127.0.0.1`.
- [ ] Cổng TCP có thể cấu hình; bind fail báo lỗi.
- [ ] Dữ liệu 2 instance không rò rỉ sang nhau.
- [ ] Log tách theo instance, rotation không lỗi.

---

## 7. Runbook cho client

### 7.1. Chế độ khuyến nghị: no-driver (Phương án B)

1. Giải nén bản build one-folder vào `D:\IR_OWC\` (tránh `Program Files` để không bị hạn chế ghi `data/`).
2. Nhấp đôi `Start_Dual_Demo.cmd` (hoặc shortcut “IR-OWC Dual Demo”).
3. Chờ 2 cửa sổ: `Node A` (host) và `Node B` (join).
4. Kiểm tra góc phải mỗi cửa sổ hiển thị link; nếu B mở trước A, B sẽ retry trong vài giây.
5. Thực hiện Ping → Chat → File theo kịch bản demo.
6. Dữ liệu/log nằm trong `D:\IR_OWC\data\instances\A|B\`.

### 7.2. Chế độ COM ảo với VSPD/VSPE

1. Cài VSPD (hoặc VSPE) một lần bằng quyền admin.
2. Tạo cặp cổng: `COM11` ↔ `COM12`. Không cần reboot.
3. Mở shortcut “Node A (COM11)” và “Node B (COM12)”.
4. Xác nhận app hiển thị `COM11`/`COM12`, bấm Initialize; nếu lỗi, kiểm tra cổng đã bị app khác chiếm chưa.
5. Chạy kịch bản nghiệm thu phần cứng.

### 7.3. Chế độ com0com (chỉ lab)

1. Chỉ thực hiện trên máy lab được phép tắt Secure Boot/test-signing.
2. Cài com0com (bản signed còn hoạt động trên máy đó).
3. `setupc.exe install PortName=COM11,EmuBR=yes,EmuOverrun=yes PortName=COM12,EmuBR=yes,EmuOverrun=yes`.
4. Nếu Device Manager báo `Code 52`, **dừng lại**: chuyển sang VSPD/VSPE hoặc Phương án B; **không tắt Secure Boot trên máy khách**.

### 7.4. Troubleshooting nhanh

| Triệu chứng | Nguyên nhân thường gặp | Xử lý |
|---|---|---|
| Node B báo lỗi connect, A vẫn chạy | B mở trước host hoặc cổng TCP bận | Đợi retry; kiểm tra `--link-base-port`; kiểm tra process chiếm cổng |
| Ping timeout dù “Connected” | Peer chưa kết nối (broker chưa có đủ 2 đầu) | Mở đủ 2 node; kiểm tra handshake peer-ready |
| Cửa sổ thứ 2 trắng/chậm | WebEngine profile dùng chung | Bản build phải có profile riêng; kiểm tra `data/instances/*/webengine` |
| `Access is denied` khi mở COM | Cổng đã bị process/app khác mở | Đảm bảo A mở COM11, B mở COM12; đóng app chiếm cổng |
| Không thấy COM ảo trong app | com0com class riêng, pyserial không enumerate | Dùng registry union/manual entry; hoặc kiểm tra tên `COM#` |
| Log rotation lỗi/thiếu log | 2 process cùng `data/logs/app.log` | Dùng `--instance`; build mới tạo handler đúng instance |
| Mất tin nhắn/lịch sử | 2 app dùng chung `data/` | Luôn chạy qua launcher có `--instance` |
| `Code 52` com0com | Secure Boot/driver signing | Bỏ com0com; dùng VSPD/VSPE hoặc Phương án B |

---

## 8. Rủi ro và câu hỏi mở

### 8.1. Rủi ro

| Rủi ro | Mức | Giảm thiểu |
|---|---|---|
| Khách yêu cầu literal `COM11/COM12` | Cao | Xác nhận sớm; nếu có, đưa Phương án A (VSPD/VSPE) vào phạm vi |
| Secure Boot/driver signing chặn com0com | Cao | Không dùng com0com cho client; VSPD/VSPE hoặc B |
| WebEngine profile chưa được sửa | Cao | Phase 0 bắt buộc; test 2 instance trên Win sạch |
| Broker nhúng chết theo Node A | Trung bình | Cho phép `--broker-only`/process riêng; hoặc auto-restart |
| Trùng cổng TCP 6001 | Trung bình | Cổng cấu hình, `SO_EXCLUSIVEADDRUSE`, báo lỗi rõ |
| Bảo mật local TCP (process lạ inject) | Thấp–Trung bình | Bind loopback, token handshake, cân nhắc named pipe ACL |
| 2 build lệch version | Trung bình | Chỉ dùng 1 build; hoặc handshake version |
| GPL com0com khi bundle | Trung bình | Không bundle; chỉ hướng dẫn |
| VSPD/VSPE license theo máy | Trung bình | Ngân sách license; SDK/OEM nếu phân phối kèm |
| `data/` nằm trong thư mục không ghi được (`Program Files`) | Trung bình | Hướng dẫn cài vào thư mục người dùng/portable |
| Migration dữ liệu cũ `data/` sang `instances/A|B` | Thấp | Ghi chú; có thể copy `settings.json` một lần |

### 8.2. Câu hỏi cần chốt với khách

1. Khách có **bắt buộc** app nhìn thấy `COM11`/`COM12` không, hay chỉ cần 2 node nói chuyện được?
2. Máy demo có **Secure Boot bật** và chính sách cấm tắt không?
3. Có ngân sách cho **VSPD (~$89.95)** hoặc **VSPE (~$49.95)** không? Bao nhiêu máy?
4. Có cần mô phỏng **chắn tia/loss/latency** (Phương án B) hay chỉ cần byte link?
5. Có cần mở rộng **2 PC qua mạng LAN** sau này không?
6. Windows version/kiến trúc (10/11, x64/ARM64)? Có dùng bitlocker/policy chặn driver?
7. Có yêu cầu **2 file `.exe` vật lý khác nhau** hay chỉ cần 2 cửa sổ/2 process từ cùng build?
8. Có cần giữ dữ liệu/lịch sử hiện tại khi chuyển sang instance mode không?

---

## 9. Nguồn tham khảo

- com0com — trang chủ và license GPLv2: https://com0com.sourceforge.net/
- com0com — hướng dẫn `setupc` / Ports class / `EmuBR`, `EmuOverrun`: https://com0com.sourceforge.net/com0com/ReadMe.txt
- com0com — báo cáo `Code 52` với Secure Boot (SourceForge mailing list, 2025): https://sourceforge.net/p/com0com/mailman/message/59168056/
- Docklight — com0com 3.0 không cài mới được trên Win10/11, workaround 2.2.2.0: https://docklight.de/dl_prb059/
- Microsoft Learn — Driver Signing Policy / Windows 10 1607 Dev Portal: https://learn.microsoft.com/en-us/windows-hardware/drivers/install/kernel-mode-code-signing-policy--windows-vista-and-later-
- Microsoft Support — The Windows Driver Policy (cross-signed không còn được tin từ bản cập nhật 4/2026): https://support.microsoft.com/en-us/windows/hardware/drivers/the-windows-driver-policy
- Microsoft Learn — TESTSIGNING và Secure Boot: https://learn.microsoft.com/en-us/windows-hardware/drivers/install/the-testsigning-boot-configuration-option
- Eltima VSPD — tính năng, WHQL, giá: https://www.eltima.com/products/vspdxp/ và https://www.eltima.com/purchase/vspdxp/
- Eltima VSPD — release notes WHQL/secure boot: https://wiki.eltima.com/user-guides/vspd/vspd-9-versions.html
- Eterlogic VSPE — hỗ trợ nền tảng/license: https://eterlogic.com/products.vspe.html và https://eterlogic.com/Products.VSPE_License.html
- com0com — hub4com/com2tcp ReadMe: https://com0com.sourceforge.net/com2tcp/ReadMe.txt và https://com0com.sourceforge.net/hub4com/ReadMe.txt
- Qt 6 — QWebEngineProfile / QWebEngineProfileBuilder (storage name unique, cache path): https://doc.qt.io/QT-6/qwebengineprofilebuilder.html
- Qt Forum — nhiều process dùng chung cache path gây chậm/trắng: https://forum.qt.io/topic/148450/
- OpenRV issue — WebEngine cache/cookie DB lock trên Windows khi 2 instance: https://github.com/AcademySoftwareFoundation/OpenRV/issues/187
- Microsoft Learn — Kernel-mode driver signing requirements: https://learn.microsoft.com/en-us/windows-hardware/drivers/install/kernel-mode-code-signing-requirements--windows-vista-and-later-
- WinSocat — bridge COM/named pipe/TCP trên Windows: https://github.com/firejox/WinSocat
- WSL discussion — không thể expose PTY thành COM cho Windows: https://github.com/microsoft/WSL/discussions/9289
- Microsoft Learn — Named pipe instances/security: https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-instances

---

## Phụ lục A — Mapping thay đổi code dự kiến

| Hạng mục | File hiện tại | Thay đổi |
|---|---|---|
| Config/CLI | `src/ir_terminal/app.py` | Tách `LaunchConfig`, parse đầy đủ; auto-connect |
| Instance context | `src/ir_terminal/utils/path_helpers.py` | Thêm `InstanceContext`, validate/sanitize; giữ API cũ |
| Logging | `src/ir_terminal/app.py`, `utils/logger.py` | `setup_logger(log_dir=ctx.logs_dir)` sớm; bỏ re-point |
| WebEngine | `src/ir_terminal/ui/main_window_web.py` | `QWebEngineProfile` riêng + cache/storage path |
| Lock | `src/ir_terminal/app.py` | `QLockFile` theo instance |
| Broker | `scripts/virtual_optical_link.py` → `src/ir_terminal/core/virtual_link.py` | Đóng gói, `start/stop`, `SO_EXCLUSIVEADDRUSE`, bỏ stdin |
| Transport factory | `src/ir_terminal/core/transport_factory.py` | Nhận `link_endpoints`/config; bỏ phụ thuộc map cứng |
| Socket | `src/ir_terminal/core/socket_transport.py` | Retry/reconnect, peer status, endpoint động |
| Worker | `src/ir_terminal/core/serial_worker.py` | Giữ config; phát disconnected khi link chết; port scan registry |
| Bridge/UI | `src/ir_terminal/ui/backend_bridge.py`, `ui/web/js/bridge_client.js` | `getLaunchConfig()`, giữ selection, nhãn LINK/host |
| Chat | `src/ir_terminal/services/chat_service.py` | Tên local/remote theo instance |
| Packaging | `ir_terminal.spec`, `scripts/` | Bundle broker; launcher Windows; (tuỳ chọn) EXE broker |
| Docs | `docs/` | Thêm doc này + runbook vận hành |

## Phụ lục B — Bảng chọn nhanh cho client

| Tình huống | Chọn |
|---|---|
| Máy demo sạch, không admin, cần chạy ngay | **B** (`--link-role host/join`) |
| Cần test loss/LOS/latency | **B** (broker có loss/LOS) |
| Bắt buộc thấy `COM11/COM12`, máy có Secure Boot | **A + VSPD** (hoặc VSPE) |
| Máy lab cho phép tắt Secure Boot, ngân sách 0 | A + com0com (chấp nhận rủi ro) |
| Không cài được driver trên máy khách | **B** hoặc HW USB-UART |
| Cần 2 PC qua LAN | B mở rộng `--link-host`, hoặc A + com2tcp |
| Muốn “1 build, 2 node” | **C** — một codebase, nhiều launch profile |
