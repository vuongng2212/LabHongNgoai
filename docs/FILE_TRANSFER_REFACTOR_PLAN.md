# Review kiến trúc và kế hoạch refactor `FileTransferService`

**Phạm vi:** review tĩnh codebase, tập trung vào `src/ir_terminal/services/file_transfer_service.py` (xấp xỉ 1.857 dòng theo phạm vi review). Đây là kế hoạch thay đổi, chưa thực hiện refactor.

## 1. Tóm tắt nhận định

Codebase đã có các lớp tầng tương đối rõ: `core` quản lý transport/thread, `protocol` định nghĩa frame/payload, `services` chứa nghiệp vụ, `ui` nối backend với SPA, và `app.py` làm composition root. File transfer hiện chạy được các luồng phức tạp: Stop-and-Wait ARQ, legacy/large payload, truyền streaming, nhận `.part`, CRC-32, hủy hai chiều, idempotent START/END, cấu hình và lưu lịch sử.

Vấn đề chính không phải thiếu tính năng mà là ranh giới trách nhiệm bị phá vỡ: `FileTransferService` vừa là Qt `QObject`, state machine, ARQ engine, protocol dispatcher, file repository, integrity checker, settings facade, persistence coordinator và nguồn UI events. Các trạng thái TX/RX và cache retransmit được lưu trong nhiều thuộc tính mutable cấp service. Điều này làm thay đổi nhỏ dễ gây lỗi chéo, khó kiểm thử độc lập và khiến quy tắc thread của I/O không được giữ.

**Rủi ro ưu tiên cao:** `SerialWorker` đọc/ghi transport trong QThread, nhưng `FileTransferService` được tạo trong thread gọi `app.py` (thông thường UI thread). Signal `packet_received` nối tới service đưa callback về thread affinity của receiver theo Qt AutoConnection. Vì vậy các handler RX thực hiện ghi/flush file, CRC và publish `os.replace` trong thread service; `start_transfer()` cũng quét CRC toàn file đồng bộ. Với wiring hiện tại, các thao tác này có thể block UI. Cần xác minh bằng test affinity/latency trước khi thay đổi kiến trúc, không nên suy luận rằng serial worker tự động làm mọi transfer I/O chạy nền.

## 2. Cấu trúc codebase hiện tại

| Tầng | Vai trò hiện tại | Thành phần tiêu biểu |
|---|---|---|
| Composition root | Tạo và nối dependency | `app.py` |
| Core / I/O | Transport abstraction, serial worker, QThread lifecycle, byte polling | `interfaces.py`, `serial_worker.py`, `serial_manager.py`, transports, `ring_buffer.py` |
| Protocol | Constants, packet/framing, payload serializers | `protocol/constants.py`, `framer.py`, `packet.py`, `file_payloads.py` |
| Services | Nghiệp vụ ứng dụng và storage JSON | `file_transfer_service.py`, `chat_service.py`, `telemetry_service.py`, `storage_service.py` |
| UI | QWebEngine/QWebChannel, backend bridge, SPA | `backend_bridge.py`, `ui/web/` |
| Tests | Unit và integration/E2E mock-link | `tests/unit/`, `tests/integration/` |

Kiến trúc tổng thể đã có separation theo tầng và DI ở `app.py`, nhưng abstraction transport chưa được dùng trực tiếp bởi file-transfer service: service phụ thuộc concrete `SerialManager`, rồi tự phát `send_packet`. `SerialManager` là facade vừa đóng gói frame qua `pack_frame`, vừa phát trạng thái kết nối/raw/packet events. Đây là dependency phù hợp cho ứng dụng hiện tại nhưng khiến kiểm thử nghiệp vụ phải dựng Qt/manager hoặc mock một API khá rộng.

`docs/ARCHITECTURE.md` cũng ghi `FileTransferService` chỉ nên chunk/reassemble/hash và không gọi UI trực tiếp. Implementation hiện vượt phạm vi đó: nó sở hữu Qt signals, cấu hình/settings, storage, file handles, timer, giao thức, retries và state. Tài liệu architecture nên được cập nhật sau khi thiết kế mới được chấp thuận/thực hiện; không nên coi sơ đồ mục tiêu là mô tả implementation hiện thời.

## 3. Trách nhiệm đang dồn trong service

Các nhóm hành vi thực tế trong file:

