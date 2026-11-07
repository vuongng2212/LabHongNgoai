/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Transparent Bridge PC <-> IR (MCP2122/TFDU4101)
  *                   STM32F407 + ILI9341 LCD
  *                   Baud quang: 2400 / 4800 / 9600 / 19200 (AUTO mac dinh 19200)
  *                   Protocol binary frames + Control channel SET_BAUD
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "usb_host.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "ili9341.h"
#include "fonts.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */
/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define FRAME_X  0
#define FRAME_Y  0
#define FRAME_W  320
#define FRAME_H  240
#define FRAME_T  1
#define DIVIDER_X 158

/* AUTO mac dinh = 19200 bps -> index 3 */
#define AUTO_CONFIG_STATE  3

/* Ring buffer size – toi thieu 2048 theo PROTOCOL */
#define RING_SIZE          2048U

/* Control line max length */
#define CTRL_LINE_MAX      64U
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */
/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
SPI_HandleTypeDef hspi2;

UART_HandleTypeDef huart2;
UART_HandleTypeDef huart3;

/* USER CODE BEGIN PV */

/* ========== RING BUFFER ========== */
typedef struct {
  uint8_t  buf[RING_SIZE];
  volatile uint16_t head;
  volatile uint16_t tail;
} RingBuf_t;

static RingBuf_t ring_pc_to_ir;   /* USART3 RX → USART2 TX */
static RingBuf_t ring_ir_to_pc;   /* USART2 RX → USART3 TX */

static uint8_t uart2_rx_byte = 0;
static uint8_t uart3_rx_byte = 0;

/* Control line accumulator (from PC) */
static char     ctrl_line[CTRL_LINE_MAX];
static uint8_t  ctrl_idx = 0;
static uint8_t  ctrl_active = 0;

/* Counters */
static volatile uint32_t cnt_pc_to_ir = 0;
static volatile uint32_t cnt_ir_to_pc = 0;
static volatile uint32_t cnt_ctrl_cmd = 0;

/* Status strings – giu nguyen ten bien nhu code cu */
static char display_tx_msg[64] = "Waiting for PC data...";
static char display_rx_msg[64] = "Waiting for data...";
static char pc_status_msg[40]  = "PC: WAITING";
static char ir_tx_status_msg[40] = "IR TX: 0 B";
static char ir_rx_status_msg[40] = "IR RX: 0 B";

/* Switch / baud state */
static int8_t   current_mode = -1;
static uint32_t current_baud = 0;
static uint32_t applied_ir_baud = 0;

volatile uint8_t dbg_sw_state = 0;
volatile uint8_t dbg_baud1    = 0;
volatile uint8_t dbg_baud2    = 0;
volatile uint8_t dbg_baud3    = 0;
volatile uint8_t dbg_baud4    = 0;

/* 0:2400  1:4800  2:9600  3:19200  – mac dinh AUTO = 19200 */
volatile uint8_t auto_state_cmd = AUTO_CONFIG_STATE;

static volatile uint8_t flag_lcd_update = 0;
static volatile uint8_t flag_new_tx = 0;
static volatile uint8_t flag_new_rx = 0;

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_USART3_UART_Init(void);
static void MX_SPI2_Init(void);
static void MX_USART2_UART_Init(void);
void MX_USB_HOST_Process(void);

/* USER CODE BEGIN PFP */
void Process_Switches_And_Display(void);
void DrawMainScreen(void);
void DrawTransferStatus(void);
void DrawTextBox(const char *msg, uint16_t x, uint16_t y, uint16_t bg_color);
void IR_SetBaudrate(uint32_t baud);
void Process_Control_Line(const char *line);
void Bridge_Process(void);
static inline uint16_t Ring_Count(RingBuf_t *r);
static inline int      Ring_Push(RingBuf_t *r, uint8_t b);
static inline int      Ring_Pop(RingBuf_t *r, uint8_t *b);
/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */

/* ---------- Ring buffer helpers ---------- */
static inline uint16_t Ring_Count(RingBuf_t *r)
{
  return (uint16_t)((r->head - r->tail) & (RING_SIZE - 1));
}

static inline int Ring_Push(RingBuf_t *r, uint8_t b)
{
  uint16_t next = (uint16_t)((r->head + 1) & (RING_SIZE - 1));
  if (next == r->tail) return -1;
  r->buf[r->head] = b;
  r->head = next;
  return 0;
}

static inline int Ring_Pop(RingBuf_t *r, uint8_t *b)
{
  if (r->head == r->tail) return -1;
  *b = r->buf[r->tail];
  r->tail = (uint16_t)((r->tail + 1) & (RING_SIZE - 1));
  return 0;
}

/* ---------- Doi baudrate USART2 an toan ---------- */
void IR_SetBaudrate(uint32_t baud)
{
  if (baud == 0) return;
  if (applied_ir_baud == baud) return;

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

  if (HAL_UART_Init(&huart2) != HAL_OK)
  {
    Error_Handler();
  }

  applied_ir_baud = baud;
  HAL_UART_Receive_IT(&huart2, &uart2_rx_byte, 1);
}

/* ---------- Xu ly dong dieu khien tu PC ---------- */
void Process_Control_Line(const char *line)
{
  /* Ho tro:
   *   SET_BAUD:2400 / 4800 / 9600 / 19200
   *   Lenh cu: 00 01 10 11  →  2400 4800 9600 19200
   */
  uint32_t new_baud = 0;
  int      cmd_idx  = -1;

  if (strncmp(line, "SET_BAUD:", 9) == 0)
  {
    new_baud = (uint32_t)atoi(line + 9);
  }
  else if (strcmp(line, "00") == 0) { new_baud = 2400;  cmd_idx = 0; }
  else if (strcmp(line, "01") == 0) { new_baud = 4800;  cmd_idx = 1; }
  else if (strcmp(line, "10") == 0) { new_baud = 9600;  cmd_idx = 2; }
  else if (strcmp(line, "11") == 0) { new_baud = 19200; cmd_idx = 3; }
  else
  {
    return;
  }

  /* Chi chap nhan baud hop le */
  if (new_baud != 2400 && new_baud != 4800 &&
      new_baud != 9600 && new_baud != 19200)
  {
    char err[48];
    snprintf(err, sizeof(err), "BAUD_ERR:invalid\n");
    HAL_UART_Transmit(&huart3, (uint8_t *)err, (uint16_t)strlen(err), 100);
    return;
  }

  if (cmd_idx >= 0)
    auto_state_cmd = (uint8_t)cmd_idx;
  else
  {
    if      (new_baud == 2400)  auto_state_cmd = 0;
    else if (new_baud == 4800)  auto_state_cmd = 1;
    else if (new_baud == 9600)  auto_state_cmd = 2;
    else if (new_baud == 19200) auto_state_cmd = 3;
  }

  IR_SetBaudrate(new_baud);

  char ok[32];
  snprintf(ok, sizeof(ok), "BAUD_OK:%lu\n", (unsigned long)new_baud);
  HAL_UART_Transmit(&huart3, (uint8_t *)ok, (uint16_t)strlen(ok), 100);

  cnt_ctrl_cmd++;
  snprintf(pc_status_msg, sizeof(pc_status_msg), "PC: CTRL #%lu", (unsigned long)cnt_ctrl_cmd);
  snprintf(display_tx_msg, sizeof(display_tx_msg), "CTRL: SET_BAUD %lu", (unsigned long)new_baud);
  flag_new_tx = 1;
  flag_lcd_update = 1;
}

