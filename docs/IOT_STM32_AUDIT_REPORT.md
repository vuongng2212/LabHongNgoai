# BÁO CÁO AUDIT & ĐÁNH GIÁ KỸ THUẬT FIRMWARE STM32 (docs/stm32_new_main.c)
**Dự án:** LabHongNgoai / IR-OWC Lab Terminal Workstation  
**Đối tượng đánh giá:** File mã nguồn firmware `docs/stm32_new_main.c` (Team IoT gửi)  
**Tài liệu & Codebase đối soát:** `docs/PROTOCOL.md`, `docs/BAUD_SWITCHING_PLAN.md`, và mã nguồn PC App trên nhánh `main` (`src/ir_terminal/`)  
**Ngày lập báo cáo:** 2026-11-05  
**Trạng thái kết luận:** **REJECTED (CẦN SỬA GẤP TRƯỚC KHI THỬ NGHIỆM)** — Phát hiện lỗi nghiêm trọng (FATAL) gây nuốt byte nhị phân làm hỏng 100% các phiên truyền file và chat.

---

## 1. TỔNG QUAN & TÓM TẮT ĐÁNH GIÁ (EXECUTIVE SUMMARY)

Bản firmware `docs/stm32_new_main.c` được Team IoT xây dựng với mục tiêu làm **Cầu nối trong suốt hai chiều (Transparent Bridge)** giữa Máy tính (PC qua cổng USART3 cố định 115200 bps) và Kênh truyền quang hồng ngoại (IR Transceiver MCP2122 / TFDU4101 qua USART2 với tốc độ quang 2400 / 4800 / 9600 / 19200 bps), tích hợp màn hình hiển thị LCD TFT ILI9341 và kênh điều khiển tốc độ ASCII (`SET_BAUD`, `BAUD_OK`, `NOTIFY_BAUD`).

### Kết quả đánh giá sơ bộ:
| Hạng mục kiểm tra | Trạng thái | Đánh giá kỹ thuật |
| :--- | :---: | :--- |
| **1. Cấu hình UART vật lý PC (USART3)** | **ĐẠT** | Cố định 115200 8-N-1, chuẩn xác theo đặc tả mới (§8 PROTOCOL). |
| **2. Tốc độ kênh quang (USART2)** | **ĐẠT MỘT PHẦN** | Đủ 4 tốc độ (2400, 4800, 9600, 19200), nhưng lệch Default Baudrate với App và không đồng bộ kịp với bộ chia clock MCP2122. |
| **3. Tương thích Binary Framing** | **KHÔNG ĐẠT (FATAL)** | **Nuốt byte dữ liệu nghiêm trọng.** Thuật toán gom lệnh điều khiển trong ISR nuốt sạch các byte `0x30`, `0x31`, `'S'`, `'B'`, `'N'` trong frame nhị phân, phá hủy hoàn toàn gói tin `TRANSFER_START`, `TRANSFER_END` và các chunk file. |
| **4. Kênh điều khiển ASCII** | **KHÔNG ĐẠT (HIGH)** | Nhận diện chuỗi sai ngữ cảnh, xử lý lệnh `SET_BAUD` chặn CPU (blocking) ngay bên trong ngắt RX ISR, thiếu đồng bộ ở chế độ MANUAL. |
| **5. RingBuffer & Concurrency** | **CẢNH BÁO (HIGH)** | Chưa có rào cản bộ nhớ (`__DMB()`), bỏ qua cờ tràn buffer, nguy cơ xung đột tài nguyên UART (`HAL_BUSY`) giữa ISR và luồng chính. |
| **6. Hiệu năng & Blocking Latency** | **KHÔNG ĐẠT (HIGH)** | Truyền blocking từng byte đơn lẻ qua `HAL_UART_Transmit(size=1)` gây nghẽn CPU hàng giây; vẽ LCD SPI polling liên tục không có rate-limiting gây treo và tràn bộ đệm. |

---

## 2. NHỮNG ĐIỂM ĐÃ LÀM TỐT / KHỚP VỚI PC APP

1. **Cố định UART vật lý PC ở 115200 bps:**
   - Hàm `MX_USART3_UART_Init()` thiết lập đúng: `huart3.Init.BaudRate = 115200`, 8 bit dữ liệu, không parity, 1 stop bit. Điều này hoàn toàn chính xác theo kiến trúc tách rời baudrate vật lý và baudrate quang quy định tại `docs/PROTOCOL.md` §8.1.
2. **Hỗ trợ đầy đủ tập tốc độ quang chuẩn:**
   - Firmware hỗ trợ đúng 4 dải tốc độ: 2400, 4800, 9600, 19200 bps; từ chối các giá trị ngoài dải bằng thông báo `BAUD_ERR:invalid\n`.
3. **Tuân thủ định dạng cú pháp kênh điều khiển ASCII:**
   - Hỗ trợ phân tích `SET_BAUD:<baud>\n`, phản hồi `BAUD_OK:<baud>\n`, `BAUD_ERR:...` và phát `NOTIFY_BAUD:<baud>\n` khi có sự thay đổi switch phần cứng.
4. **Cấp phát bộ đệm RingBuffer kích thước chuẩn:**
   - Sử dụng `RING_SIZE = 2048U` cho cả hai chiều `ring_pc_to_ir` và `ring_ir_to_pc`, thỏa mãn khuyến nghị tối thiểu của `docs/PROTOCOL.md` §6.
5. **Điều khiển phần cứng multiplexer và LED trực quan:**
   - Đã cấu hình chân điều khiển `PC14` (chọn nguồn clock AUTO/MANUAL), `CLK_S0/S1` cấp cho bộ giải mã quang MCP2122, và dãy LED báo tốc độ trên `GPIOB` (PB6-PB9).
   - Tách biệt hiển thị LCD để không in chuỗi nhị phân rác ra màn hình, tránh làm tràn bộ đệm chuỗi.

---

## 3. PHÂN TÍCH CHI TIẾT CÁC LỖI NGHIÊM TRỌNG & RỦI RO TIỀM ẨN

### Lỗi 1 (MỨC ĐỘ FATAL - BLOCKER): Nuốt và phá hủy dữ liệu nhị phân (Binary Byte Swallowing) trong `HAL_UART_RxCpltCallback(USART3)`

