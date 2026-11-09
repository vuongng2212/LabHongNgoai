#include "main.h"
#include "usb_host.h"
#include "ili9341.h"
#include "fonts.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#define AUTO_CONFIG_STATE  2
#define RING_SIZE          2048U
#define CTRL_LINE_MAX      64U
#define TX_BLOCK_MAX       64U
#define LCD_UPDATE_MS     200U
#define IDLE_RESET_MS      300U
#define DBG_VIEW_LEN       20U

/*
 * UART Mapping & System Architecture:
 * - USART3: PC Interface (Physical USB-to-UART bridge) @ FIXED 115200 bps 8N1.
 *   Receives data and in-band control lines (SET_BAUD:<baud>\n), replies BAUD_OK.
 * - USART2: Optical Interface (MCP2122 IrDA Encoder/Decoder + TFDU4101).
 *   Baud rate dynamic: 2400, 4800, 9600, 19200 bps (matched with MCP2122 16x clock).
 */
SPI_HandleTypeDef hspi2;
UART_HandleTypeDef huart2;
UART_HandleTypeDef huart3;

typedef struct {
  uint8_t  buf[RING_SIZE];
  volatile uint16_t head;
  volatile uint16_t tail;
} RingBuf_t;

/* Ring buffers for transparent bridging */
static RingBuf_t ring_pc_to_ir;
static RingBuf_t ring_ir_to_pc;

static uint8_t uart2_rx_byte = 0;
static uint8_t uart3_rx_byte = 0;

/* Diagnostic counters (monitored in debugger/telemetry) */
static volatile uint32_t cnt_pc_to_ir = 0;
static volatile uint32_t cnt_ir_to_pc = 0;
static volatile uint32_t cnt_ctrl_cmd = 0;
static volatile uint32_t cnt_drop_pc_to_ir = 0; /* Increments if PC->IR ring buffer overflows */
static volatile uint32_t cnt_drop_ir_to_pc = 0; /* Increments if IR->PC ring buffer overflows */
static volatile uint32_t cnt_uart2_err = 0;     /* Optical UART errors (ORE, FE, NE, PE) */
static volatile uint32_t cnt_uart3_err = 0;     /* PC UART errors */
static volatile uint8_t pc_baud_override = 0;   /* Latched when PC sends SET_BAUD command */

static char display_tx_msg[64] = "Waiting for PC data...";
static char display_rx_msg[64] = "Waiting for data...";
static char pc_status_msg[40]  = "PC: WAITING";
static char ir_tx_status_msg[40] = "IR TX: 0 B";
static char ir_rx_status_msg[40] = "IR RX: 0 B";

static uint8_t dbg_tx_bytes[DBG_VIEW_LEN];
static uint8_t dbg_rx_bytes[DBG_VIEW_LEN];
static uint8_t dbg_tx_len = 0;
static uint8_t dbg_rx_len = 0;

static int8_t   current_mode = -1;
static uint32_t current_baud = 0;
static uint32_t applied_ir_baud = 0;

volatile uint8_t dbg_sw_state = 0;
volatile uint8_t dbg_baud1 = 0, dbg_baud2 = 0, dbg_baud3 = 0, dbg_baud4 = 0;
volatile uint8_t auto_state_cmd = AUTO_CONFIG_STATE;

static volatile uint8_t flag_lcd_update = 0;
static volatile uint8_t flag_new_tx = 0;
static volatile uint8_t flag_new_rx = 0;
static uint32_t last_tx_activity_ms = 0;
static uint32_t last_rx_activity_ms = 0;
static uint32_t last_lcd_ms = 0;

typedef enum {
  STATE_STREAM_IDLE = 0,
  STATE_FORWARD_BINARY_FRAME
} BridgeStreamState_t;

static BridgeStreamState_t bridge_state = STATE_STREAM_IDLE;
static uint16_t binary_bytes_left = 0;

void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_USART3_UART_Init(void);
static void MX_SPI2_Init(void);
static void MX_USART2_UART_Init(void);
void MX_USB_HOST_Process(void);

void Process_Switches_And_Display(void);
void DrawMainScreen(void);
void DrawTransferStatus(void);
void DrawTextBox(const char *msg, uint16_t x, uint16_t y, uint16_t bg_color);
void IR_SetBaudrate(uint32_t baud);
void Apply_Hardware_Clock_And_LEDs(uint8_t state_idx);
void Process_Control_Line(const char *line);
void Bridge_Process(void);
void Format_Debug_Hex(char *out, size_t out_sz, const uint8_t *data, uint8_t len);
void Debug_Push_TX(uint8_t b);
void Debug_Push_RX(uint8_t b);
static inline uint16_t Ring_Count(RingBuf_t *r);
static inline int Ring_Push(RingBuf_t *r, uint8_t b);
static inline int Ring_Pop(RingBuf_t *r, uint8_t *b);
static inline int Ring_Peek(RingBuf_t *r, uint16_t offset, uint8_t *b);