/* ---------- Bridge: day du lieu 2 chieu tu ring ---------- */
void Bridge_Process(void)
{
  uint8_t b;

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

  /* Cap nhat status LCD */
  static uint32_t last_pc = 0, last_ir = 0;
  if (cnt_pc_to_ir != last_pc || cnt_ir_to_pc != last_ir)
  {
    last_pc = cnt_pc_to_ir;
    last_ir = cnt_ir_to_pc;
    snprintf(ir_tx_status_msg, sizeof(ir_tx_status_msg), "IR TX: %lu B", (unsigned long)cnt_pc_to_ir);
    snprintf(ir_rx_status_msg, sizeof(ir_rx_status_msg), "IR RX: %lu B", (unsigned long)cnt_ir_to_pc);
    snprintf(pc_status_msg, sizeof(pc_status_msg), "PC: BRIDGE OK");

    /* Hien thi ngan gon trong 2 o content (khong in nhi phan) */
    snprintf(display_tx_msg, sizeof(display_tx_msg), "TX %lu bytes forwarded", (unsigned long)cnt_pc_to_ir);
    snprintf(display_rx_msg, sizeof(display_rx_msg), "RX %lu bytes forwarded", (unsigned long)cnt_ir_to_pc);
    flag_new_tx = 1;
    flag_new_rx = 1;
    flag_lcd_update = 1;
  }
}

/* USER CODE END 0 */

int main(void)
{
  /* USER CODE BEGIN 1 */
  /* USER CODE END 1 */

  HAL_Init();

  /* USER CODE BEGIN Init */
  /* USER CODE END Init */

  SystemClock_Config();

  /* USER CODE BEGIN SysInit */
  /* USER CODE END SysInit */

  MX_GPIO_Init();
  MX_USB_HOST_Init();
  MX_USART3_UART_Init();
  MX_SPI2_Init();
  MX_USART2_UART_Init();

  /* USER CODE BEGIN 2 */
  memset(&ring_pc_to_ir, 0, sizeof(ring_pc_to_ir));
  memset(&ring_ir_to_pc, 0, sizeof(ring_ir_to_pc));

  HAL_UART_Receive_IT(&huart3, &uart3_rx_byte, 1);
  HAL_UART_Receive_IT(&huart2, &uart2_rx_byte, 1);

  applied_ir_baud = huart2.Init.BaudRate;

  DrawMainScreen();
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */
    MX_USB_HOST_Process();

    /* USER CODE BEGIN 3 */
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
  /* USER CODE END 3 */
}

/* ========== GIAO DIEN LCD GIU NGUYEN NHU CODE CU ========== */
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

/* Hien thi toi da 4 dong, moi dong toi da 42 ky tu (font 7x10) */
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
    ILI9341_WriteString(x, (uint16_t)(y + row * 11U), line,
                        Font_7x10, ILI9341_WHITE, bg_color);
  }
}

/**
  * @brief System Clock Configuration
  */
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
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV4;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV2;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_5) != HAL_OK)
  {
    Error_Handler();
  }
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
  if (HAL_SPI_Init(&hspi2) != HAL_OK)
  {
    Error_Handler();
  }
}

/* USART2 = IR, khoi tao 19200 (AUTO mac dinh) */
static void MX_USART2_UART_Init(void)
{
  huart2.Instance = USART2;
  huart2.Init.BaudRate = 19200;          /* AUTO mac dinh 19200 */
  huart2.Init.WordLength = UART_WORDLENGTH_8B;
  huart2.Init.StopBits = UART_STOPBITS_1;
  huart2.Init.Parity = UART_PARITY_NONE;
  huart2.Init.Mode = UART_MODE_TX_RX;
  huart2.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart2.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart2) != HAL_OK)
  {
    Error_Handler();
  }
}

/* USART3 = PC, co dinh 115200 */
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
  if (HAL_UART_Init(&huart3) != HAL_OK)
  {
    Error_Handler();
  }
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

/* USER CODE BEGIN 4 */

/**
  * @brief  Doc switch, dieu khien LED + GPIO chon clock, cap nhat LCD.
  *         Baud: 2400 / 4800 / 9600 / 19200
  */