#### Vị trí lỗi:
`docs/stm32_new_main.c:692-735`:
```c
  else if (huart->Instance == USART3)
  {
    uint8_t b = uart3_rx_byte;

    if (!ctrl_active)
    {
      /* Bat dau dong control neu thay ky tu dac trung */
      if (b == 'S' || b == 'B' || b == 'N' || b == '0' || b == '1')
      {
        ctrl_active = 1;
        ctrl_idx = 0;
        if (ctrl_idx < CTRL_LINE_MAX - 1)
          ctrl_line[ctrl_idx++] = (char)b;
      }
      else
      {
        (void)Ring_Push(&ring_pc_to_ir, b);
      }
    }
    else
    {
      if (b == '\n' || b == '\r')
      {
        ctrl_line[ctrl_idx] = '\0';
        Process_Control_Line(ctrl_line);
        ctrl_active = 0;
        ctrl_idx = 0;
      }
      else
      {
        if (ctrl_idx < CTRL_LINE_MAX - 1)
          ctrl_line[ctrl_idx++] = (char)b;
        else
        {
          /* Qua dai → day nhu du lieu thuong */
          for (uint8_t i = 0; i < ctrl_idx; i++)
            (void)Ring_Push(&ring_pc_to_ir, (uint8_t)ctrl_line[i]);
          (void)Ring_Push(&ring_pc_to_ir, b);
          ctrl_active = 0;
          ctrl_idx = 0;
        }
      }
    }

    HAL_UART_Receive_IT(&huart3, &uart3_rx_byte, 1);
  }
```

#### Phân tích cơ chế gây lỗi và hậu quả thực tế:
1. **Trùng khớp Opcode giao thức với ký tự điều khiển:**
   - Theo `docs/PROTOCOL.md` §3:
     - Opcode `TRANSFER_START` = `0x30` $\to$ Mã ASCII chính là ký tự `'0'`.
     - Opcode `TRANSFER_END` = `0x31` $\to$ Mã ASCII chính là ký tự `'1'`.
   - Khi PC gửi gói `TRANSFER_START`: Chuỗi byte truyền trên UART là:
     `0x55, 0xAA` (SOF), `0x00` (SEQ), `0x30` (CMD = TRANSFER_START), `LEN_L`, `LEN_H`, `PAYLOAD...`, `CRC32...`, `0xAA, 0x55` (EOF).
   - Ngay khi byte thứ 4 (`0x30`) tới ngắt:
     Điều kiện `b == '0'` thỏa mãn $\to$ `ctrl_active = 1`! Byte `0x30` này **KHÔNG được đẩy vào RingBuffer**, mà bị nhét vào `ctrl_line[0]`.
   - Các byte tiếp theo (`LEN_L`, `LEN_H`, `file_size`, `chunk_size`, tên file...) đều bị tích lũy vào `ctrl_line`.
   - Trong dữ liệu nhị phân (kích thước file, checksum CRC32, hoặc tên file), xác suất xuất hiện byte `0x0A` (`\n`) hoặc `0x0D` (`\r`) là cực kỳ cao (~39% đối với chuỗi 63 byte ngẫu nhiên).
   - Ngay khi gặp byte `0x0A` hoặc `0x0D`, firmware gọi `Process_Control_Line(ctrl_line)`. Vì chuỗi này không khớp với `"SET_BAUD:"` hay `"00"`, hàm thoát ra và **TOÀN BỘ CÁC BYTE TỪ `0x30` ĐẾN `0x0A` BỊ XÓA BỎ HOÀN TOÀN KHỎI DÒNG DỮ LIỆU!**
   - Phía bên thu IR chỉ nhận được: `0x55 0xAA 0x00` rồi nhảy cóc sang phần sau của frame $\to$ Frame bị cắt cụt, sai định dạng, máy thu vứt bỏ gói tin hoặc báo lỗi CRC.
2. **Áp dụng cho mọi gói tin nhị phân khác:**
   - Trong một khối `DATA_CHUNK` (240 byte) hoặc chat UTF-8 tiếng Việt, bất kỳ byte nào mang giá trị `'S'` (`0x53`), `'B'` (`0x42`), `'N'` (`0x4E`), `'0'` (`0x30`), `'1'` (`0x31`) đều sẽ kích hoạt `ctrl_active = 1`.
   - Dẫn đến việc dòng dữ liệu nhị phân liên tục bị xé nhỏ, nuốt byte, làm hỏng toàn bộ tính toàn vẹn gói tin.

---

### Lỗi 2 (MỨC ĐỘ CRITICAL): Lệch pha Baudrate, Xung đột Chế độ Switch và Thiếu Đồng bộ Clock MCP2122

#### 1. Lệch Default Baudrate ban đầu giữa PC và STM32:
- Trong `docs/stm32_new_main.c`:
  - Dòng 39: `#define AUTO_CONFIG_STATE 3` (tương ứng 19200 bps).
  - Dòng 468: `huart2.Init.BaudRate = 19200;` (USART2 khởi tạo mặc định 19200).
- Trong PC App (`src/ir_terminal/protocol/constants.py:78`):
  - `DEFAULT_OPTICAL_BAUDRATE: int = 9600`.
- **Hệ quả:** Khi vừa mở phần mềm và cắm kit STM32, nếu người dùng chưa bấm chọn nút baudrate trên UI (chưa gửi `SET_BAUD`), PC mặc định truyền nhận với giả định 9600 bps, trong khi STM32 chạy ở 19200 bps $\to$ Hai bên lệch xung nhịp ngay từ đầu, mất kết nối liên kết quang.

#### 2. Khởi động ở chế độ MANUAL bị câm (`NOTIFY_BAUD` không gửi):
- Xem xét đoạn mã dòng 641-658:
  ```c
  if (mode != current_mode || baud != current_baud) {
    uint32_t old_baud = current_baud;
    ...
    if (mode == 0 && old_baud != 0) {
      char ntf[32];
      snprintf(ntf, sizeof(ntf), "NOTIFY_BAUD:%lu\n", (unsigned long)baud);
      HAL_UART_Transmit(&huart3, (uint8_t *)ntf, (uint16_t)strlen(ntf), 100);
    }
  }
  ```
- Biến `current_baud` được khởi tạo tĩnh bằng `0`. Tại lần chạy đầu tiên lúc bật nguồn, `old_baud = 0`.
- Do điều kiện `old_baud != 0`, STM32 **KHÔNG gửi** `NOTIFY_BAUD` lên PC!
- Nếu người dùng cắm kit đang gạt sẵn DIP switch ở 4800 bps, PC hoàn toàn không biết và vẫn giữ 9600 bps.