1. **Qt façade và UI events:** `QObject`, `progress_updated`, `transfer_completed`, `transfer_failed`, `state_changed`; signals được nối trực tiếp vào `BackendBridge`.
2. **Cấu hình:** load/update max size và receive directory, validate path, persist settings, giữ default directory.
3. **TX orchestration:** validate input/chunk limits, chọn legacy/large opcode, mở file, dựng START/CHUNK/END, sequence, progress, ACK timer.
4. **ARQ:** xác thực ACK/NACK, timeout, retry budget, retransmit cùng sequence và cached payload.
5. **RX protocol/application dispatch:** switch opcode, decode payload, trạng thái busy, validate metadata, thứ tự/duplicate chunk, xử lý ABORT.
6. **Disk I/O:** pass CRC trước TX, đọc chunk theo offset, tạo `.part`, ghi và flush từng chunk, rename atomically, cleanup khi fail/abort.
7. **Integrity:** tính CRC-32 toàn file khi TX; incremental CRC, size reconciliation và compare START/END khi RX.
8. **Persistence/audit:** ghi SUCCESS/FAILED transfer record và quyết định direction/path/size/status.
9. **State machine:** trạng thái chung TX/RX, transitions, reset/teardown hai session cùng tồn tại trong một đối tượng.
10. **Logging/diagnostics:** frame summary, opcode/status, hex preview, retry/progress log xen kẽ logic.

Những thành phần protocol framing thuần túy không nằm trong service: `SerialWorker._drain_rx_buffer()` gọi `parse_frames_with_stats`; `SerialManager.send_packet()` gọi `pack_frame`. Dù vậy service vẫn biết opcode, loại payload, sequence semantics và phải phối hợp với framing qua `SerialManager`, tạo coupling ở application protocol boundary.

## 4. Anti-patterns, coupling và điểm rủi ro

### 4.1 God object và state rời rạc

- Một QObject mutable giữ hàng chục trường `_tx_*`, `_rx_*`, `_cached_*`, timer và config. Reset thủ công có thể quên một cache/field; hai nhánh TX/RX dùng chung `_state`, `_seq`, timer/log context.
- `_state` là enum tổng cho hai hướng; logic cần biết hướng từ field phụ, ví dụ `_tx_active` hoặc `_rx_filename`. Một state model riêng cho mỗi direction sẽ làm rõ invariant hơn.
- Các transition và cleanup bị phân tán giữa `_fail_tx`, `_fail_rx`, `abort_transfer`, remote pause, ACK finalization và reset helpers. Thứ tự “emit/persist/cleanup” có ý nghĩa và dễ bị thay đổi ngoài ý muốn.

### 4.2 Coupling giữa domain, Qt và adapters

- Service kế thừa QObject và dùng QTimer trực tiếp, nên unit test nghiệp vụ phụ thuộc Qt event loop/`qtbot`.
- Service phụ thuộc `SerialManager`, `StorageService`, filesystem/path helper, logger và Qt signals. Không có port nhỏ cho gửi packet, clock/timer, transfer repository hay file staging.
- `BackendBridge` biết concrete service và nối Qt signals; đây là façade hữu ích nhưng hiện domain event contract trùng với lifecycle Qt.
- Serializer payload đã có trong `protocol/file_payloads.py`; việc chọn opcode/serializer vẫn nằm trong service. Cần phân định rõ protocol adapter không sở hữu ARQ hay filesystem.

### 4.3 Threading / blocking I/O

- `SerialWorker` được `moveToThread(QThread)`; transport polling và `send_bytes()` ở worker. `FileTransferService` được tạo trong `app.py` trên thread tạo QApplication và có QTimer cùng affinity đó.
- `packet_received` được forward qua `SerialManager` rồi kết nối tới `_handle_packet`. Với receiver QObject và AutoConnection, handler thường được gọi queued ở thread affinity của service. Do đó `_on_data_chunk_received()` ghi+flush, `_on_transfer_end_received()` rename/storage, và ACK/NACK handler tiếp tục đọc chunk đồng bộ ở thread service.
- `start_transfer()` tính CRC bằng vòng đọc block 1 MiB trước khi phát START; kích thước RAM có giới hạn nhưng thời gian disk I/O vẫn tuyến tính theo dung lượng và có thể làm UI khựng.
- RX flush mỗi chunk có lợi cho khả năng thấy dữ liệu `.part` sau crash nhưng có thể tăng latency đáng kể; thao tác này nằm trên critical path của ACK. Chính sách durability nên là yêu cầu rõ ràng, cân nhắc flush theo batch hoặc worker chuyên I/O sau khi đo và giữ bảo đảm đã thống nhất.
- Không thấy lock trong service. Điều này an toàn nếu mọi mutation đều được serialize trên một thread; nhưng public methods (`start_transfer`, `abort_transfer`, `update_config`) gọi trực tiếp từ thread khác sẽ race với slot/timer. Contract thread-affinity hiện chưa được enforce/document.
- `send_packet` enqueue qua signal tới worker, do đó API không đồng nghĩa với “đã ghi xuống transport”. Disconnect/shutdown trong lúc session active có thể để timer/retry tiếp diễn nếu service không nhận lifecycle event và fail-fast/cancel session.
- Timeout Qt chỉ được xử lý khi event loop của thread sở hữu timer chạy. Blocking CRC/storage trên cùng thread có thể làm ACK timeout bị trễ, ngoài việc làm UI phản hồi chậm.