static inline uint16_t Ring_Count(RingBuf_t *r)
{
  return (uint16_t)((r->head - r->tail) & (RING_SIZE - 1U));
}

static inline int Ring_Push(RingBuf_t *r, uint8_t b)
{
  uint16_t next = (uint16_t)((r->head + 1U) & (RING_SIZE - 1U));
  if (next == r->tail) return -1;
  r->buf[r->head] = b;
  __DMB();
  r->head = next;
  return 0;
}

static inline int Ring_Pop(RingBuf_t *r, uint8_t *b)
{
  if (r->head == r->tail) return -1;
  *b = r->buf[r->tail];
  __DMB();
  r->tail = (uint16_t)((r->tail + 1U) & (RING_SIZE - 1U));
  return 0;
}

static inline int Ring_Peek(RingBuf_t *r, uint16_t offset, uint8_t *b)
{
  if (offset >= Ring_Count(r)) return -1;
  uint16_t idx = (uint16_t)((r->tail + offset) & (RING_SIZE - 1U));
  *b = r->buf[idx];
  return 0;
}

static inline uint32_t Get_Dynamic_Timeout(uint16_t len, uint32_t baud)
{
  if (baud == 0U) baud = 9600U;
  return (uint32_t)(((uint32_t)len * 10000UL) / baud) + 20UL;
}

void Apply_Hardware_Clock_And_LEDs(uint8_t state_idx)
{
  uint8_t s1 = 0, s0 = 0;
  switch (state_idx)
  {
    case 0: s1 = 0; s0 = 0;
      HAL_GPIO_WritePin(GPIOB, GPIO_PIN_9, GPIO_PIN_SET);
      HAL_GPIO_WritePin(GPIOB, GPIO_PIN_8|GPIO_PIN_7|GPIO_PIN_6, GPIO_PIN_RESET);
      break;
    case 1: s1 = 0; s0 = 1;
      HAL_GPIO_WritePin(GPIOB, GPIO_PIN_8, GPIO_PIN_SET);
      HAL_GPIO_WritePin(GPIOB, GPIO_PIN_9|GPIO_PIN_7|GPIO_PIN_6, GPIO_PIN_RESET);
      break;
    case 2: s1 = 1; s0 = 0;
      HAL_GPIO_WritePin(GPIOB, GPIO_PIN_7, GPIO_PIN_SET);
      HAL_GPIO_WritePin(GPIOB, GPIO_PIN_9|GPIO_PIN_8|GPIO_PIN_6, GPIO_PIN_RESET);
      break;
    case 3: s1 = 1; s0 = 1;
      HAL_GPIO_WritePin(GPIOB, GPIO_PIN_6, GPIO_PIN_SET);
      HAL_GPIO_WritePin(GPIOB, GPIO_PIN_9|GPIO_PIN_8|GPIO_PIN_7, GPIO_PIN_RESET);
      break;
    default: return;
  }
  HAL_GPIO_WritePin(GPIOC, CLK_S1_3_3V_Pin, s1 ? GPIO_PIN_SET : GPIO_PIN_RESET);
  HAL_GPIO_WritePin(GPIOC, CLK_S0_3_3V_Pin, s0 ? GPIO_PIN_SET : GPIO_PIN_RESET);
}

/*
 * Dynamically updates optical baudrate on USART2:
 * - Waits for active transmission complete (TC flag).
 * - Aborts ongoing interrupt reception safely before re-init.
 * - Reinitializes USART2 with new baud rate (2400/4800/9600/19200).
 * - USART3 (PC) is NOT touched and stays at 115200 bps.
 */
void IR_SetBaudrate(uint32_t baud)
{
  if (baud == 0U || applied_ir_baud == baud) return;

  uint32_t t0 = HAL_GetTick();
  while (__HAL_UART_GET_FLAG(&huart2, UART_FLAG_TC) == RESET)
  {
    if ((HAL_GetTick() - t0) > 50U) break;
  }

  HAL_UART_AbortReceive_IT(&huart2);
  HAL_UART_DeInit(&huart2);

  huart2.Instance = USART2;
  huart2.Init.BaudRate = baud;
  huart2.Init.WordLength = UART_WORDLENGTH_8B;
  huart2.Init.StopBits = UART_STOPBITS_1;
  huart2.Init.Parity = UART_PARITY_NONE;
  huart2.Init.Mode = UART_MODE_TX_RX;
  huart2.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart2.Init.OverSampling = UART_OVERSAMPLING_16;

  if (HAL_UART_Init(&huart2) != HAL_OK)
    Error_Handler();

  applied_ir_baud = baud;
  HAL_UART_Receive_IT(&huart2, &uart2_rx_byte, 1);
}

/*
 * In-band baudrate control handler:
 * - Protocol: "SET_BAUD:<baud>\n" sent by PC application over USART3.
 * - Supported rates: 2400, 4800, 9600, 19200 bps.
 * - Software Override: Once PC sends SET_BAUD, pc_baud_override is set to 1,
 *   allowing PC control even if hardware DIP switch was set to manual.
 * - Response: "BAUD_OK:<baud>\n" sent back to PC over USART3.
 *   USART3 remains FIXED at 115200 bps. Only USART2 (optical) baud is updated.
 */