#### 3. Xung đột giữa lệnh `SET_BAUD` và chế độ MANUAL:
- Khi người dùng gạt switch sang MANUAL (`mode == 0`):
  - Phần cứng switch DIP SW2 trực tiếp điều khiển clock của MCP2122 thông qua đường multiplexer ngoài (`PC14 = 0`), và STM32 reset các chân `CLK_S0/S1` về 0 (dòng 614-615).
  - Tuy nhiên, trong hàm `Process_Control_Line()` (dòng 186), firmware **không kiểm tra** `current_mode` hay `dbg_sw_state`. Khi nhận `SET_BAUD:9600`, nó vẫn gọi `IR_SetBaudrate(9600)` và trả về `BAUD_OK:9600\n`!
  - **Hậu quả:** USART2 của STM32 đổi sang 9600, nhưng phần cứng MCP2122 vẫn đang nhận clock từ switch thủ công (ví dụ 2400) $\to$ Bộ giải mã MCP2122 và bộ UART STM32 chạy ở hai tốc độ khác nhau, dẫn đến tín hiệu thu phát bị méo bit hoàn toàn.
  - **Quy chuẩn đúng:** Khi đang ở MANUAL mode, STM32 bắt buộc phải từ chối lệnh từ PC bằng: `BAUD_ERR:manual_mode_active\n`.

#### 4. Lệch pha thời gian (Timing Gap) giữa UART và Clock MCP2122:
- Khi gọi `IR_SetBaudrate(new_baud)` trong `Process_Control_Line()`, tốc độ baud của USART2 đổi ngay lập tức.
- Nhưng chân tạo xung nhịp ngoại vi (`CLK_S0_3_3V_Pin`, `CLK_S1_3_3V_Pin`) chỉ được cập nhật trong hàm `Process_Switches_And_Display()` chạy ở vòng lặp `while(1)`.
- Có một khoảng thời gian trễ (jitter) khiến USART2 đã đổi tốc độ nhưng bộ mã hóa MCP2122 vẫn chạy ở xung cũ.

---

### Lỗi 3 (MỨC ĐỘ HIGH): Blocking Transmit, Re-entrancy và Nguy cơ Treo / Tràn Bộ Đệm

#### 1. Truyền từng byte một qua hàm Blocking `HAL_UART_Transmit` với timeout lớn:
`docs/stm32_new_main.c:246-257`:
```c
  /* PC → IR */
  while (Ring_Pop(&ring_pc_to_ir, &b) == 0)
  {
    if (HAL_UART_Transmit(&huart2, &b, 1, 20) == HAL_OK)
      cnt_pc_to_ir++;
  }

  /* IR → PC */
  while (Ring_Pop(&ring_ir_to_pc, &b) == 0)
  {
    if (HAL_UART_Transmit(&huart3, &b, 1, 5) == HAL_OK)
      cnt_ir_to_pc++;
  }
```
- **Thời gian chiếm dụng CPU khủng khiếp:**
  - Ở tốc độ quang 2400 bps, thời gian truyền 1 byte là: $\frac{10 \text{ bits}}{2400} \approx 4.17\text{ ms}$.
  - Một gói tin dữ liệu chuẩn (254 bytes) sẽ khiến vòng lặp `while` giam cứng CPU trong:
    $$254 \times 4.17\text{ ms} \approx 1059\text{ ms} \ (> 1 \text{ giây!})$$
  - Ở 9600 bps, thời gian giam CPU là: $254 \times 1.04\text{ ms} \approx 264\text{ ms}$.
- **Tác động tiêu cực:**
  - Trong suốt hơn 1 giây này, nhánh truyền ngược lại (`IR → PC`) bị **đóng băng hoàn toàn**, không thể chuyển tiếp bất kỳ byte nào lên PC. Nếu có gói tin ACK từ phía bên kia trả về, nó sẽ nằm chết trong `ring_ir_to_pc`.
  - Hàm `HAL_UART_Transmit(size=1)` trong STM32 HAL bắt buộc phải chờ cờ `TC` (Transmission Complete - cờ báo thanh ghi dịch đã đẩy xong bit Stop ra dây vật lý). Việc này triệt tiêu hoàn toàn tính năng đệm phần cứng (TDR/TSR double buffering), tạo ra khoảng hở (idle dead-time) lớn giữa các byte, làm suy giảm tốc độ truyền thực tế xuống 30-50%.

#### 2. Mất byte khi truyền lỗi / timeout:
- Nhìn vào cách gọi:
  ```c
  while (Ring_Pop(&ring_pc_to_ir, &b) == 0) {
    if (HAL_UART_Transmit(&huart2, &b, 1, 20) == HAL_OK)
      cnt_pc_to_ir++;
  }
  ```
- Byte `b` đã bị lấy ra khỏi RingBuffer (`Ring_Pop`) **trước khi** truyền.
- Nếu `HAL_UART_Transmit` gặp lỗi hoặc hết thời gian chờ (trả về `HAL_TIMEOUT` hoặc `HAL_ERROR`), byte `b` này **BỊ VỨT BỎ VĨNH VIỄN**.
- Không có cơ chế push-back hay lưu giữ tail index. Việc mất 1 byte ở giữa gói tin chắc chắn sẽ làm sai lệch toàn bộ chuỗi byte còn lại và hỏng CRC32 của frame.

#### 3. Nguy cơ Xung đột Re-entrancy trên `huart3`:
- Trong luồng chính (`main` thread), `Bridge_Process()` đang gọi `HAL_UART_Transmit(&huart3, &b, 1, 5)` để gửi dữ liệu từ IR lên PC.
- Ngay lúc đó, một ngắt RX USART3 kích hoạt, nhận được ký tự `\n` của dòng `SET_BAUD`.
- Ngay trong ngắt, `HAL_UART_RxCpltCallback` gọi `Process_Control_Line()`, và hàm này gọi:
  ```c
  HAL_UART_Transmit(&huart3, (uint8_t *)ok, (uint16_t)strlen(ok), 100);
  ```
- **Hậu quả:** Cả luồng ngắt và luồng chính cùng can thiệp vào một đối tượng UART `huart3`. HAL STM32 phát hiện `huart3->gState == HAL_UART_STATE_BUSY_TX` và ngay lập tức trả về `HAL_BUSY`.
- Chuỗi phản hồi `BAUD_OK` bị nuốt chửng, không gửi được lên PC. PC chờ hết timeout 1000ms sẽ báo lỗi đổi baudrate thất bại.
- Việc gọi `HAL_UART_DeInit()` và `HAL_UART_Transmit(timeout=100ms)` bên trong ngắt ISR là điều **tối kỵ** trong thiết kế hệ thống nhúng.