### 4.4 Protocol / state correctness risks cần bảo toàn

- Stop-and-Wait có các invariant quan trọng: cùng seq khi retry; chỉ ACK đúng seq hiện tại mới advance; stale ACK/NACK bị bỏ qua; chunk duplicate được re-ACK không ghi lần hai; chunk out-of-order bị NACK; duplicate START/END idempotent.
- Sequence là 8-bit và quay vòng; nhận diện START/END hiện dựa trên sequence cùng session fields/state. Cần giữ tests cho wraparound, duplicate muộn và boundary giữa phiên liên tiếp.
- TX/RX đều có thể hoạt động qua cùng service, nhưng guard half-duplex và remote cancel được cài ở nhiều handler. Refactor không nên vô tình biến hỗ trợ concurrent chat/file mock thành concurrent TX/RX file hoặc thay đổi semantics ABORT.
- RX filename được rút về basename trước publish và collision được giải quyết bằng timestamp; test path traversal/collision cần giữ.
- `FileStart` cũ và large extension có layout khác nhau; giới hạn chunk là 510B legacy / 508B large do prefix ID và payload max 512B. Cần giữ compatibility byte-for-byte.
- Tên `IntegrityVerifier` nên phản ánh hiện thực: MVP kiểm CRC-32 và byte count. SHA-256 là Post-MVP theo protocol/docs, không đưa vào refactor nếu chưa có yêu cầu wire/product tương ứng.

## 5. Kiến trúc mục tiêu đề xuất

Giữ package `services/file_transfer/` hoặc các module con bên dưới `services/`; `FileTransferService` hiện tại trở thành façade tương thích trong giai đoạn migration. Đừng tách thành nhiều lớp chỉ để đổi tên: mỗi module cần API nhỏ, dữ liệu truyền rõ, và ownership xác định.

```text
UI / BackendBridge
        │ commands + presentation events
        ▼
FileTransferService (façade / application coordinator)
        ├── StateCoordinator  (session ownership, transitions, invariants)
        ├── TxManager         (TX Stop-and-Wait flow)
        ├── RxManager         (RX flow and duplicate/order rules)
        ├── ProtocolAdapter   (opcode ↔ typed message/payload mapping)
        ├── IntegrityVerifier (streaming CRC-32 and size checks)
        ├── ChunkBuffer       (bounded chunk read / ordered staged write)
        ├── TransferRepository (StorageService adapter)
        └── PacketPort / Scheduler / FileStore (small injected ports)
                │
                ▼
       SerialManager → SerialWorker/QThread → ITransport
```

### Ranh giới và ownership

| Module | Trách nhiệm | Không sở hữu |
|---|---|---|
| `FileTransferService` | Giữ public API/signals cũ; nhận command/message; điều phối; chuyển domain event thành Qt signal | Chi tiết payload, file handle, retry counter |
| `TxManager` | TX session, START/chunk/END, ACK/NACK/timeout, retry/seq invariants | UI, settings, disk implementation |
| `RxManager` | Nhận START/chunk/END/ABORT; order/duplicate/session validation | Serial worker, UI, storage JSON |
| `ChunkBuffer` / `FileStore` adapter | Đọc chunk có giới hạn bộ nhớ; tạo/ghi/flush/publish/delete staging file; khóa directory/session | ARQ decisions, opcode |
| `IntegrityVerifier` | CRC-32 streaming/incremental + kiểm byte count; interface có thể mở rộng hash sau này | Protocol framing, persistence |
| `ProtocolAdapter` | Chuyển typed transfer message thành opcode/payload và decode ngược; mapping legacy/large | Retries, state, file I/O |
| `StateCoordinator` | Quy tắc state per direction, busy/half-duplex policy, transition validation, abort/teardown coordination | Qt widgets, binary serialization |
| `TransferRepository` | Ghi SUCCESS/FAILED records và settings thông qua `StorageService` | Giao thức và transport |
| `PacketPort` | Port gửi packet và subscribe packet đã parse; adapter hiện tại bọc `SerialManager` | Framer/transport details trong use cases |
| `Scheduler` | Timeout/clock injectable; adapter Qt dùng QTimer hoặc event dispatch | Quyết định retry business |