void Process_Control_Line(const char *line)
{
  if (strncmp(line, "SET_BAUD:", 9) != 0)
    return;

  char baud_text[CTRL_LINE_MAX + 1U];
  size_t baud_text_len = 0U;
  while (baud_text_len < CTRL_LINE_MAX && line[9U + baud_text_len] != '\0')
    baud_text_len++;
  if (baud_text_len == CTRL_LINE_MAX)
  {
    const char *err = "BAUD_ERR:invalid\n";
    HAL_UART_Transmit(&huart3, (uint8_t *)err, (uint16_t)strlen(err), 100);
    return;
  }
  memcpy(baud_text, line + 9, baud_text_len + 1U);
  while (baud_text_len > 0U &&
         (baud_text[baud_text_len - 1U] == '\r' || baud_text[baud_text_len - 1U] == '\n'))
    baud_text[--baud_text_len] = '\0';

  if (baud_text_len == 0U)
  {
    const char *err = "BAUD_ERR:invalid\n";
    HAL_UART_Transmit(&huart3, (uint8_t *)err, (uint16_t)strlen(err), 100);
    return;
  }
  for (size_t i = 0; i < baud_text_len; i++)
  {
    if (baud_text[i] < '0' || baud_text[i] > '9')
    {
      const char *err = "BAUD_ERR:invalid\n";
      HAL_UART_Transmit(&huart3, (uint8_t *)err, (uint16_t)strlen(err), 100);
      return;
    }
  }

  char *end = NULL;
  unsigned long parsed_baud = strtoul(baud_text, &end, 10);
  if (end == baud_text || *end != '\0')
  {
    const char *err = "BAUD_ERR:invalid\n";
    HAL_UART_Transmit(&huart3, (uint8_t *)err, (uint16_t)strlen(err), 100);
    return;
  }
  uint32_t new_baud = (uint32_t)parsed_baud;

  if (new_baud != 2400U && new_baud != 4800U &&
      new_baud != 9600U && new_baud != 19200U)
  {
    const char *err = "BAUD_ERR:invalid\n";
    HAL_UART_Transmit(&huart3, (uint8_t *)err, (uint16_t)strlen(err), 100);
    return;
  }

  if      (new_baud == 2400U)  auto_state_cmd = 0;
  else if (new_baud == 4800U)  auto_state_cmd = 1;
  else if (new_baud == 9600U)  auto_state_cmd = 2;
  else                         auto_state_cmd = 3;

  pc_baud_override = 1U;
  Apply_Hardware_Clock_And_LEDs(auto_state_cmd);
  IR_SetBaudrate(new_baud);

  char ok[32];
  snprintf(ok, sizeof(ok), "BAUD_OK:%lu\n", (unsigned long)new_baud);
  HAL_UART_Transmit(&huart3, (uint8_t *)ok, (uint16_t)strlen(ok), 100);

  cnt_ctrl_cmd++;
  snprintf(pc_status_msg, sizeof(pc_status_msg), "PC: CTRL #%lu", (unsigned long)cnt_ctrl_cmd);
  flag_lcd_update = 1;
}

void Format_Debug_Hex(char *out, size_t out_sz, const uint8_t *data, uint8_t len)
{
  size_t pos = 0;
  for (uint8_t i = 0; i < len && (pos + 3U) < out_sz; i++)
  {
    int n = snprintf(out + pos, out_sz - pos, "%02X ", data[i]);
    if (n <= 0) break;
    pos += (size_t)n;
  }
  if (pos > 0U && out[pos - 1U] == ' ') out[pos - 1U] = '\0';
  else out[pos] = '\0';
}

void Debug_Push_TX(uint8_t b)
{
  if (dbg_tx_len < DBG_VIEW_LEN) dbg_tx_bytes[dbg_tx_len++] = b;
  else { memmove(dbg_tx_bytes, dbg_tx_bytes + 1, DBG_VIEW_LEN - 1); dbg_tx_bytes[DBG_VIEW_LEN - 1] = b; }
}

void Debug_Push_RX(uint8_t b)
{
  if (dbg_rx_len < DBG_VIEW_LEN) dbg_rx_bytes[dbg_rx_len++] = b;
  else { memmove(dbg_rx_bytes, dbg_rx_bytes + 1, DBG_VIEW_LEN - 1); dbg_rx_bytes[DBG_VIEW_LEN - 1] = b; }
}

