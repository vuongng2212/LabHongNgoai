# BỘ ĐẶC TẢ GIAO THỨC TRUYỀN THÔNG QUANG HỒNG NGOẠI (IR-OWC SPECS) — BẢN CHỐT
**Tài liệu kỹ thuật tích hợp giữa Phần mềm Desktop (PySide6) và Phần cứng STM32F407**
**Ràng buộc báo giá:** `LAB_BaoGia.xlsx` — MVP dùng `chunk 240B / CRC-32 / timeout 250ms / retry 5→FAILED`. `SHA-256`, auto-resume, BER đo thực là Post-MVP, chưa áp dụng.

---

## 1. CẤU HÌNH PHẦN CỨNG & VẬT LÝ
- **Vi điều khiển:** STM32F407 (ARM Cortex-M4 @ 168 MHz).
- **Bộ mã hóa/giải mã quang:** Microchip MCP2122 (Baud Rate: 115,200 bps; Bit-duration: $3/16$ pulse width).
- **Mô-đun thu phát hồng ngoại:** Vishay TFDU4101 (Bước sóng đỉnh $\lambda = 880\text{ nm}$, Direct LOS Link).
- **Cấu hình UART STM32:**
  - Baudrate: `115200 bps`
  - Data bits: `8`
  - Parity: `None`
  - Stop bits: `1`
  - Flow Control: `None`

---

## 2. CẤU TRÚC FRAME NHỊ PHÂN (BINARY FRAME LAYOUT)
Mọi trường số nguyên từ 2 bytes trở lên **bắt buộc tuân thủ thứ tự byte Little-Endian** (byte thấp đứng trước).

```
+---------------+---------------+---------------+---------------+-------------------+---------------+---------------+
| SOF (2 Bytes) | SEQ (1 Byte)  | CMD (1 Byte)  | LEN (2 Bytes) | PAYLOAD (N Bytes) | CRC (4 Bytes) | EOF (2 Bytes) |
| 0x55, 0xAA    | 0x00 - 0xFF   | Opcode uint8  | Little-Endian | 0 - 512 Bytes     | IEEE 802.3 LE | 0xAA, 0x55    |
+---------------+---------------+---------------+---------------+-------------------+---------------+---------------+
```

### Chi tiết các trường:
1. **SOF (Start of Frame - 2 bytes):** Cố định `0xAA55` (Truyền trên dây UART: `0x55`, sau đó `0xAA`).
2. **SEQ (Sequence Number - 1 byte):** Số thứ tự gói `0x00` - `0xFF`, tăng tuần hoàn.
3. **CMD (Opcode - 1 byte):** Mã lệnh thực thi.
4. **LEN (Payload Length - 2 bytes Little-Endian):** Độ dài payload thực tế ($0 \le N \le 512$).
5. **PAYLOAD (N bytes):** Dữ liệu ứng dụng (Chat text hoặc File chunk).
6. **CRC32 (Checksum - 4 bytes Little-Endian):** Tính toán từ trường `SEQ` qua hết `PAYLOAD` (không bao gồm SOF). Đa thức chuẩn IEEE 802.3: `0xEDB88320`.
7. **EOF (End of Frame - 2 bytes):** Cố định `0x55AA` (Truyền trên dây UART: `0xAA`, sau đó `0x55`).
8. **Giới hạn phiên file:** `total_chunks` là `uint16` nên tối đa **65.535 chunk/phiên**. Với chunk mặc định `240B`, một phiên tối đa $\approx 15.7\text{ MB}$; file lớn hơn phải tăng `chunk_size` (tối đa `510B` dữ liệu, vì payload frame tối đa `512B` đã gồm 2B `chunk_id`) hoặc chia nhỏ. App reject sớm với lỗi rõ ràng nếu vượt giới hạn.

---

## 3. BẢNG MÃ LỆNH (OPCODES)