Không nhất thiết tạo ngay cả tám class. Incremental minimum khả thi: tách pure protocol mapping, file/integrity primitives và Tx/Rx state holders; sau đó mới tách coordinator/persistence khi seams đã ổn định. Nếu cần file I/O nền, dùng worker/service object riêng với command/result messages và giới hạn rõ thread ownership; không chuyển `QTimer`/QObject tùy tiện giữa threads.

## 6. Migration plan theo từng bước

### Bước 0 — Baseline và contract freeze

- Ghi lại test hiện có và hành vi public: constructor, properties (`state`, config, `uses_extended_protocol`), methods, signal signatures, retry/timeout constants, file naming/cleanup, storage records.
- Theo repo docs, test suite gần nhất được ghi nhận là **200 passed ngày 2026-10-19**; test file transfer có **38 test methods** trong `tests/unit/test_file_transfer_service.py`, ngoài E2E ở integration. Đây là số trong code/docs, không phải kết quả chạy mới trong review này.
- Không sửa public API cùng lúc với việc di chuyển implementation. Tạo characterization tests cho các behavior chưa có coverage trước khi đổi.
- Thêm test xác định thread affinity của `_handle_packet`, `start_transfer`, file write/flush và timeout. Ghi baseline latency cho file lớn và RX burst nếu cần chứng minh cải thiện.

### Bước 1 — Tách type và protocol mapping thuần

- Gom transfer session dataclasses bất biến: `TxSessionSnapshot`, `RxSessionSnapshot`, `TransferProgress`, `TransferResult`, `TransferFailure` (các loại status rõ ràng, không mang QObject).
- Tách chọn legacy/large, pack/unpack START/CHUNK/END và opcode mapping thành `ProtocolAdapter`, tái sử dụng các payload types hiện có.
- Giữ `FileTransferService._handle_packet` như dispatcher tạm, delegate phần decode/encode. Không đổi bytes trên wire.
- Unit test thuần cho payload boundaries, opcodes, malformed payload, round-trip legacy/large; tests payload hiện tại vẫn chạy nguyên.

### Bước 2 — Tách integrity và file staging

- Chuyển `_compute_file_crc` và incremental CRC/size compare sang `IntegrityVerifier` không phụ thuộc Qt.
- Tách `ChunkBuffer`/file adapter chịu trách nhiệm file handle, seek/read bounded chunk, temp `.part`, ordered append, cleanup và atomic publish.
- Inject receive directory snapshot vào phiên RX để giữ directory lock hiện có và tránh đổi `EXDEV` khi setting đổi giữa phiên.
- Unit test với `tmp_path`: empty file, partial last chunk, short read/write error, CRC mismatch, cleanup idempotent, collision, basename/path traversal, config directory đổi giữa session.
- Chưa đổi flush durability policy trong bước này; chỉ di chuyển nguyên behavior.

### Bước 3 — Tách TX ARQ

- Di chuyển state TX + cached payload + ACK/NACK/timeout/retry handling vào `TxManager`.
- Inject `PacketPort`, `Scheduler`, `Clock`, file chunk reader và event sink. Scheduler test dùng fake clock, tránh `sleep`/Qt event loop cho logic retry.
- `FileTransferService` forward packet/timer callbacks; giữ signal/properties công khai. Chỉ một module sở hữu sequence/retry counter.
- Bảo toàn tests: stale ACK ignored, error-status ACK, NACK retry, timeout max retries, retransmit identical seq/payload, busy guard, abort ở handshake/transferring/finalizing, empty file.

### Bước 4 — Tách RX và StateCoordinator

- Di chuyển RX validation/order/duplicate/END publish/ABORT vào `RxManager`; trước tiên có thể giữ một state coordinator duy nhất để quản lý half-duplex policy.
- Tách state TX/RX nội bộ; ánh xạ về enum `TransferState` cũ ở façade để UI/tests không phải đổi ngay.
- Bao phủ duplicate START re-ACK, duplicate CHUNK không ghi lặp, out-of-order NACK, duplicate END re-ACK/re-NACK, legacy/large mismatch, invalid metadata, write/publish failure, remote/user abort.
- Giữ lịch sử persistence và event emission tương đương; xác định thứ tự commit file → ACK → SUCCESS record và test cho failure ở từng ranh giới.