/*
 * Bridge Processing Loop (called continuously in main while(1)):
 * Handles bi-directional communication:
 * 1. PC -> IR:
 *    - Binary Frame Detection: 0x55 0xAA SOF, reads payload length (LE), forwards
 *      exactly (payload_len + 12) bytes (6B header + 4B CRC32 + 2B EOF) without splitting.
 *    - In-Band Command Detection: "SET_BAUD:<baud>\n" is intercepted locally,
 *      processed by Process_Control_Line(), and acknowledged via BAUD_OK. Never leaked to IR.
 *    - Malformed Command Protection: If "SET_BAUD" exceeds 64B without '\n', it is discarded.
 * 2. IR -> PC:
 *    - Fully transparent block forwarding to PC via USART3.
 */
void Bridge_Process(void)
{
  uint8_t block_buf[TX_BLOCK_MAX];
  uint16_t avail;
  uint32_t now = HAL_GetTick();
  uint8_t tx_act = 0, rx_act = 0;
  static const uint8_t control_prefix[] = "SET_BAUD:";

  avail = Ring_Count(&ring_pc_to_ir);
  while (avail > 0U)
  {
    if (bridge_state == STATE_FORWARD_BINARY_FRAME)
    {
      uint16_t chunk = (avail < binary_bytes_left) ? avail : binary_bytes_left;
      if (chunk > TX_BLOCK_MAX) chunk = TX_BLOCK_MAX;

      for (uint16_t i = 0; i < chunk; i++)
        (void)Ring_Pop(&ring_pc_to_ir, &block_buf[i]);

      if (HAL_UART_Transmit(&huart2, block_buf, chunk,
            Get_Dynamic_Timeout(chunk, applied_ir_baud)) == HAL_OK)
      {
        cnt_pc_to_ir += chunk;
        for (uint16_t i = 0; i < chunk; i++) Debug_Push_TX(block_buf[i]);
        tx_act = 1;
        last_tx_activity_ms = now;
      }

      binary_bytes_left = (uint16_t)(binary_bytes_left - chunk);
      if (binary_bytes_left == 0U)
        bridge_state = STATE_STREAM_IDLE;
      break;
    }

    {
      uint8_t b0 = 0, b1 = 0;
      if (Ring_Peek(&ring_pc_to_ir, 0, &b0) == 0 && b0 == 0x55 && avail == 1U)
        break;
      if (Ring_Peek(&ring_pc_to_ir, 0, &b0) == 0 && b0 == 0x55 &&
          Ring_Peek(&ring_pc_to_ir, 1, &b1) == 0 && b1 == 0xAA)
      {
        if (avail < 6U) break;
        uint8_t len_l = 0, len_h = 0;
        if (Ring_Peek(&ring_pc_to_ir, 4, &len_l) != 0 ||
            Ring_Peek(&ring_pc_to_ir, 5, &len_h) != 0)
          break;
        uint16_t payload_len = (uint16_t)(len_l | ((uint16_t)len_h << 8));
        if (payload_len <= 512U)
        {
          binary_bytes_left = (uint16_t)(payload_len + 12U);
          bridge_state = STATE_FORWARD_BINARY_FRAME;
          continue;
        }
      }

      if (Ring_Peek(&ring_pc_to_ir, 0, &b0) == 0 && b0 == control_prefix[0])
      {
        uint16_t prefix_len = (uint16_t)(sizeof(control_prefix) - 1U);
        uint16_t matched = 0;
        uint16_t check_len = (avail < prefix_len) ? avail : prefix_len;
        while (matched < check_len)
        {
          uint8_t value = 0;
          if (Ring_Peek(&ring_pc_to_ir, matched, &value) != 0 ||
              value != control_prefix[matched])
            break;
          matched++;
        }
        if (matched == check_len && avail < prefix_len)
          break;
        if (matched == prefix_len)
        {
          uint16_t line_len = 0;
          uint16_t scan_len = (avail < CTRL_LINE_MAX) ? avail : CTRL_LINE_MAX;
          uint8_t terminator_found = 0;
          for (uint16_t i = prefix_len; i < scan_len; i++)
          {
            uint8_t value = 0;
            if (Ring_Peek(&ring_pc_to_ir, i, &value) != 0)
              break;
            if (value == (uint8_t)'\n')
            {
              line_len = (uint16_t)(i + 1U);
              terminator_found = 1U;
              break;
            }
          }
          if (!terminator_found && avail < CTRL_LINE_MAX)
            break;
          if (!terminator_found && avail >= CTRL_LINE_MAX)
          {
            for (uint16_t i = 0; i < scan_len; i++)
            {
              uint8_t value = 0;
              (void)Ring_Pop(&ring_pc_to_ir, &value);
            }
            break;
          }
          if (terminator_found)
          {
            char line_buf[CTRL_LINE_MAX + 1U];
            for (uint16_t i = 0; i < line_len; i++)
            {
              uint8_t value = 0;
              (void)Ring_Pop(&ring_pc_to_ir, &value);
              line_buf[i] = (char)value;
            }
            line_buf[line_len] = '\0';
            Process_Control_Line(line_buf);
            break;
          }
        }
      }

      uint16_t chunk = (avail > TX_BLOCK_MAX) ? TX_BLOCK_MAX : avail;
      for (uint16_t i = 0; i < chunk; i++)
        (void)Ring_Pop(&ring_pc_to_ir, &block_buf[i]);
      if (HAL_UART_Transmit(&huart2, block_buf, chunk,
            Get_Dynamic_Timeout(chunk, applied_ir_baud)) == HAL_OK)
      {
        cnt_pc_to_ir += chunk;
        for (uint16_t i = 0; i < chunk; i++) Debug_Push_TX(block_buf[i]);
        tx_act = 1;
        last_tx_activity_ms = now;
      }
      break;
    }
  }

  avail = Ring_Count(&ring_ir_to_pc);
  while (avail > 0U)
  {
    uint16_t chunk = (avail > TX_BLOCK_MAX) ? TX_BLOCK_MAX : avail;
    for (uint16_t i = 0; i < chunk; i++)
      (void)Ring_Pop(&ring_ir_to_pc, &block_buf[i]);

    if (HAL_UART_Transmit(&huart3, block_buf, chunk, 50) == HAL_OK)
    {
      cnt_ir_to_pc += chunk;
      for (uint16_t i = 0; i < chunk; i++) Debug_Push_RX(block_buf[i]);
      rx_act = 1;
      last_rx_activity_ms = now;
    }
    avail = Ring_Count(&ring_ir_to_pc);
    if (chunk < TX_BLOCK_MAX) break;
  }

  if ((tx_act || rx_act) && (now - last_lcd_ms) >= LCD_UPDATE_MS)
  {
    last_lcd_ms = now;
    snprintf(ir_tx_status_msg, sizeof(ir_tx_status_msg), "IR TX: %lu B", (unsigned long)cnt_pc_to_ir);
    snprintf(ir_rx_status_msg, sizeof(ir_rx_status_msg), "IR RX: %lu B", (unsigned long)cnt_ir_to_pc);
    snprintf(pc_status_msg, sizeof(pc_status_msg), "PC: BRIDGE OK");
    if (dbg_tx_len > 0)
    {
      Format_Debug_Hex(display_tx_msg, sizeof(display_tx_msg), dbg_tx_bytes, dbg_tx_len);
      flag_new_tx = 1;
    }
    if (dbg_rx_len > 0)
    {
      Format_Debug_Hex(display_rx_msg, sizeof(display_rx_msg), dbg_rx_bytes, dbg_rx_len);
      flag_new_rx = 1;
    }
    flag_lcd_update = 1;
  }

  if (dbg_tx_len > 0 && (now - last_tx_activity_ms) >= IDLE_RESET_MS)
  {
    dbg_tx_len = 0; cnt_pc_to_ir = 0;
    snprintf(display_tx_msg, sizeof(display_tx_msg), "Waiting for PC data...");
    snprintf(ir_tx_status_msg, sizeof(ir_tx_status_msg), "IR TX: 0 B");
    flag_new_tx = 1; flag_lcd_update = 1;
  }
  if (dbg_rx_len > 0 && (now - last_rx_activity_ms) >= IDLE_RESET_MS)
  {
    dbg_rx_len = 0; cnt_ir_to_pc = 0;
    snprintf(display_rx_msg, sizeof(display_rx_msg), "Waiting for data...");
    snprintf(ir_rx_status_msg, sizeof(ir_rx_status_msg), "IR RX: 0 B");
    flag_new_rx = 1; flag_lcd_update = 1;
  }
}