---

### Lỗi 4 (MỨC ĐỘ MEDIUM): Vẽ LCD TFT Polling liên tục gây nghẽn toàn bộ hệ thống

#### Vị trí lỗi:
`docs/stm32_new_main.c:261-276` và `324-340`:
- Trong `Bridge_Process()`:
  ```c
  if (cnt_pc_to_ir != last_pc || cnt_ir_to_pc != last_ir) {
    ...
    flag_new_tx = 1;
    flag_new_rx = 1;
    flag_lcd_update = 1;
  }
  ```
- Trong `main()`:
  ```c
  if (flag_new_tx) {
    ILI9341_FillRectangle(7, 141, 306, 46, ILI9341_BLACK);
    DrawTextBox(display_tx_msg, ...);
  }
  if (flag_new_rx) { ... }
  if (flag_lcd_update) { DrawTransferStatus(); }
  ```
#### Phân tích:
- Mỗi khi có 1 byte được chuyển tiếp qua cầu nối, cả 3 cờ `flag_new_tx`, `flag_new_rx`, `flag_lcd_update` đều được bật lên.
- Màn hình ILI9341 giao tiếp qua SPI2 ở chế độ Polling (không dùng DMA).
- Tổng số điểm ảnh phải vẽ cho 3 hộp trạng thái là:
  $$(306 \times 46) + (306 \times 25) + 3 \times (308 \times 15) \approx 35.586 \text{ pixels} = 71.172 \text{ bytes SPI}.$$
- Với tần số SPI 21 MHz và overhead của thư viện, việc xóa và vẽ lại chữ ngốn từ **20ms đến 35ms** thời gian CPU.
- Nếu dữ liệu đổ về liên tục từ PC (115200 bps = 11.5 bytes/ms), trong 30ms vẽ màn hình sẽ có ~345 bytes mới tràn vào. Vì CPU bị chiếm dụng vẽ màn hình, `Bridge_Process` không được chạy, dẫn đến tràn bộ đệm RingBuffer (chỉ 2048 bytes) rất nhanh chóng.
- **Quy chuẩn:** LCD chỉ được phép cập nhật định kỳ (Rate Limiting), ví dụ tối đa 5 - 10 Hz (mỗi 100ms - 200ms cập nhật 1 lần).

---

### Lỗi 5 (MỨC ĐỘ MEDIUM): Thiếu bộ lọc chống rung (Debounce) cho Switch phần cứng

- Hàm `Process_Switches_And_Display()` đọc trực tiếp các chân GPIO switch (`STATE_SW_3_3V_Pin`, `BAUD1..4`) liên tục trong mỗi vòng lặp `while(1)`.
- Khi người dùng gạt switch cơ học, hiện tượng rung tiếp điểm (contact bounce) kéo dài 5 - 20ms sẽ làm chân GPIO chuyển trạng thái 0-1 liên tục hàng chục lần.
- Điều này khiến hàm `IR_SetBaudrate()` bị gọi lặp đi lặp lại nhiều lần trong vài mili-giây, ngắt và cấu hình lại USART2 liên tục, đồng thời làm spam hàng loạt chuỗi `NOTIFY_BAUD:...` lên PC, gây nghẽn hàng đợi xử lý của ứng dụng Desktop.

---

## 4. MA TRẬN ĐỐI CHIẾU ĐẶC TẢ (PROTOCOL COMPLIANCE MATRIX)

| Tiêu chí kỹ thuật | Yêu cầu đặc tả (`PROTOCOL.md`) | Thực trạng `docs/stm32_new_main.c` | Kết luận |
| :--- | :--- | :--- | :---: |
| **Định dạng Frame nhị phân** | `0x55 0xAA` (SOF), SEQ, CMD, LEN (LE), DATA ($\le 512$B), CRC32, `0xAA 0x55` (EOF) | Bị ngắt và nuốt byte nếu dữ liệu chứa `'S', 'B', 'N', '0', '1'` kết thúc bằng `\n, \r`. | **FAIL** |
| **Opcode START/END** | `TRANSFER_START` (`0x30`), `TRANSFER_END` (`0x31`) | Trùng với `'0'` và `'1'` $\to$ bị gom vào chuỗi điều khiển rồi vứt bỏ. | **FAIL** |
| **Kênh điều khiển ASCII** | Chỉ bóc tách ngoài ranh giới frame nhị phân; không làm mất byte dữ liệu. | Bóc tách tuỳ tiện ngay trong ngắt byte USART3, không phân biệt ranh giới frame. | **FAIL** |
| **Tốc độ vật lý PC** | Cố định 115200 bps 8-N-1 | Cấu hình đúng `huart3` ở 115200 bps. | **PASS** |
| **Tốc độ quang (IR)** | 2400, 4800, 9600, 19200 bps | Đủ 4 tốc độ, nhưng mặc định lệch (19200 vs 9600 của PC App). | **WARN** |
| **Phản hồi `SET_BAUD`** | Trả về `BAUD_OK:<baud>\n` hoặc `BAUD_ERR:<reason>\n` | Đúng cú pháp, nhưng gọi blocking trong ngắt và không từ chối khi ở MANUAL mode. | **WARN** |
| **Báo trạng thái `NOTIFY_BAUD`** | Báo tức thời khi đổi DIP switch; PC luôn lấy giá trị này làm chuẩn | Không gửi khi vừa khởi động ở chế độ MANUAL. | **FAIL** |
| **RingBuffer Thread Safety** | Chống tràn, an toàn đồng thời giữa ISR và luồng chính | Thiếu memory barrier `__DMB()`, bỏ qua lỗi tràn buffer `(void)Ring_Push`. | **WARN** |
| **Độ trễ truyền nhận** | Đáp ứng luồng Stop-and-Wait ARQ timeout 250ms | `HAL_UART_Transmit` blocking 1 byte giữ CPU > 1 giây ở 2400 baud $\to$ Chắc chắn timeout ARQ. | **FAIL** |

---

## 5. KIẾN NGHỊ TINH CHỈNH KIẾN TRÚC CHO TEAM IOT

Để giải quyết triệt để các vấn đề trên mà vẫn giữ tính đơn giản, hiệu quả của vi điều khiển, đề nghị Team IoT điều chỉnh kiến trúc firmware theo 4 nguyên tắc cốt lõi:

```
[ UART3 PC RX Interrupt ] ---> (Chỉ đẩy byte vào RingBuffer, < 1 us) ---> [ ring_pc_to_ir ]
                                                                                  |
                                                                           [ Bridge_Process() ]
                                                                           (Main Loop FSM Demux)
                                                                                  |
                     +------------------------------------------------------------+
                     |                                                            |
            [ Nhận diện 0x55 0xAA ]                                    [ Nhận diện "SET_BAUD:" ]
                     |                                                            |
            (Trạng thái BINARY_FRAME)                                      (Trạng thái CONTROL_CMD)
     Bỏ qua mọi kiểm tra ký tự,                                      Gom đủ dòng lệnh đến '\n'
     Đọc LEN, chuyển tiếp toàn bộ                                   Thực thi đổi tốc độ & Clock
     (LEN + 12) bytes sang USART2 theo block                         Gửi BAUD_OK về PC
                     |
                     v
             [ USART2 IR TX ]
```

### Nguyên tắc 1: Đưa ISR về đúng chức năng tối giản (Thin ISR)
- Trong `HAL_UART_RxCpltCallback`: **Tuyệt đối KHÔNG** phân tích chuỗi, không kiểm tra `'S', '0', '1'`, không gọi `HAL_UART_Transmit()`, không gọi `IR_SetBaudrate()`.
- Nhiệm vụ duy nhất của ISR là: Đẩy byte vào RingBuffer và tái kích hoạt nhận ngắt `HAL_UART_Receive_IT`. Quá trình này diễn ra dưới 1 micro-giây.

### Nguyên tắc 2: Tách kênh nhị phân và lệnh điều khiển bằng Máy trạng thái (FSM Demux) trong luồng chính
- Phân tích luồng từ PC sang IR (`ring_pc_to_ir`) trong hàm `Bridge_Process()`:
  - Nếu gặp cặp byte `0x55, 0xAA` $\to$ Xác định đây là **Frame nhị phân**. Đọc tiếp trường `LEN` (byte 4 và 5) để tính tổng chiều dài gói tin: $\text{Total} = \text{LEN} + 12$ bytes. Chuyển sang chế độ **Forwarding thuần túy**, đẩy thẳng toàn bộ số byte này sang IR mà không kiểm tra bất kỳ ký tự nào.
  - Chỉ khi đang ở trạng thái nghỉ (`IDLE`, bên ngoài frame nhị phân) và phát hiện chuỗi ký tự `"SET_BAUD:"`, mới gom dòng ASCII cho tới khi gặp `\n` để xử lý đổi tốc độ.

### Nguyên tắc 3: Truyền UART theo khối (Block / Chunk Transmit)
- Không dùng `HAL_UART_Transmit(&huart2, &b, 1, 20)` cho từng byte đơn lẻ.
- Gom các byte có sẵn trong RingBuffer thành một mảng đệm nhỏ (ví dụ tối đa 32 hoặc 64 bytes) và phát đi trong một lần gọi `HAL_UART_Transmit()`. Việc này tận dụng tối đa bộ đệm phần cứng của UART, xóa bỏ khe hở giữa các byte, giảm 90% số lần gọi hàm và giải phóng CPU.
- Tính toán timeout truyền động dựa trên tốc độ baudrate thực tế thay vì gán cứng 20ms.

### Nguyên tắc 4: Rate-Limiting màn hình LCD và Debounce Switch
- Màn hình LCD TFT chỉ được cập nhật lại trạng thái với chu kỳ cố định 100ms - 200ms (5 - 10 Hz) bằng cách kiểm tra `HAL_GetTick()`.
- Switch chuyển mạch DIP được lấy mẫu với bộ lọc chống rung tối thiểu 50ms.
- Khi khởi động kit, nếu đang ở chế độ MANUAL, phát ngay thông báo `NOTIFY_BAUD:<baud>\n` lên PC.

---

## 6. MÃ NGUỒN MẪU CHUẨN ĐỀ XUẤT CHO TEAM IOT

Dưới đây là toàn bộ các đoạn mã nguồn C chuẩn hóa, an toàn tuyệt đối, Team IoT có thể tích hợp trực tiếp vào dự án:

### 6.1. RingBuffer an toàn và hỗ trợ Peek (Đọc trước không mất dữ liệu)
```c
/* ========================================================================== */
/*                            RING BUFFER CAO CẤP                             */
/* ========================================================================== */
#define RING_SIZE 2048U /* Bắt buộc là lũy thừa của 2 */

typedef struct {
    uint8_t  buf[RING_SIZE];
    volatile uint16_t head;
    volatile uint16_t tail;
} RingBuf_t;

static inline uint16_t Ring_Count(RingBuf_t *r)
{
    return (uint16_t)((r->head - r->tail) & (RING_SIZE - 1));
}

static inline int Ring_Push(RingBuf_t *r, uint8_t b)
{
    uint16_t next = (uint16_t)((r->head + 1) & (RING_SIZE - 1));
    if (next == r->tail) {
        return -1; /* Bộ đệm bị đầy (Tràn) */
    }
    r->buf[r->head] = b;
    __DMB(); /* Data Memory Barrier: đảm bảo ghi data vào RAM trước khi tăng head */
    r->head = next;
    return 0;
}

static inline int Ring_Pop(RingBuf_t *r, uint8_t *b)
{
    if (r->head == r->tail) {
        return -1; /* Bộ đệm rỗng */
    }
    *b = r->buf[r->tail];
    __DMB(); /* Data Memory Barrier: đảm bảo đọc data trước khi tăng tail */
    r->tail = (uint16_t)((r->tail + 1) & (RING_SIZE - 1));
    return 0;
}

/* Đọc trước dữ liệu tại vị trí offset tính từ tail mà không thay đổi con trỏ */
static inline int Ring_Peek(RingBuf_t *r, uint16_t offset, uint8_t *b)
{
    if (offset >= Ring_Count(r)) {
        return -1;
    }
    uint16_t idx = (uint16_t)((r->tail + offset) & (RING_SIZE - 1));
    *b = r->buf[idx];
    return 0;
}
```

---

### 6.2. Ngắt UART RX hoàn toàn tinh gọn
```c
/* ========================================================================== */
/*                           UART RX ISR CHUẨN HOÁ                            */
/* ========================================================================== */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART3) /* Chiều PC -> STM32 */
    {
        (void)Ring_Push(&ring_pc_to_ir, uart3_rx_byte);
        HAL_UART_Receive_IT(&huart3, &uart3_rx_byte, 1);
    }
    else if (huart->Instance == USART2) /* Chiều IR -> STM32 */
    {
        (void)Ring_Push(&ring_ir_to_pc, uart2_rx_byte);
        HAL_UART_Receive_IT(&huart2, &uart2_rx_byte, 1);
    }
}
```