### Bước 5 — Giải quyết thread ownership và blocking I/O

- Chọn rõ model: (A) toàn bộ application transfer engine chạy trên worker thread riêng; hoặc (B) state machine ở service thread, disk operations chạy qua file-I/O worker có command/result. Không để cùng mutable state bị sửa đồng thời từ hai thread.
- Ưu tiên command/result queued signals và immutable payload/result objects. Không gọi method trực tiếp cross-thread; mọi command public được marshal vào owner thread.
- TX CRC scan phải chạy nền hoặc chunked incremental có scheduler; RX write/flush/publish không block UI. Nếu ACK cần đợi write thành công, ACK được phát từ completion event sau write (semantics protocol giữ nguyên).
- Định nghĩa disconnect/shutdown: dừng timeout, fail/cancel session, đóng handles, xóa `.part` hoặc giữ tùy policy đã chốt. Test race disconnect vs ACK/timeout/abort.
- Đo UI responsiveness, timeout accuracy, throughput và memory sau thay đổi. RAM vẫn `O(chunk_size + bounded queues)`; không buffer toàn file.

### Bước 6 — Di trú API, tài liệu, rồi dọn façade

- Giữ import path `ir_terminal.services.file_transfer_service.FileTransferService` và signal signatures ít nhất qua một giai đoạn; các internal modules có thể nằm package mới.
- Di chuyển tests theo module mới nhưng giữ nguyên E2E public facade. Tránh rewrite toàn bộ 38+ test vào một lần.
- Khi mọi consumer chỉ dùng façade/ports mới, xóa helpers/private fields cũ; cập nhật `ARCHITECTURE.md`, `docs/README.md` và module docstrings để phản ánh implementation thật.
- SHA-256 chỉ là extension riêng sau này; không gắn vào bước refactor CRC-32 này.

## 7. Test strategy và tiêu chí không regression

### Phân tầng coverage

1. **Pure unit tests:** ProtocolAdapter, CRC verifier, chunk/file store, state transition rules, retry policy với fake clock.
2. **Facade contract tests:** API/signals hiện tại, event ordering, config, storage record, public state mapping.
3. **Qt/thread tests:** signal queued delivery, owner thread assertions, timer timeout và không block main thread khi xử lý file lớn.
4. **Integration tests:** hai node mock, dual-link, chat đồng thời file, persistence, abort hai chiều, transfer lớn/empty.
5. **Regression suite:** toàn bộ tests hiện có trước và sau mỗi bước tách có thể chạy nguyên; chỉ đổi test khi contract thay đổi đã được chủ động chấp thuận.

### Ma trận behavior bắt buộc

| Nhóm | Cases phải giữ |
|---|---|
| TX start | missing/unreadable file, size cap, chunk boundary, legacy↔large threshold, CRC prepass |
| ARQ | ACK đúng/sai seq, stale ACK/NACK, NACK/status error, timeout, retry limit, cùng seq và payload khi retry |
| TX data | empty file, last short chunk, short read, cancel theo state |
| RX start | malformed payload, invalid size/chunk/count, busy, duplicate START, directory snapshot, temp creation failure |
| RX chunks | đúng thứ tự, duplicate re-ACK/no duplicate write, out-of-order, wrong opcode, wrong length, write failure |
| RX end | missing chunks, wrong byte count, CRC disagreement, duplicate END, rename failure, collision/path safety |
| Lifecycle | local/remote abort, disconnect/shutdown, state terminal/reset, cleanup file handle/temp |
| Integration | two-node success/failure, concurrent chat, dual-link, history persistence, large streaming |

### Định nghĩa “không break”

- Wire bytes/opcodes/payload compatibility được kiểm bằng payload tests/golden vectors; không chỉ dựa vào hai app Python cùng sửa.
- Các signal công khai vẫn phát đúng số lần, đúng tham số và thứ tự quan sát được; duplicate END không phát `transfer_completed` lần hai.
- Retry vẫn đúng giới hạn và giữ nguyên sequence của frame retransmit; timeout được kiểm bằng fake clock ở unit, Qt timer chỉ kiểm ở integration.
- File sai CRC/size không được publish; `.part` được dọn theo policy; file hoàn tất chỉ xuất hiện sau verify.
- Test integration chuyển file hai node và concurrent chat vẫn pass; test suite đầy đủ không giảm coverage behavior.
- Thread assertion chứng minh disk/CRC work không chạy UI thread sau Bước 5; không chỉ kiểm tra serial I/O.