| Opcode | Tên lệnh | Mô tả | Chi tiết Payload |
| :---: | :--- | :--- | :--- |
| `0x20` | `PING` | Yêu cầu đo độ trễ liên kết quang (RTT) | Chuỗi ASCII hoặc byte rỗng |
| `0x21` | `PONG` | Phản hồi đo độ trễ quang | Echo lại đúng payload của PING |
| `0x02` | `DATA_TEXT` | Tin nhắn chat thời gian thực | Chuỗi ký tự UTF-8 |
| `0x10` | `ACK` | Báo nhận thành công gói tin | `[acked_seq (uint8), status (uint8)]` |
| `0x11` | `NACK` | Báo nhận gói lỗi / sai CRC | `[failed_seq (uint8), err_code (uint8)]` |
| `0x30` | `TRANSFER_START`| Bắt đầu truyền file (Metadata) | Xem định dạng metadata bên dưới |
| `0x01` | `DATA_CHUNK` | Khối dữ liệu file nhị phân | `[chunk_id (uint16_t), data_bytes...]` |
| `0x31` | `TRANSFER_END` | Hoàn tất truyền file & đối soát | `[file_crc32 (uint32_t), bytes_sent (uint32_t)]` |
| `0x32` | `TRANSFER_PAUSE`| Hủy / Tạm dừng truyền do chắn tia | Mã lý do (ASCII) |

---

## 4. BẮT TAY TRUYỀN FILE (STOP-AND-WAIT ARQ FLOW)

```
[PC Workstation]                                              [STM32 Transceiver]
       |                                                               |
       |------ 1. TRANSFER_START (FileSize, ChunkSize, CRC) --------->| (Mở file/buffer)
       |<----- 2. ACK (Seq #0, Status OK) ----------------------------|
       |                                                               |
       |------ 3. DATA_CHUNK #0 (ChunkID: 0, 240B) ------------------->| (Ghi Flash/RAM)
       |<----- 4. ACK (Seq #1, Status OK) ----------------------------|
       |                                                               |
       |------ 5. DATA_CHUNK #1 (ChunkID: 1, 240B) ------------------->| (Ghi Flash/RAM)
       |<----- 6. ACK (Seq #2, Status OK) ----------------------------|
       |                              ...                              |
       |------ 7. TRANSFER_END (Total Bytes, Whole File CRC) --------->| (Verify Checksum)
       |<----- 8. ACK (Seq #N, Status OK) ----------------------------| (Đóng file)
```

### Quy tắc Timeout & Xử lý lỗi:
- Phía PC gửi 1 chunk $\to$ Chờ `ACK` trong vòng **250ms**.
- Nếu sau 250ms không nhận được `ACK` $\to$ Retransmit lại đúng chunk đó (tối đa 5 lần), **giữ nguyên SEQ** (`new_seq=False`).
- STM32 khi nhận frame sai CRC $\to$ Phản hồi `NACK` ngay lập tức để PC gửi lại mà không cần đợi hết 250ms.
- Nếu `ACK` bị mất, bên gửi sẽ retransmit `START`/`END` với **cùng SEQ**. Bên nhận phải nhận diện đây là duplicate của phiên hiện tại và **gửi lại ACK (idempotent)**, không NACK và không ghi file/lưu bản ghi lần hai.
- `DATA_CHUNK` trùng (`cùng chunk_id`) được **ACK lại** và ghi đè idempotent, không cộng dồn `bytes_received`.
- Trên PC, frame sai CRC/EOF bị `framer` loại bỏ và đếm vào `crc_errors/eof_errors`; bên gửi retry theo timeout 250ms. `NACK CRC` tức thời là hành vi phía STM32 khi STM32 là bên nhận.

---

## 5. MÃ NGUỒN C MẪU DÀNH CHO STM32 (FIRMWARE IMPLEMENTATION)

