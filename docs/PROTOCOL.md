# ĐẶC TẢ GIAO THỨC QUANG HỒNG NGOẠI TƯƠNG THÍCH WinForm C#

**Nguồn chuẩn:** `docs/WFF.txt` (ứng dụng WinForm C# của firmware team).
**Phạm vi:** PC Desktop (PySide6) ↔ STM32F407 + MCP2122 + TFDU4101.
**Trạng thái:** giao thức mở, không bắt tay, không kiểm tra toàn vẹn ở tầng frame.

Tài liệu này thay thế hoàn toàn đặc tả cũ dùng SOF `0x55AA`, opcode, CRC-32 và
Stop-and-Wait ARQ. Mọi con số dưới đây khớp với
`src/ir_terminal/protocol/constants.py`, `framer.py` và
`src/ir_terminal/services/file_transfer_service.py`.

---

## 1. CẤU HÌNH PHẦN CỨNG

| Tham số | Giá trị |
| :--- | :--- |
| Vi điều khiển | STM32F407 (Cortex-M4) |
| Bộ mã hóa quang | Microchip MCP2122 |
| Module thu phát | Vishay TFDU4101, bước sóng đỉnh 880 nm |
| COM1 — dữ liệu quang | `2400`, `4800`, `9600` hoặc `19200` bps, 8-N-1 |
| COM2 — điều khiển MCU | `115200` bps cố định, 8-N-1 |
| Flow control | Không dùng |

COM1 và COM2 là hai cổng độc lập. Chọn tốc độ quang chỉ thay đổi COM1; COM2
luôn giữ `115200` bps để nhận lệnh đổi tốc độ.

---

## 2. KHUNG TRUYỀN (FRAME LAYOUT)

Mọi số nhiều byte đều Little-Endian.

```text
+----------+----------------+----------------+-----------------------+
| SYNC     | SEQUENCE       | LENGTH         | PAYLOAD               |
| 2 bytes  | uint32         | uint16         | LENGTH bytes          |
| AA 55    | 4 bytes LE     | 2 bytes LE     | 0 .. 65535 bytes      |
+----------+----------------+----------------+-----------------------+
```

- Tổng overhead cố định: **8 byte** (`HEADER_OVERHEAD`).
- `SYNC` cố định `0xAA`, `0x55` (`SYNC_MAGIC`).
- `LENGTH` là độ dài payload, không gồm 8 byte header.
- Payload tối đa theo trường `uint16`: **65535 byte**.
- Không có trường opcode, checksum, CRC hay byte kết thúc khung.

### 2.1. Ý nghĩa `SEQUENCE`

| Giá trị | Ý nghĩa |
| :--- | :--- |
| `0xFFFFFFFF` (`SEQ_HEADER`) | Khung HEADER mở phiên truyền file |
| `0xFFFFFFFE` (`SEQ_END`) | Khung END kết thúc phiên |
| `0x00000000` .. `0xFFFFFFFD` | Chỉ số chunk dữ liệu, bắt đầu từ 0 |

### 2.2. Payload của HEADER

```text
+------------------+------------------------+-----------------------+
| nameLength       | fileName               | totalPackets          |
| uint16 LE        | UTF-8, nameLength byte | int32 LE              |
+------------------+------------------------+-----------------------+
```

- `nameLength`: độ dài tên file tính bằng byte UTF-8.
- `fileName`: tên file gốc, không có null terminator.
- `totalPackets`: số chunk DATA sẽ gửi, bằng
  `ceil(fileSize / 256)`. File rỗng có `totalPackets = 0`.
- Giá trị âm bị coi là payload không hợp lệ và bị bỏ.

### 2.3. Payload của DATA và END

- DATA: đúng `256` byte dữ liệu cho mọi chunk, trừ chunk cuối có thể ngắn hơn.
- END: payload rỗng (`LENGTH = 0`).

Kích thước file bị giới hạn bởi `totalPackets int32`, tức tối đa
`2.147.483.647` chunk; với chunk 256 byte là khoảng 512 GiB. Giới hạn thực tế
thấp hơn nhiều do bộ nhớ đệm, thời gian truyền và chất lượng kênh quang.

---

## 3. LUỒNG TRUYỀN FILE (OPEN-LOOP PACED STREAMING)

```text
[PC gửi]                                              [PC nhận / STM32]
   |------ HEADER (tên file, totalPackets) ----------------->|  mở file .part
   |------ DATA #0 (256 byte) ------------------------------>|  ghi tại offset 0
   |------ DATA #1 (256 byte) ------------------------------>|  ghi tại offset 256
   |                          ...                            |
   |------ DATA #N-1 (<=256 byte) -------------------------->|  ghi tại offset (N-1)*256
   |------ END (payload rỗng) ------------------------------>|  chốt file, đóng phiên
```

### 3.1. Quy tắc phía gửi

1. Mở file, tính `totalPackets = ceil(size / 256)`.
2. Gửi HEADER với tên file và `totalPackets`.
3. Gửi lần lượt DATA theo chỉ số 0, 1, 2, ...
4. Gửi END sau chunk cuối.
5. Không chờ phản hồi, không retransmit, không đọc ACK.

### 3.2. Nhịp gửi (pacing)

Sau mỗi khung, bên gửi chờ:

```text
delay_ms = ceil(frame_length_bytes * 10.0 * 1000 / baudrate) + 10
```

Trong đó:

- `frame_length_bytes = 8 + payload_length`.
- Hệ số `10` là 8 bit dữ liệu + 1 bit start + 1 bit stop của cấu hình 8-N-1.
- `+10 ms` là biên an toàn cố định (`PACED_DELAY_SAFETY_MS`).

Ví dụ với Baudrate `19200` và DATA đầy đủ 256 byte:

```text
frame_length = 8 + 256 = 264
delay_ms     = ceil(264 * 10 * 1000 / 19200) + 10 = 148 ms
```

Đây là nhịp danh định. Hệ điều hành có thể trễ thêm; firmware phải chịu được
khoảng trống giữa các khung và không được giả định khung đến liền nhau.

### 3.3. Quy tắc phía nhận

1. HEADER hợp lệ mở phiên mới và tạo file tạm `.ir_rx_<tên>.part`.
2. DATA được ghi tại offset `sequence * 256`, không ghi nối tiếp.
3. Chunk trùng được ghi đè cùng vị trí; chunk đến sai thứ tự vẫn đúng chỗ.
4. Chunk mất chỉ để lại khoảng trống, không làm lệch dữ liệu phía sau.
5. END chốt file tại offset lớn nhất đã ghi và đổi tên file tạm thành tên thật.
6. Nếu thiếu chunk khi nhận END, file vẫn được giữ nhưng trạng thái là
   `PARTIAL` và vùng thiếu chứa byte 0.
7. Nếu quá `3.0` giây không có thêm khung khi đang nhận, phiên bị `FAILED`.
8. HEADER mới khi đang nhận thay thế phiên cũ.

---

## 4. TÍNH NĂNG REALTIME CHAT QUA VIRTUAL FILE ENVELOPE

Vì giao thức WinForm C# gốc không có opcode riêng cho Chat/Text, hệ thống mở rộng tính năng Chat thời gian thực bằng cơ chế **Virtual File Envelope** (100% trong suốt ở tầng wire):

- **Phía gửi (TX):**
  - Đóng gói tin nhắn văn bản UTF-8 thành file RAM (`io.BytesIO`).
  - Đặt tên file theo định dạng chuẩn: `_chat_<YYYYMMDD_HHMMSS>_<StationName>.txt`.
  - Truyền qua kênh quang bằng đúng luồng chuẩn: `HEADER -> DATA (1..N chunks 256B) -> END` kèm Paced Delay danh định.
- **Phía nhận (RX):**
  - Nhận diện tiền tố `_chat_` và đuôi `.txt` từ gói HEADER, tự động đệm dữ liệu trong bộ nhớ RAM thay vì ghi ra đĩa `.part`.
  - Khi gói END đến, giải mã UTF-8 và bắn trực tiếp lên bong bóng tin nhắn của Tab **Realtime Chat** trong vòng < 100 ms.
- **Khả năng tương thích ngược:**
  - Nếu bên nhận là ứng dụng WinForm C#, C# vẫn nhận và lưu thành file `.txt` bình thường mà không hề phát sinh lỗi phân tích.

---

## 5. KHÔNG CÓ ARQ, ACK, CRC HAY RETRANSMIT

Giao thức cố tình bỏ toàn bộ cơ chế bảo toàn dữ liệu:

- Không có ACK/NACK.
- Không có timeout chờ phản hồi.
- Không có CRC-32 hay checksum ở tầng frame.
- Không có gửi lại tự động.
- Không có gói PING/PONG.

Hệ quả cần chấp nhận:

| Tình huống | Kết quả |
| :--- | :--- |
| Mất một chunk | File nhận thiếu đúng vùng đó, các vùng sau vẫn đúng offset |
| Nhiễu làm hỏng sync | Framer bỏ qua byte nhiễu và tìm `AA 55` kế tiếp |
| Mất HEADER | Các DATA sau bị bỏ qua vì chưa có phiên nhận |
| Mất END | Phiên nhận kết thúc bằng watchdog 3 giây |
| Đổi baudrate giữa phiên | Bị UI chặn khi transfer đang hoạt động |

Muốn giảm mất dữ liệu, dùng tốc độ thấp hơn, căn chỉnh tia tốt hơn, hoặc gửi
lại file bằng tay. Bên nhận báo `PARTIAL` kèm số packet thiếu.

---

## 6. ĐIỀU KHIỂN BAUDRATE QUANG QUA COM2

### 6.1. Lệnh hai ký tự

Bên gửi ghi lên COM2 mã ASCII hai ký tự, kết thúc bằng `\r` (`0x0D`):

| Baudrate đích | Mã lệnh | Byte trên dây |
| :---: | :---: | :--- |
| `19200` | `00` | `30 30 0D` |
| `9600` | `01` | `30 31 0D` |
| `4800` | `10` | `31 30 0D` |
| `2400` | `11` | `31 31 0D` |

Bảng mã nằm tại `BAUD_COMMANDS` trong `protocol/constants.py`. Không có lệnh
`SET_BAUD:`, không có phản hồi `BAUD_OK`, không có kênh điều khiển dạng dòng
văn bản.

### 5.2. Trình tự đổi tốc độ

1. Kiểm tra không có transfer đang chạy.
2. Ghi mã lệnh + `\r` xuống COM2 và flush.
3. Chờ `50 ms` (`BAUD_SWITCH_GUARD_MS`) để firmware áp dụng.
4. Đổi Baudrate COM1 tại chỗ, xóa bộ đệm vào/ra.
5. Nếu driver từ chối đổi tại chỗ bằng lỗi serial có thể thử lại, đóng và mở
   lại COM1 với tốc độ mới.

Trên Windows, thao tác mở lại COM1 dùng cơ chế chống lỗi Win32 Error 31
(`ResilientWin32Serial`). Nếu cổng điều khiển COM2 chưa mở, lệnh đổi tốc độ
bị từ chối và UI báo lỗi.

---

## 7. MÃ NGUỒN C MẪU CHO STM32

### 6.1. Tạo khung

```c
#include <stdint.h>
#include <string.h>

#define IR_PACKET_SIZE 256
#define IR_HEADER      0xFFFFFFFFu
#define IR_END         0xFFFFFFFEu

static void ir_build_frame(
    uint8_t *out,
    uint32_t sequence,
    const uint8_t *payload,
    uint16_t length
) {
    size_t i;

    out[0] = 0xAA;
    out[1] = 0x55;
    out[2] = (uint8_t)(sequence);
    out[3] = (uint8_t)(sequence >> 8);
    out[4] = (uint8_t)(sequence >> 16);
    out[5] = (uint8_t)(sequence >> 24);
    out[6] = (uint8_t)(length);
    out[7] = (uint8_t)(length >> 8);

    for (i = 0; i < length; i++) {
        out[8 + i] = payload[i];
    }
}
```

### 6.2. Tạo payload HEADER

```c
static uint16_t ir_build_header_payload(
    uint8_t *out,
    const char *file_name,
    uint16_t name_length,
    int32_t total_packets
) {
    memcpy(out, &name_length, 2);
    memcpy(out + 2, file_name, name_length);
    memcpy(out + 2 + name_length, &total_packets, 4);
    return (uint16_t)(2 + name_length + 4);
}
```

### 6.3. Máy trạng thái nhận khung

```c
typedef enum {
    IR_WAIT_SYNC_1,
    IR_WAIT_SYNC_2,
    IR_WAIT_SEQUENCE,
    IR_WAIT_LENGTH,
    IR_WAIT_PAYLOAD
} ir_state_t;

typedef struct {
    ir_state_t state;
    uint8_t    header[6];
    uint8_t    header_index;
    uint32_t   sequence;
    uint16_t   length;
    uint16_t   received;
    uint8_t    payload[IR_PACKET_SIZE];
} ir_parser_t;

void ir_parser_reset(ir_parser_t *parser) {
    parser->state = IR_WAIT_SYNC_1;
    parser->header_index = 0;
    parser->received = 0;
}

void ir_parser_feed(ir_parser_t *parser, uint8_t byte) {
    switch (parser->state) {
    case IR_WAIT_SYNC_1:
        if (byte == 0xAA) {
            parser->state = IR_WAIT_SYNC_2;
        }
        break;
    case IR_WAIT_SYNC_2:
        parser->state = (byte == 0x55) ? IR_WAIT_SEQUENCE : IR_WAIT_SYNC_1;
        break;
    case IR_WAIT_SEQUENCE:
        parser->header[parser->header_index++] = byte;
        if (parser->header_index == 6) {
            memcpy(&parser->sequence, parser->header, 4);
            memcpy(&parser->length, parser->header + 4, 2);
            parser->received = 0;
            parser->state = (parser->length > IR_PACKET_SIZE)
                ? IR_WAIT_SYNC_1
                : IR_WAIT_PAYLOAD;
        }
        break;
    case IR_WAIT_PAYLOAD:
        parser->payload[parser->received++] = byte;
        if (parser->received == parser->length) {
            ir_on_frame(parser->sequence, parser->payload, parser->length);
            ir_parser_reset(parser);
        }
        break;
    }
}
```

`ir_on_frame` tự quyết định HEADER, DATA hay END dựa trên `sequence`.
Với DATA, offset ghi là `sequence * 256`.

### 6.4. Xử lý lệnh baudrate

```c
static void ir_on_control_byte(uint8_t byte) {
    static char command[2];
    static uint8_t count = 0;

    if (byte == '\r') {
        if (count == 2) {
            uint8_t code = (uint8_t)((command[0] - '0') << 1)
                         | (uint8_t)(command[1] - '0');
            ir_apply_baud_code(code);
        }
        count = 0;
        return;
    }
    if (byte == '0' || byte == '1') {
        if (count < 2) {
            command[count++] = (char)byte;
        } else {
            count = 0;
        }
    }
}
```

Ánh xạ `code`: `0b00 = 19200`, `0b01 = 9600`, `0b10 = 4800`,
`0b11 = 2400`.

---

## 8. KHUYẾN NGHỊ FIRMWARE

1. Dùng DMA RX circular kèm ngắt IDLE để không bỏ byte giữa các khung.
2. Duy trì ring buffer tối thiểu 2048 byte cho luồng quang.
3. Kiểm tra `length <= 256` trước khi ghi payload để bảo vệ RAM.
4. Ghi file theo offset `sequence * 256`, không ghi nối tiếp, để mất gói chỉ
   tạo lỗ hổng thay vì làm lệch toàn bộ file.
5. Không chèn header/footer riêng của MCU vào luồng byte.
6. Khi phát, tránh thu lại chính tín hiệu của mình nếu mạch chưa cô lập quang.

---

## 9. ĐỐI CHIẾU CODE

| Thành phần | Vị trí |
| :--- | :--- |
| Hằng số wire format | `src/ir_terminal/protocol/constants.py` |
| Đóng/mở khung | `src/ir_terminal/protocol/framer.py` |
| Mô hình frame | `src/ir_terminal/protocol/packet.py` |
| Gửi/nhận theo nhịp | `src/ir_terminal/services/file_transfer_service.py` |
| COM1/COM2 và lệnh baud | `src/ir_terminal/core/dual_serial_transport.py` |
| Worker đọc COM1 | `src/ir_terminal/core/serial_worker.py` |
| Slot UI ↔ backend | `src/ir_terminal/ui/backend_bridge.py` |
| Kiểm thử byte-exact | `tests/unit/test_winform_wire_compatibility.py` |