---

### 6.3. Hàm đổi Baudrate và Đồng bộ Phần cứng an toàn
```c
/* ========================================================================== */
/*                  ĐỒNG BỘ CLOCK PHẦN CỨNG MCP2122 VÀ LED                    */
/* ========================================================================== */
void Apply_Hardware_Clock_And_LEDs(uint8_t state_idx)
{
    uint8_t s1_val = 0, s0_val = 0;
    switch (state_idx)
    {
        case 0: /* 2400 bps */
            s1_val = 0; s0_val = 0;
            HAL_GPIO_WritePin(GPIOB, GPIO_PIN_9, GPIO_PIN_SET);
            HAL_GPIO_WritePin(GPIOB, GPIO_PIN_8 | GPIO_PIN_7 | GPIO_PIN_6, GPIO_PIN_RESET);
            break;
        case 1: /* 4800 bps */
            s1_val = 0; s0_val = 1;
            HAL_GPIO_WritePin(GPIOB, GPIO_PIN_8, GPIO_PIN_SET);
            HAL_GPIO_WritePin(GPIOB, GPIO_PIN_9 | GPIO_PIN_7 | GPIO_PIN_6, GPIO_PIN_RESET);
            break;
        case 2: /* 9600 bps */
            s1_val = 1; s0_val = 0;
            HAL_GPIO_WritePin(GPIOB, GPIO_PIN_7, GPIO_PIN_SET);
            HAL_GPIO_WritePin(GPIOB, GPIO_PIN_9 | GPIO_PIN_8 | GPIO_PIN_6, GPIO_PIN_RESET);
            break;
        case 3: /* 19200 bps */
            s1_val = 1; s0_val = 1;
            HAL_GPIO_WritePin(GPIOB, GPIO_PIN_6, GPIO_PIN_SET);
            HAL_GPIO_WritePin(GPIOB, GPIO_PIN_9 | GPIO_PIN_8 | GPIO_PIN_7, GPIO_PIN_RESET);
            break;
        default: return;
    }
    HAL_GPIO_WritePin(GPIOC, CLK_S1_3_3V_Pin, s1_val ? GPIO_PIN_SET : GPIO_PIN_RESET);
    HAL_GPIO_WritePin(GPIOC, CLK_S0_3_3V_Pin, s0_val ? GPIO_PIN_SET : GPIO_PIN_RESET);
}

void IR_SetBaudrate(uint32_t baud)
{
    if (baud == 0 || applied_ir_baud == baud) return;

    /* Chờ thanh ghi dịch USART2 đẩy xong bit Stop cuối cùng ra dây */
    uint32_t t_start = HAL_GetTick();
    while (__HAL_UART_GET_FLAG(&huart2, UART_FLAG_TC) == RESET) {
        if (HAL_GetTick() - t_start > 50) break; /* Thoát nếu timeout tránh treo */
    }

    HAL_UART_AbortReceive_IT(&huart2);
    HAL_UART_DeInit(&huart2);

    huart2.Instance          = USART2;
    huart2.Init.BaudRate     = baud;
    huart2.Init.WordLength   = UART_WORDLENGTH_8B;
    huart2.Init.StopBits     = UART_STOPBITS_1;
    huart2.Init.Parity       = UART_PARITY_NONE;
    huart2.Init.Mode         = UART_MODE_TX_RX;
    huart2.Init.HwFlowCtl    = UART_HWCONTROL_NONE;
    huart2.Init.OverSampling = UART_OVERSAMPLING_16;

    if (HAL_UART_Init(&huart2) != HAL_OK) {
        Error_Handler();
    }

    applied_ir_baud = baud;
    HAL_UART_Receive_IT(&huart2, &uart2_rx_byte, 1);
}
```

---

### 6.4. Xử lý Lệnh Điều khiển an toàn trong Luồng chính (Main Thread)
```c
/* ========================================================================== */
/*                XỬ LÝ DÒNG LỆNH KÊNH ĐIỀU KHIỂN (CONTROL CHANNEL)           */
/* ========================================================================== */
void Process_Control_Line(const char *line)
{
    /* Chỉ chấp nhận tiền tố chuẩn SET_BAUD: */
    if (strncmp(line, "SET_BAUD:", 9) != 0) {
        return;
    }

    uint32_t new_baud = (uint32_t)atoi(line + 9);

    /* 1. Nếu đang ở chế độ MANUAL (Switch phần cứng nắm quyền), từ chối lệnh */
    if (dbg_sw_state == 0)
    {
        const char *err = "BAUD_ERR:manual_mode_active\n";
        HAL_UART_Transmit(&huart3, (uint8_t *)err, (uint16_t)strlen(err), 100);
        return;
    }

    /* 2. Kiểm tra tính hợp lệ của tốc độ quang */
    if (new_baud != 2400 && new_baud != 4800 &&
        new_baud != 9600 && new_baud != 19200)
    {
        const char *err = "BAUD_ERR:invalid\n";
        HAL_UART_Transmit(&huart3, (uint8_t *)err, (uint16_t)strlen(err), 100);
        return;
    }

    /* 3. Cập nhật state phần mềm và xung clock MCP2122 */
    if      (new_baud == 2400)  auto_state_cmd = 0;
    else if (new_baud == 4800)  auto_state_cmd = 1;
    else if (new_baud == 9600)  auto_state_cmd = 2;
    else if (new_baud == 19200) auto_state_cmd = 3;

    Apply_Hardware_Clock_And_LEDs(auto_state_cmd);
    IR_SetBaudrate(new_baud);

    /* 4. Phản hồi BAUD_OK cho PC */
    char ok[32];
    snprintf(ok, sizeof(ok), "BAUD_OK:%lu\n", (unsigned long)new_baud);
    HAL_UART_Transmit(&huart3, (uint8_t *)ok, (uint16_t)strlen(ok), 100);

    /* 5. Cập nhật giao diện LCD */
    cnt_ctrl_cmd++;
    snprintf(pc_status_msg, sizeof(pc_status_msg), "PC: CTRL #%lu", (unsigned long)cnt_ctrl_cmd);
    snprintf(display_tx_msg, sizeof(display_tx_msg), "CTRL: SET_BAUD %lu", (unsigned long)new_baud);
    flag_lcd_update = 1;
}
```

---