void Process_Switches_And_Display(void)
{
  dbg_sw_state = HAL_GPIO_ReadPin(GPIOD, STATE_SW_3_3V_Pin);
  dbg_baud1    = HAL_GPIO_ReadPin(GPIOD, BAUD1_3V3_Pin);
  dbg_baud2    = HAL_GPIO_ReadPin(GPIOD, BAUD2_3V3_Pin);
  dbg_baud3    = HAL_GPIO_ReadPin(GPIOD, BAUD3_3V3_Pin);
  dbg_baud4    = HAL_GPIO_ReadPin(GPIOD, BAUD4_3V3_Pin);

  uint8_t  mode = dbg_sw_state;
  uint32_t baud = 0;
  uint8_t  s1_val = 0, s0_val = 0;
  uint8_t  baud_index = 0;

  if (mode == 1)
  {
    /* ========== AUTO mode ========== */
    HAL_GPIO_WritePin(GPIOC, GPIO_PIN_14, GPIO_PIN_SET);

    switch (auto_state_cmd)
    {
      case 0: /* 2400 */
        s1_val = 0; s0_val = 0; baud = 2400;
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_9, GPIO_PIN_SET);
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_8, GPIO_PIN_RESET);
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_7, GPIO_PIN_RESET);
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_6, GPIO_PIN_RESET);
        break;
      case 1: /* 4800 */
        s1_val = 0; s0_val = 1; baud = 4800;
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_9, GPIO_PIN_RESET);
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_8, GPIO_PIN_SET);
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_7, GPIO_PIN_RESET);
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_6, GPIO_PIN_RESET);
        break;
      case 2: /* 9600 */
        s1_val = 1; s0_val = 0; baud = 9600;
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_9, GPIO_PIN_RESET);
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_8, GPIO_PIN_RESET);
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_7, GPIO_PIN_SET);
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_6, GPIO_PIN_RESET);
        break;
      case 3: /* 19200 – mac dinh AUTO */
        s1_val = 1; s0_val = 1; baud = 19200;
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_9, GPIO_PIN_RESET);
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_8, GPIO_PIN_RESET);
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_7, GPIO_PIN_RESET);
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_6, GPIO_PIN_SET);
        break;
      default: break;
    }

    HAL_GPIO_WritePin(GPIOC, CLK_S1_3_3V_Pin, s1_val ? GPIO_PIN_SET : GPIO_PIN_RESET);
    HAL_GPIO_WritePin(GPIOC, CLK_S0_3_3V_Pin, s0_val ? GPIO_PIN_SET : GPIO_PIN_RESET);
  }
  else
  {
    /* ========== MANUAL mode (DIP SW2) ========== */
    HAL_GPIO_WritePin(GPIOC, GPIO_PIN_14, GPIO_PIN_RESET);
    HAL_GPIO_WritePin(GPIOC, CLK_S1_3_3V_Pin, GPIO_PIN_RESET);
    HAL_GPIO_WritePin(GPIOC, CLK_S0_3_3V_Pin, GPIO_PIN_RESET);

    if (dbg_baud1 == GPIO_PIN_SET)
    {
      baud = 19200; baud_index = 1;
    }
    else if (dbg_baud2 == GPIO_PIN_SET)
    {
      baud = 9600;  baud_index = 2;
    }
    else if (dbg_baud3 == GPIO_PIN_SET)
    {
      baud = 4800;  baud_index = 3;
    }
    else if (dbg_baud4 == GPIO_PIN_SET)
    {
      baud = 2400;  baud_index = 4;
    }

    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_9, (baud_index == 4) ? GPIO_PIN_SET : GPIO_PIN_RESET);
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_8, (baud_index == 3) ? GPIO_PIN_SET : GPIO_PIN_RESET);
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_7, (baud_index == 2) ? GPIO_PIN_SET : GPIO_PIN_RESET);
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_6, (baud_index == 1) ? GPIO_PIN_SET : GPIO_PIN_RESET);
  }

  /* Baud / mode thay doi → re-init USART2 + cap nhat LCD */
  if (mode != current_mode || baud != current_baud)
  {
    uint32_t old_baud = current_baud;
    current_mode = mode;
    current_baud = baud;

    if (baud != 0 && baud != applied_ir_baud)
    {
      IR_SetBaudrate(baud);

      /* MANUAL + DIP doi → gui NOTIFY_BAUD */
      if (mode == 0 && old_baud != 0)
      {
        char ntf[32];
        snprintf(ntf, sizeof(ntf), "NOTIFY_BAUD:%lu\n", (unsigned long)baud);
        HAL_UART_Transmit(&huart3, (uint8_t *)ntf, (uint16_t)strlen(ntf), 100);
      }
    }

    /* O MODE */
    ILI9341_FillRectangle(4, 29, 154, 19, ILI9341_COLOR565(48, 58, 72));
    if (mode == 1)
      ILI9341_WriteString(9, 33, "MODE: AUTO", Font_7x10, ILI9341_GREEN, ILI9341_COLOR565(48, 58, 72));
    else
      ILI9341_WriteString(9, 33, "MODE: MANUAL", Font_7x10, ILI9341_YELLOW, ILI9341_COLOR565(48, 58, 72));

    /* O BAUD */
    ILI9341_FillRectangle(162, 29, 154, 19, ILI9341_COLOR565(48, 58, 72));
    if (baud != 0)
    {
      char str_baud[16];
      snprintf(str_baud, sizeof(str_baud), "BAUD: %lu", (unsigned long)baud);
      ILI9341_WriteString(169, 33, str_baud, Font_7x10, ILI9341_CYAN, ILI9341_COLOR565(48, 58, 72));
    }
    else
    {
      ILI9341_WriteString(169, 33, "BAUD: NONE", Font_7x10, ILI9341_RED, ILI9341_COLOR565(48, 58, 72));
    }
  }
}