int main(void)
{
  HAL_Init();
  SystemClock_Config();
  MX_GPIO_Init();
  MX_USB_HOST_Init();
  MX_USART3_UART_Init();
  MX_SPI2_Init();
  MX_USART2_UART_Init();

  memset(&ring_pc_to_ir, 0, sizeof(ring_pc_to_ir));
  memset(&ring_ir_to_pc, 0, sizeof(ring_ir_to_pc));

  HAL_UART_Receive_IT(&huart3, &uart3_rx_byte, 1);
  HAL_UART_Receive_IT(&huart2, &uart2_rx_byte, 1);
  applied_ir_baud = huart2.Init.BaudRate;

  Apply_Hardware_Clock_And_LEDs(auto_state_cmd);
  DrawMainScreen();

  while (1)
  {
    MX_USB_HOST_Process();
    Process_Switches_And_Display();
    Bridge_Process();

    if (flag_new_tx)
    {
      flag_new_tx = 0;
      ILI9341_FillRectangle(7, 141, 306, 46, ILI9341_BLACK);
      DrawTextBox(display_tx_msg, 12, 145, ILI9341_BLACK);
    }
    if (flag_new_rx)
    {
      flag_new_rx = 0;
      ILI9341_FillRectangle(7, 207, 306, 25, ILI9341_BLACK);
      DrawTextBox(display_rx_msg, 12, 210, ILI9341_BLACK);
    }
    if (flag_lcd_update)
    {
      flag_lcd_update = 0;
      DrawTransferStatus();
    }
  }
}