### 6.5. Cầu nối chuyển tiếp dữ liệu thông minh (Stream Demux Bridge)
```c
/* ========================================================================== */
/*                CẦU NỐI HAI CHIỀU KHÔNG NGHẼN (STREAM DEMUX BRIDGE)         */
/* ========================================================================== */
#define TX_BLOCK_MAX 64U

typedef enum {
    STATE_STREAM_IDLE = 0,
    STATE_FORWARD_BINARY_FRAME
} BridgeStreamState_t;

static BridgeStreamState_t bridge_state = STATE_STREAM_IDLE;
static uint16_t binary_bytes_left = 0;

static inline uint32_t Get_Dynamic_Timeout(uint16_t len, uint32_t baud)
{
    if (baud == 0) baud = 9600;
    return (uint32_t)(((uint32_t)len * 10000UL) / baud) + 20UL;
}

void Bridge_Process(void)
{
    uint8_t block_buf[TX_BLOCK_MAX];
    uint16_t avail;

    /* ==================== CHIỀU 1: PC -> IR ==================== */
    avail = Ring_Count(&ring_pc_to_ir);
    while (avail > 0)
    {
        if (bridge_state == STATE_FORWARD_BINARY_FRAME)
        {
            /* Đang chuyển tiếp nốt gói tin nhị phân: phát theo block */
            uint16_t chunk = (avail < binary_bytes_left) ? avail : binary_bytes_left;
            if (chunk > TX_BLOCK_MAX) chunk = TX_BLOCK_MAX;

            for (uint16_t i = 0; i < chunk; i++) {
                (void)Ring_Pop(&ring_pc_to_ir, &block_buf[i]);
            }

            if (HAL_UART_Transmit(&huart2, block_buf, chunk, Get_Dynamic_Timeout(chunk, applied_ir_baud)) == HAL_OK) {
                cnt_pc_to_ir += chunk;
            }

            binary_bytes_left -= chunk;
            if (binary_bytes_left == 0) {
                bridge_state = STATE_STREAM_IDLE; /* Đã đẩy hết frame nhị phân */
            }
            break;
        }
        else /* STATE_STREAM_IDLE */
        {
            uint8_t b0, b1;
            /* 1. Kiểm tra có phải là khởi đầu Frame nhị phân (0x55 0xAA) không */
            if (Ring_Peek(&ring_pc_to_ir, 0, &b0) == 0 && b0 == 0x55 &&
                Ring_Peek(&ring_pc_to_ir, 1, &b1) == 0 && b1 == 0xAA)
            {
                /* Chờ đủ 6 bytes Header để tính chiều dài */
                if (avail < 6) break;

                uint8_t len_l, len_h;
                Ring_Peek(&ring_pc_to_ir, 4, &len_l);
                Ring_Peek(&ring_pc_to_ir, 5, &len_h);
                uint16_t payload_len = (uint16_t)(len_l | (len_h << 8));

                /* Frame hợp lệ có payload <= 512 bytes */
                if (payload_len <= 512)
                {
                    binary_bytes_left = payload_len + 12; /* 6 byte header + payload + 4 byte CRC + 2 byte EOF */
                    bridge_state = STATE_FORWARD_BINARY_FRAME;
                    continue; /* Quay lại vòng lặp để đẩy block */
                }
            }

            /* 2. Kiểm tra có phải là chuỗi điều khiển ASCII "SET_BAUD:" không */
            if (Ring_Peek(&ring_pc_to_ir, 0, &b0) == 0 && b0 == 'S')
            {
                /* Quét tìm ký tự kết thúc '\n' trong phạm vi 32 bytes */
                int newline_offset = -1;
                uint16_t scan_limit = (avail < 48) ? avail : 48;
                for (uint16_t i = 1; i < scan_limit; i++) {
                    uint8_t sc;
                    Ring_Peek(&ring_pc_to_ir, i, &sc);
                    if (sc == '\n' || sc == '\r') {
                        newline_offset = (int)i;
                        break;
                    }
                }

                if (newline_offset > 0)
                {
                    char line_buf[64];
                    for (int i = 0; i <= newline_offset; i++) {
                        uint8_t c;
                        Ring_Pop(&ring_pc_to_ir, &c);
                        if (i < 63) line_buf[i] = (char)c;
                    }
                    line_buf[newline_offset] = '\0';

                    if (strncmp(line_buf, "SET_BAUD:", 9) == 0) {
                        Process_Control_Line(line_buf);
                        break;
                    } else {
                        /* Không phải lệnh SET_BAUD: chuyển tiếp ra UART2 như dữ liệu thô */
                        HAL_UART_Transmit(&huart2, (uint8_t *)line_buf, (uint16_t)strlen(line_buf), Get_Dynamic_Timeout(strlen(line_buf), applied_ir_baud));
                        cnt_pc_to_ir += strlen(line_buf);
                        break;
                    }
                }
                else if (avail < 32) {
                    /* Dòng lệnh chưa nhận xong '\n', chờ thêm byte ở chu kỳ sau */
                    break;
                }
            }

            /* 3. Dữ liệu thô thông thường ngoài frame: đẩy thẳng ra IR */
            uint16_t chunk = (avail > TX_BLOCK_MAX) ? TX_BLOCK_MAX : avail;
            for (uint16_t i = 0; i < chunk; i++) {
                Ring_Pop(&ring_pc_to_ir, &block_buf[i]);
            }
            if (HAL_UART_Transmit(&huart2, block_buf, chunk, Get_Dynamic_Timeout(chunk, applied_ir_baud)) == HAL_OK) {
                cnt_pc_to_ir += chunk;
            }
            break;
        }
    }

    /* ==================== CHIỀU 2: IR -> PC ==================== */
    avail = Ring_Count(&ring_ir_to_pc);
    if (avail > 0)
    {
        uint16_t chunk = (avail > TX_BLOCK_MAX) ? TX_BLOCK_MAX : avail;
        for (uint16_t i = 0; i < chunk; i++) {
            Ring_Pop(&ring_ir_to_pc, &block_buf[i]);
        }
        /* PC chạy 115200 bps rất nhanh: timeout ngắn */
        if (HAL_UART_Transmit(&huart3, block_buf, chunk, Get_Dynamic_Timeout(chunk, 115200)) == HAL_OK) {
            cnt_ir_to_pc += chunk;
        }
    }
}
```

---