## 8. Rủi ro triển khai và kiểm soát

- **Thay đổi event ordering khi tách class:** đóng băng ordering hiện tại bằng characterization tests trước; nhất là ACK/emit/persist/cleanup.
- **Race do worker mới:** quyết định single owner cho mutable state, marshal command/result; không chia field session cho nhiều thread.
- **Protocol regression:** giữ serializers hiện hữu làm nguồn chuẩn; thêm golden vectors và round-trip legacy/large.
- **Qt timer semantics:** inject scheduler; không chuyển QTimer qua thread sau khi tạo; timer phải được tạo và start trên owner thread.
- **Storage chặn event loop:** `StorageService` dùng file JSON; ghi record nên nằm ngoài critical UI path hoặc được đo và thiết kế async có ordering rõ.
- **Scope creep:** không triển khai Selective Repeat, SHA-256, auto-resume hoặc thay đổi wire format trong refactor này. Implementation hiện tại là Stop-and-Wait; tài liệu kiến trúc cần tránh mô tả nhầm “Selective Repeat”.
- **Kế hoạch tài liệu repo:** `docs/ARCHITECTURE.md` nói chỉ có bốn tài liệu docs và quy tắc không thêm planning file, nhưng yêu cầu hiện tại chỉ định tạo báo cáo này. Báo cáo được đặt đúng đường dẫn yêu cầu; sau này nên cập nhật index/architecture để không tạo tài liệu mồ côi.

## 9. Đề xuất thứ tự ưu tiên

1. Chốt characterization + kiểm tra thread-affinity/blocking (Bước 0).
2. Tách protocol mapping và `IntegrityVerifier`/file staging theo nguyên behavior (Bước 1–2).
3. Tách TxManager rồi RxManager, mỗi lần chạy test liên quan và E2E (Bước 3–4).
4. Giải quyết thread ownership với contract ACK-after-write rõ ràng, benchmark và test affinity (Bước 5).
5. Dọn façade và cập nhật tài liệu/index (Bước 6).

Đây là review tĩnh và kế hoạch. Không có test suite nào được chạy trong lần tạo báo cáo này; con số 200 passed là mốc được ghi trong tài liệu repo ngày 2026-10-19.

## 10. Cập nhật thực hiện refactor (2026-10-20)

- Giữ import path `ir_terminal.services.file_transfer_service`, constructor, public properties/methods, signal signatures và enum `TransferState` (re-export từ module types).
- Tách TX handlers sang `file_transfer/tx_manager.py`, RX handlers sang `rx_manager.py`; façade giảm từ khoảng 1.857 dòng xuống 603 dòng. Hai manager là collaborators được compose vào façade và nhận context có type rõ ràng; session fields vẫn được giữ trên façade để bảo toàn các compatibility/internal contracts hiện có.
- Tách `ProtocolAdapter`, `IntegrityVerifier`, `ChunkBuffer`, `StateCoordinator` và `TransferState`; protocol bytes và disk policy giữ nguyên. Bổ sung 4 unit tests cho protocol generations, CRC và staging/publish.
- Sau các bước tách handlers, primitives và file operations, nhóm transfer pass **46 tests**; toàn bộ `tests/` pass **204 tests**. Ruff check/format cho module refactor và component tests pass.
- **Chưa hoàn tất bước 5 về thread ownership:** CRC prepass, RX write/flush/publish, persistence và timer vẫn chạy theo thread affinity hiện tại của service. Giải quyết an toàn cần async command/result flow, test contract cho timing mới và đảm bảo RX chỉ ACK sau write thành công. Không chuyển worker một phần trong đợt này vì duplicate chunk có thể đến trước write completion. Đây là hạng mục còn lại; UI-thread blocking chưa được khắc phục.
- `StateCoordinator` hiện sở hữu giá trị state và chuyển state; chính sách half-duplex vẫn nằm trong handler logic để không đổi hành vi. Việc đưa toàn bộ transitions thành luật domain tách biệt là bước tiếp theo khi session state được chuyển khỏi compatibility façade.
- Strict Mypy trên package mới và façade pass sau khi manager composition được định nghĩa bằng typed context; Ruff check/format cũng pass.