void DrawMainScreen(void)
{
  ILI9341_Init();
  ILI9341_FillScreen(ILI9341_BLACK);
  ILI9341_FillRectangle(0, 0, 320, 3, ILI9341_CYAN);
  ILI9341_WriteString(8, 5, "IR FILE / DATA LINK", Font_11x18, ILI9341_CYAN, ILI9341_BLACK);
  ILI9341_FillRectangle(0, 26, 320, 1, ILI9341_COLOR565(48, 58, 72));
  ILI9341_FillRectangle(4, 29, 154, 19, ILI9341_COLOR565(48, 58, 72));
  ILI9341_FillRectangle(162, 29, 154, 19, ILI9341_COLOR565(48, 58, 72));
  ILI9341_WriteString(9, 33, "MODE: --", Font_7x10, ILI9341_WHITE, ILI9341_COLOR565(48, 58, 72));
  ILI9341_WriteString(169, 33, "BAUD: ----", Font_7x10, ILI9341_WHITE, ILI9341_COLOR565(48, 58, 72));
  ILI9341_WriteString(8, 52, "TRANSFER STATUS", Font_7x10, ILI9341_CYAN, ILI9341_BLACK);
  ILI9341_FillRectangle(5, 65, 310, 17, ILI9341_COLOR565(48, 58, 72));
  ILI9341_FillRectangle(5, 84, 310, 17, ILI9341_COLOR565(48, 58, 72));
  ILI9341_FillRectangle(5, 103, 310, 17, ILI9341_COLOR565(48, 58, 72));
  DrawTransferStatus();
  ILI9341_FillRectangle(0, 124, 320, 1, ILI9341_COLOR565(48, 58, 72));
  ILI9341_WriteString(8, 128, "PC -> IR  |  DATA / FILE CONTENT", Font_7x10, ILI9341_GREEN, ILI9341_BLACK);
  ILI9341_FillRectangle(5, 141, 310, 48, ILI9341_BLACK);
  ILI9341_FillRectangle(4, 140, 312, 1, ILI9341_GREEN);
  ILI9341_FillRectangle(4, 189, 312, 1, ILI9341_GREEN);
  ILI9341_FillRectangle(4, 140, 1, 50, ILI9341_GREEN);
  ILI9341_FillRectangle(315, 140, 1, 50, ILI9341_GREEN);
  DrawTextBox(display_tx_msg, 10, 145, ILI9341_BLACK);
  ILI9341_WriteString(8, 194, "IR -> PC  |  RECEIVED CONTENT", Font_7x10, ILI9341_YELLOW, ILI9341_BLACK);
  ILI9341_FillRectangle(5, 207, 310, 27, ILI9341_BLACK);
  ILI9341_FillRectangle(4, 206, 312, 1, ILI9341_YELLOW);
  ILI9341_FillRectangle(4, 234, 312, 1, ILI9341_YELLOW);
  ILI9341_FillRectangle(4, 206, 1, 29, ILI9341_YELLOW);
  ILI9341_FillRectangle(315, 206, 1, 29, ILI9341_YELLOW);
  DrawTextBox(display_rx_msg, 10, 210, ILI9341_BLACK);
}

void DrawTransferStatus(void)
{
  ILI9341_FillRectangle(6, 66, 308, 15, ILI9341_COLOR565(48, 58, 72));
  ILI9341_FillRectangle(6, 85, 308, 15, ILI9341_COLOR565(48, 58, 72));
  ILI9341_FillRectangle(6, 104, 308, 15, ILI9341_COLOR565(48, 58, 72));
  ILI9341_WriteString(10, 68, pc_status_msg, Font_7x10, ILI9341_WHITE, ILI9341_COLOR565(48, 58, 72));
  ILI9341_WriteString(10, 87, ir_tx_status_msg, Font_7x10, ILI9341_GREEN, ILI9341_COLOR565(48, 58, 72));
  ILI9341_WriteString(10, 106, ir_rx_status_msg, Font_7x10, ILI9341_YELLOW, ILI9341_COLOR565(48, 58, 72));
}

void DrawTextBox(const char *msg, uint16_t x, uint16_t y, uint16_t bg_color)
{
  char line[43];
  size_t len = strlen(msg);
  for (uint8_t row = 0; row < 4; row++)
  {
    size_t start = (size_t)row * 42U;
    if (start >= len) break;
    size_t count = len - start;
    if (count > 42U) count = 42U;
    memcpy(line, &msg[start], count);
    line[count] = '\0';
    ILI9341_WriteString(x, (uint16_t)(y + row * 11U), line, Font_7x10, ILI9341_WHITE, bg_color);
  }
}

void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLM = 8;
  RCC_OscInitStruct.PLL.PLLN = 336;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
  RCC_OscInitStruct.PLL.PLLQ = 7;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK) Error_Handler();
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV4;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV2;
  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_5) != HAL_OK) Error_Handler();
}