/**
  * @brief  UART RX Complete – chi day byte vao ring (nhanh).
  */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
  if (huart->Instance == USART2)
  {
    /* IR → PC: luon forward moi byte */
    (void)Ring_Push(&ring_ir_to_pc, uart2_rx_byte);
    HAL_UART_Receive_IT(&huart2, &uart2_rx_byte, 1);
  }
  else if (huart->Instance == USART3)
  {
    uint8_t b = uart3_rx_byte;

    /* ============================================================
     * QUAN TRONG: LUON day moi byte vao ring (transparent bridge).
     * Khong bao gio chan / vut bo byte chi vi thay 'S' hay '0'.
     * Dong thoi gom vao ctrl_line de kiem tra lenh control.
     * Chi khi nao ca dong khop 100% SET_BAUD / 00 01 10 11
     * moi goi Process_Control_Line (side-effect doi baud).
     * ============================================================ */
    (void)Ring_Push(&ring_pc_to_ir, b);

    /* Gom dong control song song (khong anh huong forward) */
    if (b == '\n' || b == '\r')
    {
      if (ctrl_idx > 0)
      {
        ctrl_line[ctrl_idx] = '\0';
        /* Process_Control_Line chi thuc thi neu khop lenh;
         * neu khong khop thi bo qua (byte da duoc forward roi). */
        Process_Control_Line(ctrl_line);
      }
      ctrl_idx = 0;
      ctrl_active = 0;
    }
    else
    {
      if (ctrl_idx < CTRL_LINE_MAX - 1)
      {
        ctrl_line[ctrl_idx++] = (char)b;
      }
      else
      {
        /* Qua dai → reset, coi nhu khong phai control */
        ctrl_idx = 0;
        ctrl_active = 0;
      }
    }

    HAL_UART_Receive_IT(&huart3, &uart3_rx_byte, 1);
  }
}

/* USER CODE END 4 */

void Error_Handler(void)
{
  __disable_irq();
  while (1)
  {
  }
}

#ifdef USE_FULL_ASSERT
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