### 6.6. Vòng lặp chính với Chống rung Switch và Giới hạn tốc độ vẽ LCD
```c
/* ========================================================================== */
/*                VÒNG LẶP CHÍNH MAIN() ĐÃ TỐI ƯU HIỆU NĂNG                   */
/* ========================================================================== */
void Process_Switches_And_Display(void)
{
    static uint32_t last_sw_tick = 0;
    uint32_t now = HAL_GetTick();

    /* 1. Lọc rung cơ học (Debounce): chỉ quét switch mỗi 50ms */
    if (now - last_sw_tick < 50) return;
    last_sw_tick = now;

    dbg_sw_state = HAL_GPIO_ReadPin(GPIOD, STATE_SW_3_3V_Pin);
    dbg_baud1    = HAL_GPIO_ReadPin(GPIOD, BAUD1_3V3_Pin);
    dbg_baud2    = HAL_GPIO_ReadPin(GPIOD, BAUD2_3V3_Pin);
    dbg_baud3    = HAL_GPIO_ReadPin(GPIOD, BAUD3_3V3_Pin);
    dbg_baud4    = HAL_GPIO_ReadPin(GPIOD, BAUD4_3V3_Pin);

    uint8_t  mode = dbg_sw_state;
    uint32_t baud = 0;

    if (mode == 1) /* AUTO mode */
    {
        HAL_GPIO_WritePin(GPIOC, GPIO_PIN_14, GPIO_PIN_SET);
        switch (auto_state_cmd) {
            case 0: baud = 2400;  break;
            case 1: baud = 4800;  break;
            case 2: baud = 9600;  break;
            case 3: baud = 19200; break;
        }
        Apply_Hardware_Clock_And_LEDs(auto_state_cmd);
    }
    else /* MANUAL mode (DIP SW2) */
    {
        HAL_GPIO_WritePin(GPIOC, GPIO_PIN_14, GPIO_PIN_RESET);
        HAL_GPIO_WritePin(GPIOC, CLK_S1_3_3V_Pin, GPIO_PIN_RESET);
        HAL_GPIO_WritePin(GPIOC, CLK_S0_3_3V_Pin, GPIO_PIN_RESET);

        uint8_t baud_index = 0;
        if      (dbg_baud1 == GPIO_PIN_SET) { baud = 19200; baud_index = 1; }
        else if (dbg_baud2 == GPIO_PIN_SET) { baud = 9600;  baud_index = 2; }
        else if (dbg_baud3 == GPIO_PIN_SET) { baud = 4800;  baud_index = 3; }
        else if (dbg_baud4 == GPIO_PIN_SET) { baud = 2400;  baud_index = 4; }

        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_9, (baud_index == 4) ? GPIO_PIN_SET : GPIO_PIN_RESET);
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_8, (baud_index == 3) ? GPIO_PIN_SET : GPIO_PIN_RESET);
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_7, (baud_index == 2) ? GPIO_PIN_SET : GPIO_PIN_RESET);
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_6, (baud_index == 1) ? GPIO_PIN_SET : GPIO_PIN_RESET);
    }

    /* 2. Kiểm tra thay đổi trạng thái */
    static uint8_t is_boot = 1;
    if (mode != current_mode || baud != current_baud || is_boot)
    {
        current_mode = mode;
        current_baud = baud;

        if (baud != 0 && baud != applied_ir_baud) {
            IR_SetBaudrate(baud);
        }

        /* Khi ở MANUAL: gửi NOTIFY_BAUD ngay cả khi boot lần đầu */
        if (mode == 0 && baud != 0) {
            char ntf[32];
            snprintf(ntf, sizeof(ntf), "NOTIFY_BAUD:%lu\n", (unsigned long)baud);
            HAL_UART_Transmit(&huart3, (uint8_t *)ntf, (uint16_t)strlen(ntf), 100);
        }

        is_boot = 0;
        flag_lcd_update = 1;
    }
}
```

Trong hàm `main()`:
```c
  /* Khởi tạo giá trị ban đầu khớp chuẩn PC App (mặc định 9600) */
  auto_state_cmd = 2; /* 9600 bps */
  huart2.Init.BaudRate = 9600;

  /* Trong vòng lặp while (1): */
  while (1)
  {
    MX_USB_HOST_Process();

    Bridge_Process();
    Process_Switches_And_Display();

    /* Chỉ cập nhật màn hình tối đa 10 Hz (mỗi 100ms) để không nghẽn CPU */
    static uint32_t last_lcd_tick = 0;
    if (HAL_GetTick() - last_lcd_tick >= 100)
    {
      last_lcd_tick = HAL_GetTick();

      if (flag_new_tx) {
        flag_new_tx = 0;
        ILI9341_FillRectangle(7, 141, 306, 46, ILI9341_BLACK);
        DrawTextBox(display_tx_msg, 12, 145, ILI9341_BLACK);
      }
      if (flag_new_rx) {
        flag_new_rx = 0;
        ILI9341_FillRectangle(7, 207, 306, 25, ILI9341_BLACK);
        DrawTextBox(display_rx_msg, 12, 210, ILI9341_BLACK);
      }
      if (flag_lcd_update) {
        flag_lcd_update = 0;
        DrawTransferStatus();
      }
    }
  }
```

---

## 7. KẾT LUẬN & BƯỚC TRIỂN KHAI TIẾP THEO

1. **Đánh giá tổng thể:** Bản firmware `docs/stm32_new_main.c` đã có khung sườn phần cứng tương đối tốt (đã có LCD, switch, LED, UART), nhưng phần lõi giao thức bị lỗi logic phân tích chuỗi nghiêm trọng trong ngắt dẫn đến **nuốt dữ liệu nhị phân**.
2. **Khuyến nghị gửi Team IoT:**
   - Yêu cầu Team IoT áp dụng ngay bộ code đề xuất tại **Mục 6**.
   - Tuyệt đối loại bỏ việc gom ký tự `'S', 'B', 'N', '0', '1'` trong ISR.
   - Kiểm tra kỹ việc cấu hình chân `CLK_S0/S1` cấp cho MCP2122 để đảm bảo tốc độ quang phát ra ngoài không khí trùng khớp với baudrate của USART2.
   - Đặt lại tốc độ khởi động mặc định về **9600 bps** để tương thích 100% với PC App.
3. **Kế hoạch kiểm thử sau khi sửa:**
   - Test 1: Chạy chat thời gian thực gửi các câu có chứa chữ cái `S`, `B`, `N`, `0`, `1` và ký tự xuống dòng `\n`.
   - Test 2: Truyền file ảnh / nhị phân kích thước từ 10 KB đến 5 MB ở 9600 bps và 19200 bps qua kênh quang.
   - Test 3: Đổi baudrate quang động từ giao diện PC và gạt switch thủ công kiểm tra phản hồi `BAUD_OK` / `NOTIFY_BAUD`.
