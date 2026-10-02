# BỘ ĐẶC TẢ GIAO THỨC TRUYỀN THÔNG QUANG HỒNG NGOẠI (IR-OWC SPECS)
**Tài liệu kỹ thuật tích hợp giữa Phần mềm Desktop (PySide6) và Phần cứng STM32F407**

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
- Nếu sau 250ms không nhận được `ACK` $\to$ Retransmit lại đúng chunk đó (tối đa 5 lần).
- STM32 khi nhận frame sai CRC $\to$ Phản hồi `NACK` ngay lập tức để PC gửi lại mà không cần đợi hết 250ms.

---

## 5. MÃ NGUỒN C MẪU DÀNH CHO STM32 (FIRMWARE IMPLEMENTATION)

### 5.1. Hàm tính CRC-32 chuẩn IEEE 802.3
```c
#include <stdint.h>
#include <stddef.h>

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

### 5.2. Hàm Đóng Gói và Gửi ACK từ STM32
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