### 5.1. Định nghĩa C Struct cho các Payload nhị phân (`ir_protocol.h`)
```c
#include <stdint.h>
#include <stddef.h>

#pragma pack(push, 1)

// Payload Opcode 0x30: TRANSFER_START
typedef struct {
    uint32_t file_size;     // 4 bytes: Tổng kích thước file (Bytes, Little-Endian)
    uint16_t chunk_size;    // 2 bytes: Kích thước payload mỗi chunk (thường là 240B)
    uint16_t total_chunks;  // 2 bytes: Tổng số lượng chunk
    uint32_t file_crc32;    // 4 bytes: Mã băm CRC-32 của toàn bộ file
    uint8_t  fname_len;     // 1 byte: Độ dài tên file (N bytes)
    char     filename[];    // N bytes: Chuỗi UTF-8 tên file (không gồm null-terminator)
} FileStartPayload_t;

// Payload Opcode 0x01: DATA_CHUNK
typedef struct {
    uint16_t chunk_id;      // 2 bytes: Thứ tự chunk bắt đầu từ 0 (Little-Endian)
    uint8_t  chunk_data[];  // M bytes: Dữ liệu nhị phân của khối (tối đa 240B)
} FileChunkPayload_t;

// Payload Opcode 0x31: TRANSFER_END
typedef struct {
    uint32_t file_crc32;    // 4 bytes: Mã băm CRC-32 toàn bộ file để đối soát
    uint32_t total_bytes;   // 4 bytes: Tổng số bytes đã truyền
} FileEndPayload_t;

// Payload Opcode 0x10: ACK
typedef struct {
    uint8_t  acked_seq;     // 1 byte: Sequence number của gói vừa nhận thành công
    uint8_t  status;        // 1 byte: 0x00=OK, 0x01=CRC_ERR, 0x02=OUT_OF_ORDER, 0x03=TIMEOUT
} AckPayload_t;

#pragma pack(pop)
```

### 5.2. Hàm tính CRC-32 chuẩn IEEE 802.3
```c
uint32_t CRC32_Calculate(const uint8_t *data, size_t length) {
    uint32_t crc = 0xFFFFFFFF;
    for (size_t i = 0; i < length; i++) {
        crc ^= data[i];
        for (uint8_t j = 0; j < 8; j++) {
            if (crc & 1) {
                crc = (crc >> 1) ^ 0xEDB88320;
            } else {
                crc >>= 1;
            }
        }
    }
    return ~crc;
}
```

### 5.3. Hàm Đóng Gói và Gửi ACK từ STM32
```c
#include <string.h>

void IR_Send_ACK(UART_HandleTypeDef *huart, uint8_t acked_seq) {
    uint8_t frame[14];
    // 1. SOF
    frame[0] = 0x55;
    frame[1] = 0xAA;
    // 2. Header
    frame[2] = acked_seq; // SEQ
    frame[3] = 0x10;      // CMD: ACK
    frame[4] = 0x02;      // LEN LSB
    frame[5] = 0x00;      // LEN MSB
    // 3. Payload
    frame[6] = acked_seq; // acked_seq
    frame[7] = 0x00;      // status: OK
    // 4. CRC32 (tính từ frame[2] đến frame[7])
    uint32_t crc = CRC32_Calculate(&frame[2], 6);
    frame[8]  = (uint8_t)(crc & 0xFF);
    frame[9]  = (uint8_t)((crc >> 8) & 0xFF);
    frame[10] = (uint8_t)((crc >> 16) & 0xFF);
    frame[11] = (uint8_t)((crc >> 24) & 0xFF);
    // 5. EOF
    frame[12] = 0xAA;
    frame[13] = 0x55;

    HAL_UART_Transmit(huart, frame, 14, 100);
}
```

---

## 6. KHUYẾN NGHỊ VỀ TIMING & DMA TRÊN STM32
1. **DMA RX Stream:** Sử dụng UART DMA Circular Mode kết hợp với ngắt **IDLE Line Detection** (`__HAL_UART_ENABLE_IT(&huartX, UART_IT_IDLE)`) để nhận trọn vẹn frame mà không làm nghẽn CPU.
2. **Ring Buffer:** Duy trì bộ đệm tối thiểu 2048 bytes trong RAM STM32 để tránh tràn khi nhận burst packet.

---

## 7. RÀNG BUỘC MVP ↔ POST-MVP (KHỚP `LAB_BaoGia.xlsx`)

* MVP đã chốt: `chunk mặc định 240B` (tối đa `510B` dữ liệu, do payload frame tối đa `512B` đã gồm 2B `chunk_id`), `ACK timeout 250ms`, `retry tối đa 5 → FAILED + gửi lại tay`, toàn vẹn `CRC-32`, `TRANSFER_PAUSE` mang payload `ABORT` khi user hủy, `TRANSFER_RESUME (0x33)` mới chỉ định nghĩa chưa dùng.
* Post-MVP (báo riêng, chưa implement): `0x33 resume` + ping định kỳ để tự phục hồi sau chắn tia; `SHA-256` song song CRC-32; `BER` chỉ bật khi firmware cung cấp counter; streaming chống tràn RAM; hotplug/auto-reconnect; priority queue `Control > Text > File`.