static void MX_SPI2_Init(void)
{
  hspi2.Instance = SPI2;
  hspi2.Init.Mode = SPI_MODE_MASTER;
  hspi2.Init.Direction = SPI_DIRECTION_2LINES;
  hspi2.Init.DataSize = SPI_DATASIZE_8BIT;
  hspi2.Init.CLKPolarity = SPI_POLARITY_LOW;
  hspi2.Init.CLKPhase = SPI_PHASE_1EDGE;
  hspi2.Init.NSS = SPI_NSS_SOFT;
  hspi2.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_2;
  hspi2.Init.FirstBit = SPI_FIRSTBIT_MSB;
  hspi2.Init.TIMode = SPI_TIMODE_DISABLE;
  hspi2.Init.CRCCalculation = SPI_CRCCALCULATION_DISABLE;
  hspi2.Init.CRCPolynomial = 10;
  if (HAL_SPI_Init(&hspi2) != HAL_OK) Error_Handler();
}

static void MX_USART2_UART_Init(void)
{
  huart2.Instance = USART2;
  huart2.Init.BaudRate = 9600;
  huart2.Init.WordLength = UART_WORDLENGTH_8B;
  huart2.Init.StopBits = UART_STOPBITS_1;
  huart2.Init.Parity = UART_PARITY_NONE;
  huart2.Init.Mode = UART_MODE_TX_RX;
  huart2.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart2.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart2) != HAL_OK) Error_Handler();
}

static void MX_USART3_UART_Init(void)
{
  huart3.Instance = USART3;
  huart3.Init.BaudRate = 115200;
  huart3.Init.WordLength = UART_WORDLENGTH_8B;
  huart3.Init.StopBits = UART_STOPBITS_1;
  huart3.Init.Parity = UART_PARITY_NONE;
  huart3.Init.Mode = UART_MODE_TX_RX;
  huart3.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart3.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart3) != HAL_OK) Error_Handler();
}

static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};
  __HAL_RCC_GPIOE_CLK_ENABLE();
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOH_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();

  HAL_GPIO_WritePin(GPIOE, LCD_RESET_Pin|LCD_CS_Pin|LCD_DC_RS_Pin, GPIO_PIN_RESET);
  HAL_GPIO_WritePin(GPIOC, CLK_S1_3_3V_Pin|STATE_Pin|CLK_S0_3_3V_Pin, GPIO_PIN_RESET);
  HAL_GPIO_WritePin(OTG_FS_PowerSwitchOn_GPIO_Port, OTG_FS_PowerSwitchOn_Pin, GPIO_PIN_SET);
  HAL_GPIO_WritePin(LD4_GPIO_Port, LD4_Pin, GPIO_PIN_RESET);

  GPIO_InitStruct.Pin = LCD_RESET_Pin|LCD_CS_Pin|LCD_DC_RS_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOE, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = CLK_S1_3_3V_Pin|STATE_Pin|CLK_S0_3_3V_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOC, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = OTG_FS_PowerSwitchOn_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(OTG_FS_PowerSwitchOn_GPIO_Port, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = LD4_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(LD4_GPIO_Port, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = GPIO_PIN_6|GPIO_PIN_7|GPIO_PIN_8|GPIO_PIN_9;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = BAUD1_3V3_Pin|BAUD2_3V3_Pin|BAUD3_3V3_Pin|STATE_SW_3_3V_Pin|BAUD4_3V3_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_PULLDOWN;
  HAL_GPIO_Init(GPIOD, &GPIO_InitStruct);
}

void Process_Switches_And_Display(void)
{
  static uint32_t last_sw_ms = 0;
  uint32_t now = HAL_GetTick();
  if ((now - last_sw_ms) < 50U) return;
  last_sw_ms = now;

  dbg_sw_state = HAL_GPIO_ReadPin(GPIOD, STATE_SW_3_3V_Pin);
  dbg_baud1 = HAL_GPIO_ReadPin(GPIOD, BAUD1_3V3_Pin);
  dbg_baud2 = HAL_GPIO_ReadPin(GPIOD, BAUD2_3V3_Pin);
  dbg_baud3 = HAL_GPIO_ReadPin(GPIOD, BAUD3_3V3_Pin);
  dbg_baud4 = HAL_GPIO_ReadPin(GPIOD, BAUD4_3V3_Pin);

  uint8_t mode = (dbg_sw_state != 0U || pc_baud_override != 0U) ? 1U : 0U;
  uint32_t baud = 0;
  uint8_t baud_index = 0;

  if (mode == 1)
  {
    HAL_GPIO_WritePin(GPIOC, GPIO_PIN_14, GPIO_PIN_SET);
    switch (auto_state_cmd)
    {
      case 0: baud = 2400; break;
      case 1: baud = 4800; break;
      case 2: baud = 9600; break;
      case 3: baud = 19200; break;
      default: break;
    }
    Apply_Hardware_Clock_And_LEDs(auto_state_cmd);
  }
  else
  {
    HAL_GPIO_WritePin(GPIOC, GPIO_PIN_14, GPIO_PIN_RESET);
    HAL_GPIO_WritePin(GPIOC, CLK_S1_3_3V_Pin, GPIO_PIN_RESET);
    HAL_GPIO_WritePin(GPIOC, CLK_S0_3_3V_Pin, GPIO_PIN_RESET);

    if (dbg_baud1 == GPIO_PIN_SET) { baud = 19200; baud_index = 1; }
    else if (dbg_baud2 == GPIO_PIN_SET) { baud = 9600; baud_index = 2; }
    else if (dbg_baud3 == GPIO_PIN_SET) { baud = 4800; baud_index = 3; }
    else if (dbg_baud4 == GPIO_PIN_SET) { baud = 2400; baud_index = 4; }

    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_9, (baud_index == 4) ? GPIO_PIN_SET : GPIO_PIN_RESET);
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_8, (baud_index == 3) ? GPIO_PIN_SET : GPIO_PIN_RESET);
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_7, (baud_index == 2) ? GPIO_PIN_SET : GPIO_PIN_RESET);
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_6, (baud_index == 1) ? GPIO_PIN_SET : GPIO_PIN_RESET);
  }

  if (mode != current_mode || baud != current_baud)
  {
    uint32_t old_baud = current_baud;
    current_mode = mode;
    current_baud = baud;

    if (baud != 0U && baud != applied_ir_baud)
    {
      IR_SetBaudrate(baud);
      if (mode == 0 && old_baud != 0U)
      {
        char ntf[32];
        snprintf(ntf, sizeof(ntf), "NOTIFY_BAUD:%lu\n", (unsigned long)baud);
        HAL_UART_Transmit(&huart3, (uint8_t *)ntf, (uint16_t)strlen(ntf), 100);
      }
    }

    ILI9341_FillRectangle(4, 29, 154, 19, ILI9341_COLOR565(48, 58, 72));
    if (mode == 1)
      ILI9341_WriteString(9, 33, "MODE: AUTO", Font_7x10, ILI9341_GREEN, ILI9341_COLOR565(48, 58, 72));
    else
      ILI9341_WriteString(9, 33, "MODE: MANUAL", Font_7x10, ILI9341_YELLOW, ILI9341_COLOR565(48, 58, 72));

    ILI9341_FillRectangle(162, 29, 154, 19, ILI9341_COLOR565(48, 58, 72));
    if (baud != 0U)
    {
      char str_baud[16];
      snprintf(str_baud, sizeof(str_baud), "BAUD: %lu", (unsigned long)baud);
      ILI9341_WriteString(169, 33, str_baud, Font_7x10, ILI9341_CYAN, ILI9341_COLOR565(48, 58, 72));
    }
    else
      ILI9341_WriteString(169, 33, "BAUD: NONE", Font_7x10, ILI9341_RED, ILI9341_COLOR565(48, 58, 72));
  }
}

/*
 * UART RX Complete Interrupt Callback:
 * - USART3: Byte from PC -> pushed to ring_pc_to_ir.
 * - USART2: Byte from Optical (MCP2122) -> pushed to ring_ir_to_pc.
 * Tracks buffer drops with cnt_drop_pc_to_ir and cnt_drop_ir_to_pc.
 */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
  if (huart->Instance == USART3)
  {
    if (Ring_Push(&ring_pc_to_ir, uart3_rx_byte) != 0)
      cnt_drop_pc_to_ir++;
    HAL_UART_Receive_IT(&huart3, &uart3_rx_byte, 1);
  }
  else if (huart->Instance == USART2)
  {
    if (Ring_Push(&ring_ir_to_pc, uart2_rx_byte) != 0)
      cnt_drop_ir_to_pc++;
    HAL_UART_Receive_IT(&huart2, &uart2_rx_byte, 1);
  }
}

/*
 * UART Hardware Error Callback:
 * Handles Overrun (ORE), Framing Error (FE), Noise Error (NE), Parity Error (PE).
 * Essential for optical IR reception where ambient noise or baud mismatch causes ORE/FE.
 * Clears flags by reading SR followed by DR, then re-arms HAL_UART_Receive_IT.
 */
void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
  volatile uint32_t status;
  volatile uint32_t data;

  if (huart->Instance == USART2)
  {
    cnt_uart2_err++;
    status = huart->Instance->SR;
    data = huart->Instance->DR;
    (void)status;
    (void)data;
    HAL_UART_Receive_IT(&huart2, &uart2_rx_byte, 1);
  }
  else if (huart->Instance == USART3)
  {
    cnt_uart3_err++;
    status = huart->Instance->SR;
    data = huart->Instance->DR;
    (void)status;
    (void)data;
    HAL_UART_Receive_IT(&huart3, &uart3_rx_byte, 1);
  }
}

void Error_Handler(void)
{
  __disable_irq();
  while (1) { }
}

#ifdef USE_FULL_ASSERT
void assert_failed(uint8_t *file, uint32_t line)
{
  (void)file; (void)line;
}
#endif
