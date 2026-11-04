# KẾ HOẠCH UI POLISH, RESPONSIVE SPLIT-SCREEN VÀ SMOOTH CONNECT FEEDBACK

> **Mã kế hoạch:** PLAN-20261104-UI-POLISH-RESPONSIVE  
> **Ngày lập:** 04/11/2026  
> **Dự án:** SpectraLink IR-OWC Workstation (LabHongNgoai)  
> **Phạm vi:** Web UI trong `src/ir_terminal/ui/web/`, builder `scripts/build_modular_web.py`, cửa sổ QtWebEngine `src/ir_terminal/ui/main_window_web.py`  
> **Trạng thái:** Kế hoạch khảo sát — **chưa sửa bất kỳ file code nào** trong stage này.  
> **Quy tắc implementation:** Tuyệt đối không viết comment/inline comment mới vào `.py`, `.js`, `.html`, `.css`.

---

## 0. TÓM TẮT ĐIỀU HÀNH

Ba vấn đề được xử lý theo thứ tự ưu tiên sau:

1. **Badge highlight có kiểm soát:** tận dụng tối đa các điểm trạng thái đã có, nâng cấp thành một hệ badge thống nhất theo ngôn ngữ Apple Modern Precision Light. Chỉ thêm **một badge mới** cho RTT ở view Telemetry; các badge còn lại là nâng cấp tại chỗ, không nhân bản trạng thái.
2. **Responsive split-screen:** nguyên nhân chính là sidebar cố định 256 px, header không có cơ chế truncate/min-width, và Tailwind breakpoint đang dựa trên viewport thay vì vùng nội dung sau sidebar. Giải pháp là sidebar rail 64 px dưới 1180 px, header compact, container query cho các grid chính, và rà soát từng view ở dải 720–960 px.
3. **Giật frame khi Connect:** nguyên nhân chính là modal thành công full-screen có `backdrop-blur-[2px]` kết hợp animation `scale + translate`, cộng với việc `setConnectedUi()` ghi DOM hàng loạt trong cùng một frame. Hai cửa sổ QtWebEngine chạy song song càng làm tranh chấp GPU/raster. Giải pháp là **success → toast nhỏ, chỉ transform/opacity**, modal chỉ dùng cho lỗi/confirm có hành động, bỏ backdrop blur, gộp DOM update trong `requestAnimationFrame`.

---

## 1. HIỆN TRẠNG VÀ ROOT CAUSES

### 1.1. Kiến trúc UI hiện tại

UI là một SPA tĩnh được ghép từ các component:

```text
src/ir_terminal/ui/web/
├── index.html                         artifact được commit, sinh bởi builder
├── components/
│   ├── chrome_header.html             header macOS-style 38 px
│   ├── sidebar_nav.html               sidebar cố định 256 px
│   ├── view_hardware.html             Hardware & Link
│   ├── view_file.html                 File Transfer
│   ├── view_chat.html                 Realtime Chat
│   ├── view_telemetry.html            Lab Diagnostics
│   ├── settings_modal.html            modal cài đặt
│   └── ui_feedback.html               toast container + modal overlay
├── js/
│   ├── ui_feedback.js                 toast/modal/button loading
│   ├── bridge_client.js               QWebChannel, connect/ping/port UI
│   ├── file_controller.js             transfer progress/ARQ UI
│   ├── chat_controller.js             chat UI
│   ├── telemetry_controller.js        KPI, chart, sniffer
│   ├── transfer_history.js            bảng lịch sử truyền file
│   └── app.js                         chuyển view
└── assets/css/local-fonts.css         font local
```

`scripts/build_modular_web.py` đọc các component, chạy `sanitize()`, rồi ghi đè `index.html`. Vì vậy **không được sửa trực tiếp `index.html`**; mọi thay đổi markup phải nằm trong `components/` hoặc template của builder.

### 1.2. Hiện trạng badge và điểm nhiễu trạng thái

| Khu vực | Element hiện tại | Hành vi hiện tại | Vấn đề |
|---|---|---|---|
| Global header | `#ribbon-dot`, `#ribbon-text` trong `chrome_header.html:15-18` | Text + dot đổi màu thủ công | Chuỗi rất dài, không truncate; màu được gán bằng `className` chuỗi cứng trong `bridge_client.js:124-126` |
| Hardware header | `#hw-header-link-badge` trong `view_hardware.html:5-7` | Text `DISCONNECTED` + dot | Không có style badge thống nhất; lặp lại trạng thái với ribbon |
| TX/RX port | `#hw-tx-dot`, `#hw-rx-dot` trong `view_hardware.html:76-85` | Chỉ có dot, flash 120 ms bằng `className` + `ring-2` | Không có nhãn trạng thái; `ring-2` tạo paint không cần thiết; flash dùng class string |
| RTT | `#hw-rtt-state` trong `view_hardware.html:113-115` | Text `IDLE / READY / MEASURED` | Không có semantic tone; không phân biệt RTT tốt/xấu |
| Transfer/ARQ | `#file-active-status` trong `view_file.html:45-51` | Dot + text, cập nhật bằng `innerHTML` trong `file_controller.js:16-28` | Chỉ có `IDLE / STREAMING / COMPLETED / FAILED`; chưa dùng signal `transferStateChanged` đã có ở backend |
| Chat link | `#chat-link-state` trong `view_chat.html:16` | Text `LINK: STANDBY / CONNECTED` | Text-only, không có tone/visual hierarchy |
| Telemetry RTT | `#kpi-rtt-value` trong `view_telemetry.html:66` | Chỉ hiện số | Không có badge chất lượng RTT |
| Transfer history | `transfer_history.js:84-105` | Dot + text từng row | Nếu nâng tất cả thành badge sẽ gây spam; **giữ nguyên dạng dot + text nhẹ** |

**Kết luận:** không cần thêm nhiều badge mới. Chỉ cần chuẩn hóa các trạng thái đã có thành một badge component và thêm đúng một badge RTT ở Telemetry. Các trạng thái trùng lặp (ribbon, hardware header, chat) là contextual, nhưng phải dùng cùng một token/tone để không tạo cảm giác “badge rác”.

### 1.3. Hiện trạng responsive và các điểm vỡ layout

#### 1.3.1. Kích thước cửa sổ và split-screen thực tế

`src/ir_terminal/ui/main_window_web.py:122-123`:

```python
self.resize(1380, 880)
self.setMinimumSize(1100, 700)
```

`src/ir_terminal/ui/main_window_web.py:187-203` đặt cửa sổ vào nửa trái/phải màn hình:

```python
half_w = max(720, geo.width() // 2)
self.setMinimumSize(min(self.minimumWidth(), half_w), 700)
self.resize(half_w, geo.height())
```

Hệ quả:

| Màn hình | Split width thực tế | Sidebar hiện tại | Vùng nội dung còn lại | Rủi ro |
|---|---:|---:|---:|---|
| 1080p (1920) | 960 px | 256 px | 704 px | Header tràn; grid `sm` vẫn 2 cột dù nội dung hẹp |
| 2K (2560) | 1280 px | 256 px | 1024 px | `lg` vừa bật; card 2 cột sát ngưỡng |
| 4K (3840) | 1920 px | 256 px | 1664 px | Tốt, nhưng header vẫn có chuỗi dài |
| Manual split | 800 px | 256 px | 544 px | Sidebar chiếm 32%; nhiều control bị ép |
| Manual split | 720 px | 256 px | 464 px | Vỡ layout nghiêm trọng |

Điểm mâu thuẫn: `setMinimumSize(1100, 700)` không cho cửa sổ nhỏ hơn 1100 px, nhưng `apply_window_position()` lại hạ min xuống `half_w` (960 px ở 1080p). Vì vậy app có thể ở trạng thái 960 px trong khi CSS vẫn được thiết kế cho desktop 1100+.

#### 1.3.2. Audit theo file/component

| File / vùng | Dòng tham chiếu | Hiện trạng | Rủi ro ở 800–960 px |
|---|---|---|---|
| `chrome_header.html` | 1, 15-23, 26-34 | `justify-between`, 3 nhóm flex; center title dài; ribbon text dài; không `min-w-0`, không `truncate` | Header tràn, đè lên nút refresh/settings, ribbon đẩy center title |
| `sidebar_nav.html` | 1, 8, 14-33, 38-59 | `w-64` cố định + `flex-shrink-0`; label đầy đủ; footer widget đầy đủ | Chiếm 256 px; ở 800 px chỉ còn 544 px nội dung |
| `view_hardware.html` | 1, 21, 33, 57-63, 64-86, 111, 147, 155, 168 | Header `md:flex-row`; grid `sm:grid-cols-2`; RTT label + state `justify-between` không wrap; spec grid 4 cột ở `lg` | 2 cột select quá hẹp; label dài wrap; RTT state bị đẩy; spec card chật |
| `view_file.html` | 1, 16, 39-52, 68-90, 104-119, 121, 147 | Header `md:flex-row`; stats `grid-cols-3` cố định; search `w-48`; table `overflow-x-auto` nhưng không có `min-width` | Stats 3 cột quá hẹp; header Recent Transfers tràn; cột bảng bị bóp |
| `view_chat.html` | 1, 11, 16, 19, 28-45 | Header đã `flex-wrap`; bubble `max-w-xl`; input row không `min-w-0` | Bubble/info line có thể tràn; nút Transmit bị ép |
| `view_telemetry.html` | 1, 11, 26, 92, 105, 127, 139-160, 162, 188-195 | Header `md:flex-row`; right group `shrink-0`; filter bar nhiều nút; footer 3 span; table không min-width | Right controls tràn; filter bar xuống dòng xấu; footer overflow |
| `ui_feedback.html` | 10-13 | Modal `max-w-sm`, backdrop `backdrop-blur-[2px]`; card không max-height | Modal có thể cao quá viewport thấp; blur gây jank |
| `settings_modal.html` | 4-7 | Modal `max-w-md`, backdrop blur; card không max-height | Tương tự modal feedback |
| `telemetry_controller.js` | 36-37, 124-145 | `window.addEventListener('resize', drawCanvasCharts)` không debounce; update KPI mỗi signal | Resize/telemetry 100 Hz gây redraw canvas liên tục |
| `bridge_client.js` | 121-161, 163-202 | Ghi DOM hàng loạt bằng `innerText`/`className` chuỗi | Layout shift + style recalc mỗi lần connect/disconnect |
| `main_window_web.py` | 122-123, 199-201 | Min size 1100×700; split half width có thể 960 | Không khớp với CSS desktop |

#### 1.3.3. Root cause responsive tóm tắt

1. **Sidebar cố định 256 px** trong khi cửa sổ split có thể chỉ 800–960 px.
2. **Breakpoint Tailwind tính theo viewport**, không tính theo vùng nội dung sau sidebar. Ví dụ ở viewport 800 px, `sm:grid-cols-2` (640 px) vẫn bật dù content area chỉ còn 544 px.
3. **Header không có `min-w-0`/truncate**, nên chuỗi trạng thái dài làm hỏng flex layout.
4. **Nhiều control có width cố định** (`w-48`, `w-44`) và nhiều nhóm `shrink-0` không cho phép wrap.
5. **Bảng không có `min-width`**, nên `overflow-x-auto` không phát huy tác dụng đúng; cột bị nén thay vì cuộn.
6. **Canvas chart không có ResizeObserver**, dễ vẽ sai kích thước khi view ẩn hoặc khi split-screen thay đổi.

### 1.4. Root cause giật frame khi Connect COM

#### 1.4.1. Chuỗi sự kiện hiện tại

```text
Click Connect
  → UIFeedback.setButtonLoading(btn, true, 'Connecting...')
  → bridge.connectDualPorts(tx, rx, baud)
  → backend mở port
  → portConnected(tx, rx, baud)
      → clearConnectLoading()
      → setConnectedUi(tx, rx, baud)
          → ghi ribbon-text, ribbon-dot, footer-health-dot/text,
            footer-port-label, footer-baud-label, hw-header-link-badge,
            hw-rtt-state, chat-link-state, btn-connect
      → UIFeedback.showModal(success)
          → đổi overlay hidden/flex
          → cập nhật icon/title/message/details
          → xóa và tạo lại action buttons
          → focus button đầu tiên
          → backdrop blur 2 px + card scale/translate animation
```

#### 1.4.2. Nguyên nhân chính

1. **`backdrop-blur-[2px]` trên overlay full-screen** (`ui_feedback.html:11`, `settings_modal.html:5`).  
   Chromium phải đọc toàn bộ backdrop vào một texture ngoài, làm mờ rồi composite lại. Với cửa sổ 960×1080 hoặc 1280×1080, đây là một pass full-viewport mỗi frame. Trong QtWebEngine, nếu GPU compositing không khả dụng hoặc driver yếu, blur có thể fallback sang software raster.

2. **Animation `modal-pop-in` dùng `transform: scale(0.96) translateY(6px)`** (`scripts/build_modular_web.py:370-379`).  
   Transform trên card là compositor-friendly, nhưng khi card nằm trên backdrop filter, mỗi frame transform buộc backdrop phải được blur lại. Đây là tổ hợp tệ nhất: full-screen filter + animation.

3. **Success modal là feedback dư thừa.**  
   Sau connect, ribbon, footer, hardware badge, chat badge đều đã đổi trạng thái. Modal chỉ lặp lại thông tin nhưng chiếm toàn màn hình và tạo lớp compositing mới.

4. **DOM reflow hàng loạt trong cùng một task.**  
   `setConnectedUi()` thay đổi `innerText` và `className` của hơn 8 element; nút Connect đổi text `Connect → Disconnect` làm thay đổi kích thước nội tại; sau đó `showModal()` tiếp tục thay đổi overlay, tạo button, focus. Tất cả xảy ra trước khi browser kịp paint, gây style/layout invalidation lớn.

5. **Header text dài không truncate.**  
   Ribbon đổi từ “Waiting for link” sang “STM32 Optical Bridge • TX: … • RX: … (9600 bps)”. Ở split width, chuỗi này làm header reflow/overflow, tạo layout shift đúng lúc modal mở.

6. **Hai cửa sổ QtWebEngine chạy song song.**  
   Mỗi cửa sổ có renderer/GPU compositing riêng. Khi cả hai cùng connect trong dual mock, cả hai cùng mở full-screen blur + animation, tranh chấp GPU/raster thread. Hiện tượng “giật frame” càng rõ trên máy dùng GPU tích hợp hoặc chạy remote/VM.

7. **High-frequency updates ngay sau connect.**  
   `rawTxData`/`rawRxData` flash dot bằng `className` có `ring-2` (`bridge_client.js:272-290`); telemetry cập nhật KPI/canvas; transfer progress cập nhật width/text. Những update này không nguy hiểm riêng lẻ nhưng cộng dồn với modal full-screen sẽ làm frame drop rõ hơn.

#### 1.4.3. Nguyên nhân phụ

- `setButtonLoading()` thay toàn bộ `innerHTML` của nút (`ui_feedback.js:158-174`), gây reflow ở thời điểm connect bắt đầu và kết thúc.
- `showModal()` gọi `firstBtn.focus()` (`ui_feedback.js:134-135`), có thể gây scroll/focus ring và thêm style recalc.
- `telemetry_controller.js` redraw canvas trên mọi `resize` event (`telemetry_controller.js:36-37`), không debounce.
- `showToast()` tạo DOM mới và animation `translateX`; bản thân nó nhẹ, nhưng nếu dùng `box-shadow`/filter lớn sẽ tăng chi phí. Hiện tại toast không có blur, nên đây là hướng thay thế đúng.

#### 1.4.4. Kết luận root cause

**Primary:** success modal full-screen + `backdrop-filter: blur` + `transform scale` animation + DOM writes dồn dập.  
**Secondary:** header layout shift do text dài, high-frequency dot/telemetry updates, focus/button innerHTML changes.  
**Không phải nguyên nhân:** QWebChannel hay serial backend; profile isolation giữa 2 instance đã đúng (`main_window_web.py:125-145`).

---

## 2. THIẾT KẾ KIẾN TRÚC GIẢI PHÁP

### 2.1. Nguyên tắc thiết kế Apple Modern Precision Light

- **Surface:** `#FFFFFF` trên nền `#F5F5F7`; border mảnh `rgba(0,0,0,0.06–0.08)`; radius 8–14 px.
- **Typography:** Inter cho body, Plus Jakarta Sans cho heading, `font-mono-data` 10–12 px cho nhãn kỹ thuật.
- **Màu semantic:** success `#006C49`, info `#004AC6`, warning `#784B00`, error `#BA1A1A`, neutral `#737686`.
- **Badge:** pill nhỏ, nền tint 6–8%, border tint 18%, dot 6 px, không gradient, không glow, không pulse.
- **Motion:** 120–180 ms ease-out; chỉ animate `opacity` và `transform`; tôn trọng `prefers-reduced-motion`.
- **Không spam:** mỗi vùng trạng thái chỉ một badge; không lặp lại “880nm NIR”, “BER”, “Direct LOS” dưới dạng badge.

### 2.2. Kiến trúc Badge Highlight

#### 2.2.1. Badge component dùng chung

Tạo `assets/css/app.css` chứa class `.ui-badge` và các tone. Tạo `js/ui_badges.js` cung cấp API:

```js
window.UIBadge = {
  set: function (target, opts) {},
  setActive: function (target, active) {},
  toneForRtt: function (rtt) {}
};
```

Markup chuẩn:

```html
<span id="hw-link-badge" class="ui-badge" data-tone="neutral" data-size="compact">
  <span class="ui-badge__dot"></span>
  <span class="ui-badge__label">DISCONNECTED</span>
</span>
```

Quy ước:
- `data-tone`: `neutral | info | success | warning | error`.
- `data-size`: `compact` (20 px) hoặc `micro` (18 px).
- `data-active`: chỉ dùng cho TX/RX activity, tự reset sau 120 ms.
- Không thay `innerHTML` của badge; chỉ cập nhật `.ui-badge__label`, `dataset.tone`, `dataset.active`.

#### 2.2.2. Ma trận badge tối giản

| # | Vùng | Element ID | Nguồn dữ liệu | Trạng thái | Ghi chú |
|---|---|---|---|---|---|
| 1 | Global header ribbon | `#ribbon-badge` (bao quanh `#ribbon-dot`, `#ribbon-text`) | `portConnected`, `portDisconnected`, `errorOccurred` | `neutral / info / success / error` | Giữ nguyên vị trí, chỉ thêm tone + truncate |
| 2 | Hardware link header | `#hw-header-link-badge` | `portConnected`, `portDisconnected` | `DISCONNECTED / CONNECTING / LINK CONNECTED / ERROR` | Nâng cấp text-only thành badge |
| 3 | TX port | `#hw-tx-status` | `portConnected`, `rawTxData` | `TX IDLE / TX OPEN / TX ACTIVE` | Giữ `#hw-tx-dot` bên trong; thêm label |
| 4 | RX port | `#hw-rx-status` | `portConnected`, `rawRxData` | `RX IDLE / RX OPEN / RX ACTIVE` | Giữ `#hw-rx-dot` bên trong; thêm label |
| 5 | RTT hardware | `#hw-rtt-state` | `portConnected`, `pingResult`, `healthResult` | `IDLE / READY / MEASURED` + tone theo RTT | Nâng cấp text-only thành badge |
| 6 | Transfer/ARQ | `#file-active-status` | `transferStateChanged`, `transferCompleted`, `transferFailed` | `ARQ IDLE / SYNC / TX / RX / FINALIZE / COMPLETE / FAILED / ABORTED` | Dùng lại element hiện có; không thêm badge thứ hai |
| 7 | Chat link | `#chat-link-state` | `portConnected`, `portDisconnected` | `LINK STANDBY / LINK CONNECTED` | Nâng cấp text-only thành badge |
| 8 | Telemetry RTT | `#kpi-rtt-badge` | `telemetryUpdated` | `IDLE / GOOD / FAIR / SLOW` | **Badge mới duy nhất**; thay icon timer hiện tại |

Không thêm badge cho:
- Từng row trong Recent Transfers (giữ dot + text như hiện tại).
- Các thông số tĩnh 880 nm / MCP2122 / 8-N-1.
- Mỗi lần TX/RX activity (chỉ dot trong badge đổi trạng thái, không đổi text liên tục).

#### 2.2.3. Logic tone

| Tone | Dùng khi |
|---|---|
| `neutral` | IDLE, DISCONNECTED, STANDBY, chưa có dữ liệu |
| `info` | CONNECTING, SYNC, TX/RX đang hoạt động, đang đo |
| `success` | LINK CONNECTED, TX/RX OPEN, ARQ COMPLETE, RTT GOOD |
| `warning` | RTT FAIR, ARQ FINALIZE, retry (nếu sau này có signal retry) |
| `error` | connect failed, ARQ FAILED, RTT SLOW, CRC error |

RTT thresholds đề xuất (có thể chỉnh sau khi đo thực tế):
- `< 20 ms`: `GOOD` — success.
- `20–60 ms`: `FAIR` — warning.
- `>= 60 ms`: `SLOW` — error.
- `0 / null`: `IDLE` — neutral.

#### 2.2.4. Accessibility và motion

- Badge trạng thái động có `role="status"` và `aria-live="polite"` **chỉ khi** trạng thái thay đổi quan trọng (link, ARQ). Không đặt `aria-live` cho TX/RX activity để tránh screen reader spam.
- Mỗi badge có `title` mô tả đầy đủ nếu label bị rút gọn.
- Không dùng `animate-ping`, `status-pulse`, `animate-bounce`.
- Transition chỉ `color`, `background-color`, `border-color`, `opacity` 120 ms.
- Với `prefers-reduced-motion: reduce`, tắt toàn bộ animation toast/modal.

### 2.3. Kiến trúc Responsive Split-screen

#### 2.3.1. Layout modes

| Mode | Điều kiện | Sidebar | Header | Grid chính |
|---|---|---|---|---|
| Wide | `>= 1180 px` | 256 px đầy đủ | Đầy đủ ribbon + center title | 2 cột khi container >= 860 px |
| Split | `900–1179 px` | Rail 64 px, icon + tooltip | Ẩn center title; ribbon truncate | 1–2 cột theo container |
| Compact | `720–899 px` | Rail 64 px | Compact, ribbon ngắn, action giữ nguyên | 1 cột; control wrap |
| Narrow | `< 720 px` | Không hỗ trợ chính thức; min window 720 px | — | — |

Min window đề xuất: **720 × 600 px**. `apply_window_position()` dùng `half_w = max(720, geo.width() // 2)` để 1080p cho 960 px, 2K cho 1280 px, 4K cho 1920 px, và vẫn cho phép split 720 px trên màn hình nhỏ.

#### 2.3.2. Container query thay vì chỉ viewport breakpoint

Đặt `container-type: inline-size` trên `.view-panel`, sau đó dùng các class ổn định:

```css
.view-panel {
  container-type: inline-size;
  min-width: 0;
}

.split-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 20px;
}

@container (min-width: 860px) {
  .split-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.kpi-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
}

@container (min-width: 560px) {
  .kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@container (min-width: 980px) {
  .kpi-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
```

Fallback nếu QtWebEngine của môi trường build không hỗ trợ container query: dùng media query tương đương (`@media (max-width: 1179px)`, `@media (max-width: 899px)`) và kiểm tra trực quan ở 720/800/960 px.

#### 2.3.3. Header compact

- Thêm `min-w-0` cho header và nhóm trái.
- `#window-center-title` ẩn dưới 1280 px; ở 1280+ cho phép truncate.
- `#ribbon-text` dùng `truncate` + `title` attribute chứa full text.
- Nhóm phải (`chrome-instance-label`, refresh, settings) `shrink-0`.
- Ở `< 900 px`, giảm padding ngang và giới hạn ribbon `42vw`.

#### 2.3.4. Sidebar rail

- Thêm `id="app-sidebar"` và các class semantic:
  - `.sidebar-brand-text`
  - `.sidebar-section-title`
  - `.sidebar-label`
  - `.sidebar-footer-detail`
  - `.sidebar-action-label`
- Dưới 1180 px: sidebar width 64 px; ẩn các label; nav item center; brand icon center; footer chỉ còn dot trạng thái; nút Calibrate/Ping thành icon button 32×32.
- Thêm `title` cho nav item và footer để vẫn có tooltip khi ở rail.
- Không cần JS toggle vì min width 720 px; rail là trạng thái ổn định.

#### 2.3.5. Responsive theo từng view

**Hardware**
- Thay grid ngoài `lg:grid-cols-2` bằng `.split-grid`.
- Port select dùng `.port-grid`: 1 cột mặc định, 2 cột khi container >= 520 px.
- Physical layer spec dùng `.spec-grid`: 1 → 2 → 4 cột theo container.
- RTT label + state cho phép wrap (`flex-wrap gap-1`), state `shrink-0`.
- Các card thêm `min-w-0`; text dài `break-words`.

**File**
- Header `flex-wrap`, right specs `min-w-0`.
- `.file-stats` 3 cột ở container >= 520 px, 1 cột dưới ngưỡng.
- Search input `w-full sm:w-48`.
- Table `.data-table { min-width: 720px; }` trong wrapper `overflow-x-auto`.
- Recent Transfers header `flex-wrap`; filter input full width khi hẹp.
- Progress bar và stats vẫn hiển thị đầy đủ; không ẩn control.

**Chat**
- Bubble dùng `max-width: min(36rem, 100%)`.
- Input row `min-w-0`; nút Transmit `shrink-0`.
- Info line giữ `truncate` và có `title`.

**Telemetry**
- Header `flex-wrap`; nhóm Export/Reset `w-full md:w-auto` và `flex-wrap`.
- KPI dùng `.kpi-grid`.
- Chart grid dùng `.split-grid`.
- Sniffer toolbar `flex-wrap`; search `w-full sm:w-44`; filter bar `flex-wrap`.
- Table `.data-table` min-width 760 px.
- Footer `flex-wrap gap-2`.
- Chart redraw dùng `ResizeObserver` + debounce 120 ms; chỉ vẽ khi view visible.

**Modal / toast**
- Modal card `max-h-[calc(100vh-2rem)] overflow-y-auto`.
- Modal width `max-w-[calc(100vw-2rem)]`; settings `max-w-md`.
- Toast `w-[340px] max-w-[calc(100vw-1rem)]`; ở compact đặt `right-2 bottom-2`.
- Không dùng backdrop blur.

### 2.4. Kiến trúc Smooth Connect Feedback

#### 2.4.1. Chính sách feedback

| Sự kiện | Feedback | Lý do |
|---|---|---|
| Bắt đầu Connect | Button loading + spinner | Người dùng biết đang xử lý |
| `portConnected` thành công | **Toast success 2.8 s** + cập nhật badge | Không che màn hình, không blur, không focus steal |
| `errorOccurred` khi đang connect | Modal error có action Close/Retry | Cần hành động |
| Connect timeout 6 s | Modal error có action Close/Retry | Cần hành động |
| Disconnect | Toast info | Nhẹ |
| Ping success | Toast success ngắn | Nhẹ |
| Transfer complete | Toast success (không modal) | Tránh modal full-screen khi đang theo dõi progress |
| Transfer failed | Modal error có Retry | Cần hành động |
| Telemetry export success | Toast success | Nhẹ |
| Confirm abort/reset | Modal confirm | Cần quyết định |

#### 2.4.2. Luồng connect mới

```text
Click Connect
  → setButtonLoading(true)
  → bridge.connectDualPorts(...)

portConnected
  → clearConnectLoading()
  → requestAnimationFrame(() => {
        setConnectedUi(tx, rx, baud)   // batch, không đọc layout
        showToast(success, 2800ms)     // không modal
    })

errorOccurred / timeout
  → clearConnectLoading()
  → showModal(error, no blur, opacity-only, actions Close/Retry)
```

#### 2.4.3. Tối ưu DOM và layout

- Thêm helper `setText(id, text)` chỉ ghi khi `textContent` khác giá trị cũ.
- Thay `className = '...'` bằng `dataset.state` + CSS, hoặc `classList.toggle`.
- Không đọc `getBoundingClientRect()` trong cùng frame với DOM writes.
- `setConnectedUi()` chạy trong một `requestAnimationFrame` duy nhất.
- Nút Connect dùng `data-state="connected|disconnected"` và CSS để đổi màu; label có thể đổi nhưng giữ `min-width` ổn định.
- TX/RX activity dùng `UIBadge.setActive()` + `setTimeout(120)`; không đổi `className` chuỗi dài, không dùng `ring-2`.
- Telemetry KPI và transfer progress throttle bằng `requestAnimationFrame`; không update DOM ở mỗi signal 100 Hz.
- Canvas chart dùng `ResizeObserver`, debounce 120 ms, và redraw khi `view-changed`.

#### 2.4.4. Tối ưu CSS/GPU

- **Bỏ hoàn toàn** `backdrop-blur-[2px]` khỏi `ui_feedback.html` và `settings_modal.html`; thay bằng scrim `bg-on-surface/20`.
- Bỏ `modal-pop-in` dùng `scale/translate`; thay bằng `modal-fade-in` chỉ `opacity` 120 ms.
- Toast giữ `transform: translateX` + `opacity`; thêm `contain: layout paint` và `will-change: transform, opacity` trong thời gian animation.
- Không animate `box-shadow`, `filter`, `backdrop-filter`.
- Thêm `@media (prefers-reduced-motion: reduce)` để tắt animation.
- Không thêm `QTWEBENGINE_CHROMIUM_FLAGS` mặc định. Chỉ sau khi profiling xác nhận software raster mới cân nhắc flag GPU; ưu tiên sửa CSS trước.
- Giữ nguyên profile isolation cho dual instance; không dùng chung cache/profile.

---

## 3. DANH SÁCH FILE CẦN CHỈNH SỬA VÀ MÃ NGUỒN DỰ KIẾN

### 3.1. Bảng file

| File | Hành động | Mục đích |
|---|---|---|
| `src/ir_terminal/ui/web/assets/css/app.css` | **Tạo mới** | Badge tokens, responsive layout, container query, motion, modal/toast constraints |
| `src/ir_terminal/ui/web/js/ui_badges.js` | **Tạo mới** | API badge dùng chung, tone RTT, active state |
| `src/ir_terminal/ui/web/components/chrome_header.html` | Sửa | Header compact, truncate ribbon, ID `ribbon-badge`, class semantic |
| `src/ir_terminal/ui/web/components/sidebar_nav.html` | Sửa | Sidebar rail, class label/footer, `id="app-sidebar"` |
| `src/ir_terminal/ui/web/components/view_hardware.html` | Sửa | Badge TX/RX/link/RTT, grid container-aware, wrap |
| `src/ir_terminal/ui/web/components/view_file.html` | Sửa | Badge ARQ, stats responsive, search/table wrap |
| `src/ir_terminal/ui/web/components/view_chat.html` | Sửa | Badge link, bubble/input responsive |
| `src/ir_terminal/ui/web/components/view_telemetry.html` | Sửa | Badge RTT KPI, toolbar/footer wrap, table min-width |
| `src/ir_terminal/ui/web/components/ui_feedback.html` | Sửa | Bỏ backdrop blur, modal max-height, class scrim |
| `src/ir_terminal/ui/web/components/settings_modal.html` | Sửa | Bỏ backdrop blur, modal max-height |
| `src/ir_terminal/ui/web/js/ui_feedback.js` | Sửa | Toast/modal nhẹ, duration, không focus steal khi success |
| `src/ir_terminal/ui/web/js/bridge_client.js` | Sửa | Success toast, badge updates, batch DOM, TX/RX active state |
| `src/ir_terminal/ui/web/js/file_controller.js` | Sửa | Dùng `transferStateChanged`, ARQ badge, throttle progress |
| `src/ir_terminal/ui/web/js/telemetry_controller.js` | Sửa | RTT badge, rAF throttle, ResizeObserver, view-changed redraw |
| `src/ir_terminal/ui/web/js/chat_controller.js` | Sửa nhẹ | Dùng badge helper cho link state nếu cần |
| `src/ir_terminal/ui/web/js/app.js` | Sửa nhẹ | Phát `view-changed` event sau `switchView` |
| `src/ir_terminal/ui/web/js/transfer_history.js` | Không bắt buộc | Giữ dot + text; chỉ dùng token nếu muốn đồng bộ |
| `scripts/build_modular_web.py` | Sửa | Link `app.css`, script `ui_badges.js`, cập nhật fallback template + shell classes + motion CSS |
| `src/ir_terminal/ui/main_window_web.py` | Sửa | Min size 720×600, split width tối thiểu 720 |
| `src/ir_terminal/ui/web/index.html` | **Sinh lại** | Artifact từ builder; không sửa tay |
| `scripts/capture_all_screens.py` | Sửa | Dùng baud 9600; thêm capture 960/1280 px nếu cần |
| `tests/unit/test_web_ui_responsive_contract.py` | **Tạo mới** | Contract test badge/responsive/feedback |
| `tests/unit/test_web_ui_dual_com_contract.py` | Sửa | Bổ sung forbidden markers mới và asset wiring |
| `tests/unit/test_app_and_ui.py` | Không bắt buộc | Có thể thêm test min-size nếu muốn |

### 3.2. `assets/css/app.css` — mã nguồn dự kiến

```css
:root {
  --ui-bg: #f5f5f7;
  --ui-surface: #ffffff;
  --ui-border: rgba(0, 0, 0, 0.08);
  --ui-text: #0b1c30;
  --ui-muted: #737686;
  --ui-success: #006c49;
  --ui-info: #004ac6;
  --ui-warning: #784b00;
  --ui-error: #ba1a1a;
}

.ui-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 20px;
  padding: 0 8px;
  border: 1px solid var(--ui-border);
  border-radius: 9999px;
  background: var(--ui-surface);
  color: var(--ui-muted);
  font-family: Inter, sans-serif;
  font-size: 10px;
  line-height: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  white-space: nowrap;
}

.ui-badge[data-size="micro"] {
  height: 18px;
  padding: 0 6px;
  font-size: 9px;
}

.ui-badge__dot {
  width: 6px;
  height: 6px;
  border-radius: 9999px;
  background: currentColor;
  flex: none;
}

.ui-badge[data-tone="success"] {
  color: var(--ui-success);
  background: rgba(0, 108, 73, 0.06);
  border-color: rgba(0, 108, 73, 0.18);
}

.ui-badge[data-tone="info"] {
  color: var(--ui-info);
  background: rgba(0, 74, 198, 0.06);
  border-color: rgba(0, 74, 198, 0.18);
}

.ui-badge[data-tone="warning"] {
  color: var(--ui-warning);
  background: rgba(120, 75, 0, 0.06);
  border-color: rgba(120, 75, 0, 0.18);
}

.ui-badge[data-tone="error"] {
  color: var(--ui-error);
  background: rgba(186, 26, 26, 0.06);
  border-color: rgba(186, 26, 26, 0.18);
}

.ui-badge[data-active="true"] {
  background: rgba(0, 74, 198, 0.10);
}

#ribbon-badge[data-tone="success"] {
  color: var(--ui-success);
}

#ribbon-badge[data-tone="success"] #ribbon-dot {
  background: var(--ui-success);
}

.app-header {
  min-width: 0;
}

.app-header__left {
  min-width: 0;
}

.app-header__center {
  min-width: 0;
  overflow: hidden;
}

.app-header__title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.app-header__right {
  flex: none;
}

.app-ribbon {
  min-width: 0;
  max-width: min(34vw, 520px);
}

.app-ribbon__text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

#app-sidebar {
  width: 256px;
}

@media (max-width: 1279px) {
  .app-header__center {
    display: none;
  }
}

@media (max-width: 1180px) {
  #app-sidebar {
    width: 64px;
    padding: 8px;
  }

  #app-sidebar .sidebar-label,
  #app-sidebar .sidebar-section-title,
  #app-sidebar .sidebar-brand-text,
  #app-sidebar .sidebar-footer-detail,
  #app-sidebar .sidebar-action-label {
    display: none;
  }

  #app-sidebar .nav-item {
    justify-content: center;
    padding-left: 0;
    padding-right: 0;
  }

  #app-sidebar .sidebar-brand {
    justify-content: center;
  }

  #app-sidebar .sidebar-footer {
    padding: 8px;
  }

  #app-sidebar #btn-calibrate {
    width: 32px;
    height: 32px;
    padding: 0;
    margin: 0 auto;
  }
}

@media (max-width: 900px) {
  .app-header {
    padding-left: 8px;
    padding-right: 8px;
  }

  .app-ribbon {
    max-width: 42vw;
  }

  .app-ribbon__prefix {
    display: none;
  }
}

.view-panel {
  container-type: inline-size;
  min-width: 0;
}

.split-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 20px;
}

@container (min-width: 860px) {
  .split-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.kpi-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
}

@container (min-width: 560px) {
  .kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@container (min-width: 980px) {
  .kpi-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

.port-grid,
.spec-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
}

@container (min-width: 520px) {
  .port-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@container (min-width: 560px) {
  .spec-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@container (min-width: 980px) {
  .spec-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

.file-stats {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 8px;
}

@container (min-width: 520px) {
  .file-stats {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

.data-table {
  min-width: 720px;
}

.toast-item {
  contain: layout paint;
  will-change: transform, opacity;
}

@keyframes modal-fade-in {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

#modal-overlay.flex #modal-card,
#settings-overlay.flex #settings-card {
  animation: modal-fade-in 120ms ease-out;
}

@media (prefers-reduced-motion: reduce) {
  .toast-item,
  .toast-leaving,
  #modal-overlay.flex #modal-card,
  #settings-overlay.flex #settings-card {
    animation: none;
    transition: none;
  }
}
```

### 3.3. `js/ui_badges.js` — mã nguồn dự kiến

```js
(function () {
  'use strict';

  function resolve(target) {
    return typeof target === 'string' ? document.getElementById(target) : target;
  }

  function set(target, opts) {
    var el = resolve(target);
    if (!el) return;
    opts = opts || {};
    if (opts.tone) el.dataset.tone = opts.tone;
    if (opts.size) el.dataset.size = opts.size;
    if (opts.label !== undefined) {
      var label = el.querySelector('.ui-badge__label');
      if (label) label.textContent = opts.label;
      else el.textContent = opts.label;
    }
    if (opts.dot !== undefined) {
      var dot = el.querySelector('.ui-badge__dot');
      if (dot) dot.hidden = !opts.dot;
    }
    if (opts.title !== undefined) el.title = opts.title;
  }

  function setActive(target, active) {
    var el = resolve(target);
    if (!el) return;
    el.dataset.active = active ? 'true' : 'false';
  }

  function toneForRtt(rtt) {
    if (!rtt || rtt <= 0) return 'neutral';
    if (rtt < 20) return 'success';
    if (rtt < 60) return 'warning';
    return 'error';
  }

  window.UIBadge = {
    set: set,
    setActive: setActive,
    toneForRtt: toneForRtt
  };
})();
```

### 3.4. Component snippets

#### 3.4.1. `chrome_header.html`

```html
<header class="app-header h-[38px] bg-surface-container-lowest border-b border-black/[0.08] px-space-md flex items-center justify-between flex-shrink-0 select-none min-w-0">
  <div class="app-header__left flex items-center gap-space-md min-w-0">
    <div class="flex items-center gap-2 pr-2 shrink-0">
      <button aria-label="Close" class="traffic-dot w-3 h-3 rounded-full bg-[#FF5F56] border border-[#E0443E] flex items-center justify-center group focus:outline-none cursor-pointer">
        <span class="text-[8px] text-black font-bold opacity-0 transition-opacity">×</span>
      </button>
      <button aria-label="Minimize" class="traffic-dot w-3 h-3 rounded-full bg-[#FFBD2E] border border-[#DEA123] flex items-center justify-center group focus:outline-none cursor-pointer">
        <span class="text-[8px] text-black font-bold opacity-0 transition-opacity">-</span>
      </button>
      <button aria-label="Zoom" class="traffic-dot w-3 h-3 rounded-full bg-[#27C93F] border border-[#1AAB29] flex items-center justify-center group focus:outline-none cursor-pointer">
        <span class="text-[7px] text-black font-bold opacity-0 transition-opacity">+</span>
      </button>
    </div>
    <div class="h-4 w-px bg-outline-variant/40 shrink-0"></div>
    <div id="ribbon-badge" class="app-ribbon flex items-center gap-space-xs bg-surface-container-low px-2 py-0.5 rounded-md border border-outline-variant/50 min-w-0" data-tone="neutral">
      <span id="ribbon-dot" class="ui-badge__dot"></span>
      <span id="ribbon-text" class="app-ribbon__text font-mono-data text-mono-data text-on-surface font-medium">STM32 Optical Bridge • Disconnected</span>
    </div>
  </div>

  <div class="app-header__center flex items-center gap-2 min-w-0">
    <span class="material-symbols-outlined text-primary text-[16px] shrink-0">terminal</span>
    <span id="window-center-title" class="app-header__title font-title-sm text-title-sm text-on-surface font-semibold">SpectraLink IR-OWC — Hardware &amp; Optical Link Setup</span>
  </div>

  <div class="app-header__right flex items-center gap-space-xs">
    <span id="chrome-instance-label" class="font-mono-caption text-mono-caption text-on-surface-variant border border-outline-variant/50 px-2 py-0.5 rounded">IR-OWC</span>
    <button id="btn-top-refresh" class="p-1 rounded hover:bg-surface-container-high text-on-surface-variant transition-colors cursor-pointer" title="Refresh Port List">
      <span class="material-symbols-outlined text-[16px]">refresh</span>
    </button>
    <button id="btn-top-settings" class="p-1 rounded hover:bg-surface-container-high text-on-surface-variant transition-colors cursor-pointer" title="Transfer Settings">
      <span class="material-symbols-outlined text-[16px]">settings</span>
    </button>
  </div>
</header>
```

#### 3.4.2. `sidebar_nav.html`

```html
<aside id="app-sidebar" class="w-64 bg-surface-container-low/70 border-r border-black/[0.08] flex flex-col justify-between p-space-md select-none flex-shrink-0">
  <div class="space-y-space-md">
    <div class="sidebar-brand flex items-center gap-space-sm px-1 py-1">
      <div class="w-8 h-8 rounded-lg bg-primary-container text-white flex items-center justify-center shadow-sm shrink-0">
        <span class="material-symbols-outlined text-[20px] text-white">radar</span>
      </div>
      <div class="sidebar-brand-text leading-tight min-w-0">
        <div class="font-title-sm text-title-sm font-bold text-on-surface truncate">SpectraLink IR-OWC</div>
        <div class="font-mono-caption text-mono-caption text-on-surface-variant truncate">Lab Terminal v1.0</div>
      </div>
    </div>

    <div class="space-y-1">
      <div class="sidebar-section-title px-space-xs py-1 text-mono-caption font-mono-caption text-outline font-semibold tracking-wider">
        OPTICAL SUBSYSTEMS
      </div>
      <nav class="space-y-1" id="nav-menu">
        <a data-target="view-hardware" title="Hardware &amp; Link" class="nav-item flex items-center gap-space-sm px-space-md py-space-xs rounded-lg bg-primary-container text-white font-medium shadow-sm transition-all cursor-pointer">
          <span class="material-symbols-outlined text-[18px] shrink-0">tune</span>
          <span class="sidebar-label font-label-md text-label-md truncate">Hardware &amp; Link</span>
        </a>
        <a data-target="view-file" title="File Transfer" class="nav-item flex items-center gap-space-sm px-space-md py-space-xs rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest/60 transition-colors cursor-pointer">
          <span class="material-symbols-outlined text-[18px] shrink-0">swap_horiz</span>
          <span class="sidebar-label font-label-md text-label-md truncate">File Transfer</span>
        </a>
        <a data-target="view-chat" title="Realtime Chat" class="nav-item flex items-center gap-space-sm px-space-md py-space-xs rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest/60 transition-colors cursor-pointer">
          <span class="material-symbols-outlined text-[18px] shrink-0">forum</span>
          <span class="sidebar-label font-label-md text-label-md truncate">Realtime Chat</span>
        </a>
        <a data-target="view-telemetry" title="Lab Diagnostics" class="nav-item flex items-center gap-space-sm px-space-md py-space-xs rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest/60 transition-colors cursor-pointer">
          <span class="material-symbols-outlined text-[18px] shrink-0">terminal</span>
          <span class="sidebar-label font-label-md text-label-md truncate">Lab Diagnostics</span>
        </a>
      </nav>
    </div>
  </div>

  <div class="sidebar-footer bg-surface-container-lowest rounded-xl p-space-md border border-black/[0.08] shadow-[0_1px_3px_rgba(0,0,0,0.04)] space-y-2">
    <div class="flex items-center justify-between gap-1">
      <div class="flex items-center gap-1.5 min-w-0">
        <span id="footer-health-dot" class="w-2 h-2 rounded-full bg-outline shrink-0"></span>
        <span id="footer-health-text" class="font-mono-caption text-mono-caption font-medium text-outline truncate">OFFLINE</span>
      </div>
      <span class="sidebar-footer-detail text-[10px] font-mono text-outline tracking-widest">UART</span>
    </div>
    <div class="sidebar-footer-detail text-[11px] font-mono leading-relaxed text-on-surface-variant border-t border-black/[0.06] pt-2 flex flex-col gap-1">
      <div class="flex items-center justify-between gap-2">
        <span class="text-outline">Ports:</span>
        <span id="footer-port-label" class="font-medium text-on-surface truncate text-right">Disconnected</span>
      </div>
      <div class="flex items-center justify-between">
        <span class="text-outline">Baud:</span>
        <span id="footer-baud-label" class="font-medium text-on-surface">--</span>
      </div>
    </div>
    <button id="btn-calibrate" title="Ping Link Test" class="w-full mt-1 flex items-center justify-center gap-1 py-1 px-2 rounded-md bg-surface-container-low hover:bg-surface-container text-primary font-label-sm text-label-sm border border-outline-variant/40 transition-colors cursor-pointer">
      <span class="material-symbols-outlined text-[14px]">wifi_tethering</span>
      <span class="sidebar-action-label">Ping Link Test</span>
    </button>
  </div>
</aside>
```

#### 3.4.3. `view_hardware.html`

```html
<span id="hw-header-link-badge" class="ui-badge" data-tone="neutral" data-size="compact" role="status" aria-live="polite">
  <span class="ui-badge__dot"></span>
  <span class="ui-badge__label">DISCONNECTED</span>
</span>
```

```html
<div class="flex items-center gap-3">
  <span id="hw-tx-status" class="ui-badge" data-tone="neutral" data-size="micro" title="TX port state">
    <span id="hw-tx-dot" class="ui-badge__dot"></span>
    <span class="ui-badge__label">TX</span>
  </span>
  <span id="hw-rx-status" class="ui-badge" data-tone="neutral" data-size="micro" title="RX port state">
    <span id="hw-rx-dot" class="ui-badge__dot"></span>
    <span class="ui-badge__label">RX</span>
  </span>
</div>
```

```html
<div class="flex flex-wrap justify-between items-center gap-1 text-mono-caption font-mono-caption text-on-surface-variant">
  <span>MEASURED RTT LATENCY</span>
  <span id="hw-rtt-state" class="ui-badge shrink-0" data-tone="neutral" data-size="micro" role="status" aria-live="polite">
    <span class="ui-badge__dot"></span>
    <span class="ui-badge__label">IDLE</span>
  </span>
</div>
```

Các thay đổi grid:

```html
<div class="split-grid">
```

```html
<div class="port-grid">
```

```html
<div class="spec-grid gap-px bg-black/[0.06] border border-black/[0.06] rounded-lg overflow-hidden">
```

#### 3.4.4. `view_file.html`

```html
<span id="file-active-status" class="ui-badge" data-tone="neutral" data-size="micro" role="status" aria-live="polite">
  <span class="ui-badge__dot"></span>
  <span class="ui-badge__label">ARQ IDLE</span>
</span>
```

```html
<div class="split-grid">
```

```html
<div class="file-stats p-space-sm rounded-lg bg-surface-container-low border border-black/[0.04] mb-space-md">
```

```html
<input class="h-[28px] pl-7 pr-2 text-label-sm font-label-sm rounded-lg bg-surface border border-black/[0.1] focus:border-primary focus:ring-1 focus:ring-primary outline-none w-full sm:w-48" placeholder="Filter transmissions..." type="text"/>
```

```html
<table class="data-table w-full text-left border-collapse">
```

#### 3.4.5. `view_chat.html`

```html
<span id="chat-link-state" class="ui-badge" data-tone="neutral" data-size="micro" role="status" aria-live="polite">
  <span class="ui-badge__dot"></span>
  <span class="ui-badge__label">LINK STANDBY</span>
</span>
```

```html
<input class="flex-1 min-w-0 bg-transparent border-0 text-mono-data font-mono-data text-on-surface focus:ring-0 focus:outline-none p-0 h-8 placeholder:text-outline" placeholder="Enter payload string or message to transmit..." type="text" value=""/>
```

#### 3.4.6. `view_telemetry.html`

```html
<div class="flex items-center justify-between gap-2">
  <span class="font-mono-caption text-mono-caption text-outline uppercase tracking-wider">Round-trip latency</span>
  <span id="kpi-rtt-badge" class="ui-badge" data-tone="neutral" data-size="micro" role="status" aria-live="polite">
    <span class="ui-badge__dot"></span>
    <span class="ui-badge__label">IDLE</span>
  </span>
</div>
```

```html
<div class="kpi-grid">
```

```html
<div class="split-grid">
```

```html
<div id="sniffer-filter-bar" class="flex flex-wrap items-center bg-surface-container-low p-0.5 rounded-md border border-black/[0.08] font-mono-caption text-[10px]">
```

```html
<input id="sniffer-search" class="h-[28px] pl-6 pr-2 rounded-md bg-surface-container-lowest border border-black/[0.1] font-mono-data text-[11px] text-on-surface placeholder:text-outline focus:border-primary focus:ring-1 focus:ring-primary focus:outline-none w-full sm:w-44" placeholder="Hex opcode or frame ID..." type="text"/>
```

```html
<table class="data-table w-full text-left border-collapse">
```

```html
<div class="px-space-md py-1.5 bg-surface-container-low border-t border-black/[0.08] flex flex-wrap items-center justify-between gap-2 font-mono-caption text-[10px] text-on-surface-variant">
```

#### 3.4.7. `ui_feedback.html`

```html
<div id="modal-overlay" class="fixed inset-0 z-[10000] hidden items-center justify-center p-4">
  <div id="modal-backdrop" class="ui-modal-backdrop absolute inset-0 bg-on-surface/20"></div>
  <div id="modal-card" role="dialog" aria-modal="true"
    class="relative w-full max-w-sm max-h-[calc(100vh-2rem)] overflow-y-auto bg-surface-container-lowest rounded-2xl border border-black/[0.06] shadow-[0_8px_30px_rgba(0,0,0,0.12)] p-space-lg flex flex-col items-center text-center gap-2">
```

#### 3.4.8. `settings_modal.html`

```html
<div id="settings-overlay" class="fixed inset-0 z-[10000] hidden items-center justify-center p-4">
  <div id="settings-backdrop" class="ui-modal-backdrop absolute inset-0 bg-on-surface/20"></div>
  <div id="settings-card" role="dialog" aria-modal="true" aria-labelledby="settings-title"
    class="relative w-full max-w-md max-h-[calc(100vh-2rem)] overflow-y-auto bg-surface-container-lowest rounded-2xl border border-black/[0.06] shadow-[0_8px_30px_rgba(0,0,0,0.12)] p-space-lg flex flex-col gap-space-md">
```

### 3.5. JavaScript controller snippets

#### 3.5.1. `bridge_client.js` — connect success dùng toast

```js
bridge.portConnected.connect((txPort, rxPort, baud) => {
  clearConnectLoading();
  requestAnimationFrame(() => {
    setConnectedUi(txPort, rxPort, baud);
    toast(
      'success',
      'Link connected',
      'TX: ' + txPort + ' • RX: ' + rxPort + ' @ ' + baud + ' bps • 8-N-1',
      { durationMs: 2800 }
    );
  });
});
```

#### 3.5.2. `bridge_client.js` — helper và badge update

```js
function setText(id, text) {
  const el = document.getElementById(id);
  if (el && el.textContent !== text) el.textContent = text;
}

function setTone(id, tone) {
  const el = document.getElementById(id);
  if (el) el.dataset.tone = tone;
}

function setConnectedUi(txPort, rxPort, baud) {
  const summary = 'TX: ' + txPort + ' • RX: ' + rxPort;
  setText('ribbon-text', 'STM32 Optical Bridge • ' + summary + ' (' + baud + ' bps)');
  setTone('ribbon-badge', 'success');

  const footerHealthDot = document.getElementById('footer-health-dot');
  if (footerHealthDot) footerHealthDot.className = 'w-2 h-2 rounded-full bg-secondary';
  setText('footer-health-text', 'ONLINE');
  setText('footer-port-label', summary);
  setText('footer-baud-label', baud + ' bps');

  if (window.UIBadge) {
    window.UIBadge.set('hw-header-link-badge', { tone: 'success', label: 'LINK CONNECTED' });
    window.UIBadge.set('hw-tx-status', { tone: 'success', label: 'TX OPEN' });
    window.UIBadge.set('hw-rx-status', { tone: 'success', label: 'RX OPEN' });
    window.UIBadge.set('hw-rtt-state', { tone: 'neutral', label: 'READY' });
    window.UIBadge.set('chat-link-state', { tone: 'success', label: 'LINK CONNECTED' });
  }

  const connBtn = document.getElementById('btn-connect');
  if (connBtn) {
    connBtn.dataset.state = 'connected';
    connBtn.innerText = 'Disconnect';
  }
}
```

#### 3.5.3. `bridge_client.js` — TX/RX activity không dùng ring

```js
bridge.rawTxData.connect(() => {
  if (window.UIBadge) window.UIBadge.setActive('hw-tx-status', true);
  if (txTimeout) clearTimeout(txTimeout);
  txTimeout = setTimeout(() => {
    if (window.UIBadge) window.UIBadge.setActive('hw-tx-status', false);
  }, 120);
});

bridge.rawRxData.connect(() => {
  if (window.UIBadge) window.UIBadge.setActive('hw-rx-status', true);
  if (rxTimeout) clearTimeout(rxTimeout);
  rxTimeout = setTimeout(() => {
    if (window.UIBadge) window.UIBadge.setActive('hw-rx-status', false);
  }, 120);
});
```

#### 3.5.4. `bridge_client.js` — RTT badge

```js
bridge.pingResult.connect((rtt, seq) => {
  clearPingLoading();
  setText('hw-rtt-value', rtt.toFixed(1) + ' ms');
  if (window.UIBadge) {
    window.UIBadge.set('hw-rtt-state', {
      tone: window.UIBadge.toneForRtt(rtt),
      label: 'MEASURED'
    });
  }
  toast('success', 'Ping echo received', 'Seq #' + seq + ' • RTT ' + rtt.toFixed(1) + ' ms');
});
```

#### 3.5.5. `file_controller.js` — ARQ state mapping

```js
function arqLabel(state) {
  const map = {
    IDLE: 'ARQ IDLE',
    HANDSHAKING: 'ARQ SYNC',
    TRANSFERRING: 'ARQ TX',
    RECEIVING: 'ARQ RX',
    FINALIZING: 'ARQ FINALIZE',
    COMPLETED: 'ARQ COMPLETE',
    FAILED: 'ARQ FAILED',
    ABORTED: 'ARQ ABORTED'
  };
  const key = String(state || '').toUpperCase();
  return map[key] || 'ARQ ' + key;
}

function arqTone(state) {
  const key = String(state || '').toUpperCase();
  if (key === 'COMPLETED') return 'success';
  if (key === 'FAILED') return 'error';
  if (key === 'ABORTED') return 'neutral';
  if (key === 'IDLE') return 'neutral';
  if (key === 'FINALIZING') return 'warning';
  return 'info';
}

function setActiveStatus(label, tone) {
  if (window.UIBadge) window.UIBadge.set('file-active-status', { tone: tone, label: label });
}

window.bridge.transferStateChanged.connect((state) => {
  setActiveStatus(arqLabel(state), arqTone(state));
});
```

#### 3.5.6. `telemetry_controller.js` — RTT badge và resize

```js
if (rttValueElem) rttValueElem.innerText = rttMs > 0 ? Math.round(rttMs) : '--';
if (window.UIBadge && document.getElementById('kpi-rtt-badge')) {
  const tone = window.UIBadge.toneForRtt(rttMs);
  const label = tone === 'neutral' ? 'IDLE' : tone === 'success' ? 'GOOD' : tone === 'warning' ? 'FAIR' : 'SLOW';
  window.UIBadge.set('kpi-rtt-badge', { tone: tone, label: label });
}
```

```js
let chartResizeTimer = null;
const chartObserver = new ResizeObserver(() => {
  if (chartResizeTimer) clearTimeout(chartResizeTimer);
  chartResizeTimer = setTimeout(drawCanvasCharts, 120);
});
if (speedCanvas && speedCanvas.parentElement) chartObserver.observe(speedCanvas.parentElement);
if (rttCanvas && rttCanvas.parentElement) chartObserver.observe(rttCanvas.parentElement);
```

#### 3.5.7. `app.js` — phát event view change

```js
window.switchView = function(viewId) {
  navItems.forEach(item => {
    const target = item.getAttribute('data-target');
    if (target === viewId) {
      item.className = 'nav-item flex items-center gap-space-sm px-space-md py-space-xs rounded-lg bg-primary-container text-white font-medium shadow-sm transition-all cursor-pointer';
    } else {
      item.className = 'nav-item flex items-center gap-space-sm px-space-md py-space-xs rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest/60 transition-colors cursor-pointer';
    }
  });

  viewPanels.forEach(panel => {
    if (panel.id === viewId) panel.classList.add('active');
    else panel.classList.remove('active');
  });

  if (centerTitle) centerTitle.innerText = titleMap[viewId] || 'SpectraLink IR-OWC';
  window.dispatchEvent(new CustomEvent('view-changed', { detail: { viewId: viewId } }));
};
```

### 3.6. `scripts/build_modular_web.py` — thay đổi dự kiến

Thêm asset link và script:

```html
<link rel="stylesheet" href="assets/css/local-fonts.css"/>
<script src="assets/js/tailwindcss.js"></script>
<script id="tailwind-config">
  tailwind.config = {
    darkMode: "class",
    theme: {
      extend: {
        colors: {
          "surface": "#f8f9ff",
          "surface-container-lowest": "#ffffff",
          "surface-container-low": "#eff4ff",
          "on-surface": "#0b1c30",
          "on-surface-variant": "#434655",
          "outline": "#737686",
          "outline-variant": "#c3c6d7",
          "primary": "#004ac6",
          "primary-container": "#2563eb",
          "secondary": "#006c49",
          "tertiary": "#784b00",
          "error": "#ba1a1a"
        }
      }
    }
  }
</script>
<link rel="stylesheet" href="assets/css/app.css"/>
<style>
  .view-panel { display: none; }
  .view-panel.active { display: flex; }
  @keyframes toast-slide-in {
    from { opacity: 0; transform: translateX(16px); }
    to { opacity: 1; transform: translateX(0); }
  }
  .toast-item { animation: toast-slide-in 0.18s ease-out; }
  .toast-leaving { opacity: 0; transform: translateX(16px); transition: opacity 0.18s ease, transform 0.18s ease; }
</style>
```

Thêm script vào cuối body:

```html
<script src="js/ui_feedback.js"></script>
<script src="js/ui_badges.js"></script>
<script src="js/shared/utils.js"></script>
<script src="js/shared/bridge.js"></script>
<script src="js/app.js"></script>
<script src="js/bridge_client.js"></script>
```

Cập nhật shell:

```html
<div class="flex flex-1 overflow-hidden min-w-0">
  {sidebar_nav}
  <div class="flex-1 flex flex-col overflow-hidden bg-[#F5F5F7] min-w-0">
    <div id="view-hardware" class="view-panel active flex-1 overflow-y-auto p-space-lg flex-col gap-space-lg bg-[#F5F5F7] min-w-0">
      {v1}
    </div>
    <div id="view-file" class="view-panel flex-1 bg-[#F5F5F7] overflow-y-auto p-space-xl flex-col gap-space-lg min-w-0">
      {v2}
    </div>
    <div id="view-chat" class="view-panel flex-1 bg-[#F5F5F7] overflow-y-auto p-space-lg flex-col gap-space-md min-w-0">
      {v3}
    </div>
    <div id="view-telemetry" class="view-panel flex-1 bg-[#F5F5F7] overflow-y-auto p-space-lg flex-col gap-space-lg min-w-0">
      {v4}
    </div>
  </div>
</div>
```

Cập nhật fallback template header/sidebar trong builder để khớp component mới. Nếu không cập nhật fallback, khi component bị thiếu trong môi trường build khác, builder sẽ ghi lại markup cũ và gây drift.

### 3.7. `main_window_web.py` — mã nguồn dự kiến

```python
self.resize(1380, 880)
self.setMinimumSize(720, 600)
```

```python
half_w = max(720, geo.width() // 2)
self.setMinimumSize(min(self.minimumWidth(), half_w), 600)
self.resize(half_w, geo.height())
```

Không thay đổi profile isolation, QWebChannel, hay serial backend trong phạm vi kế hoạch này.

### 3.8. Test contract mới — mã nguồn dự kiến

`tests/unit/test_web_ui_responsive_contract.py`:

```python
"""Static contract tests for responsive layout, badges, and smooth feedback."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_ROOT / "src" / "ir_terminal" / "ui" / "web"
COMPONENTS_DIR = WEB_DIR / "components"


def test_polish_assets_are_wired() -> None:
    index = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert 'href="assets/css/app.css"' in index
    assert 'src="js/ui_badges.js"' in index
    for element_id in (
        "app-sidebar",
        "ribbon-badge",
        "hw-tx-status",
        "hw-rx-status",
        "hw-rtt-state",
        "file-active-status",
        "chat-link-state",
        "kpi-rtt-badge",
    ):
        assert f'id="{element_id}"' in index


def test_no_fullscreen_backdrop_blur_or_pulse() -> None:
    paths = (
        WEB_DIR / "index.html",
        COMPONENTS_DIR / "ui_feedback.html",
        COMPONENTS_DIR / "settings_modal.html",
    )
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "backdrop-blur" not in text
        assert "animate-ping" not in text
        assert "status-pulse" not in text


def test_connect_success_uses_toast() -> None:
    script = (WEB_DIR / "js" / "bridge_client.js").read_text(encoding="utf-8")
    assert "toast(" in script
    assert "'Link connected'" in script
    assert "title: 'Link connected'" not in script


def test_build_script_includes_polish_assets() -> None:
    script = (REPO_ROOT / "scripts" / "build_modular_web.py").read_text(encoding="utf-8")
    assert "assets/css/app.css" in script
    assert "js/ui_badges.js" in script


def test_split_screen_window_floor() -> None:
    source = (REPO_ROOT / "src" / "ir_terminal" / "ui" / "main_window_web.py").read_text(encoding="utf-8")
    assert "setMinimumSize(720, 600)" in source
    assert "max(720, geo.width() // 2)" in source
```

Bổ sung vào `tests/unit/test_web_ui_dual_com_contract.py`:

```python
def test_visual_noise_markers_removed(self, index_html: str) -> None:
    forbidden = (
        "CLEAN LAYERED ARCHITECTURE",
        "880nm NIR Direct LOS",
        "BER &lt; 10",
        "HEALTH 99.8",
        "Auto-Checksum Verification Active",
        "[BURST_TX]",
        "animate-ping",
        "status-pulse",
        "backdrop-blur",
        "modal-pop-in",
        "Port Standby",
        "Port Active",
        "Link Standby",
        "Link Synced",
    )
    for marker in forbidden:
        assert marker not in index_html
```

---

## 4. KẾ HOẠCH TRIỂN KHAI THEO GIAI ĐOẠN

| Giai đoạn | Nội dung | File chính | Đầu ra |
|---|---|---|---|
| 1. Baseline | Chụp ảnh 720/800/960/1280 px; quay màn hình connect dual mock; ghi nhận jank | `scripts/capture_all_screens.py` | Baseline screenshot + video |
| 2. Badge system | Tạo `app.css`, `ui_badges.js`; nâng cấp markup badge | `assets/css/app.css`, `js/ui_badges.js`, components | Badge hiển thị đúng tone, không pulse |
| 3. Responsive shell | Header compact, sidebar rail, container query, view-specific wrap | `chrome_header.html`, `sidebar_nav.html`, 4 view components, builder | Không vỡ ở 720–960 px |
| 4. Smooth feedback | Bỏ blur, success toast, batch DOM, TX/RX active state | `ui_feedback.*`, `bridge_client.js`, `file_controller.js` | Connect không chớp/giật |
| 5. Build & test | Chạy builder, pytest, visual matrix | `index.html`, tests | 282+ test pass, không drift |
| 6. Nghiệm thu | Dual mock 2 cửa sổ, split-screen matrix, accessibility | — | Ký nghiệm thu |

Thứ tự bắt buộc: **Giai đoạn 2 và 3 trước Giai đoạn 4** để khi bỏ modal success, các badge/trạng thái thay thế đã sẵn sàng và không tạo khoảng trống thông tin.

---

## 5. KẾ HOẠCH KIỂM THỬ VÀ TIÊU CHÍ NGHIỆM THU

### 5.1. Automated tests

1. Chạy builder:
   ```bash
   uv run python scripts/build_modular_web.py
   ```
2. Chạy toàn bộ test:
   ```bash
   uv run pytest -q
   ```
3. Kiểm tra diff `index.html` chỉ chứa thay đổi mong muốn; không có drift ngoài component.
4. Test contract mới phải fail trước khi implement và pass sau khi implement.

### 5.2. Visual test matrix

| Width | Height | Mode | Kỳ vọng |
|---:|---:|---|---|
| 720 | 600 | Compact | Sidebar rail; header không tràn; tất cả view dùng được; table cuộn ngang trong card |
| 800 | 700 | Compact | Không horizontal scroll ở body; control không bị mất |
| 960 | 1080 | Split 1080p | Header compact; hardware 1 cột; file stats 3 cột vừa; telemetry toolbar wrap |
| 1024 | 768 | Split nhỏ | Grid chuyển theo container; không tràn chữ |
| 1280 | 720 | Split 2K | 2 cột chính; header center title hiển thị hoặc truncate |
| 1920 | 1080 | Full HD | Layout đầy đủ; badge không chiếm chỗ |
| 2560 | 1440 | 2K | Không giãn quá mức; max-width hợp lý |
| 3840 | 2160 | 4K | Không có khoảng trắng chết; font/scale ổn |

Kiểm tra ở zoom 100%, 125%, 150% nếu QtWebEngine cho phép.

### 5.3. Kiểm tra responsive bằng DevTools/QWebEngine

Có thể chạy với remote debugging:

```bash
QTWEBENGINE_REMOTE_DEBUGGING=9222 uv run python -m ir_terminal
```

Sau đó kiểm tra trong console:

```js
document.documentElement.scrollWidth <= window.innerWidth
```

```js
Array.from(document.querySelectorAll('.app-header, .app-header__left, .app-header__right'))
  .every(el => el.getBoundingClientRect().right <= window.innerWidth + 1)
```

Với bảng, kiểm tra wrapper `.overflow-x-auto` có `scrollWidth > clientWidth` thay vì body bị scroll.

### 5.4. Kiểm tra smooth connect

1. Chạy dual mock:
   ```bash
   ./scripts/run_dual_mock.sh --pty
   ```
2. Connect cả hai cửa sổ trong cùng 1–2 giây.
3. Kỳ vọng:
   - Không có màn hình tối/blur full-screen.
   - Không có modal success.
   - Toast xuất hiện góc dưới phải, trượt nhẹ, tự biến mất sau ~2.8 s.
   - Ribbon/footer/badge cập nhật đúng, không nhảy layout.
   - Không thấy frame trắng/đen hoặc giật rõ trong 3 lần connect liên tiếp.
4. Lặp lại với connect/disconnect 10 lần; kiểm tra không rò toast, không mất focus, không treo.
5. Kiểm tra error path: rút cáp/đóng port để tạo lỗi; modal error vẫn hiển thị, không blur, có nút Retry.

### 5.5. Tiêu chí nghiệm thu

| ID | Tiêu chí | Cách kiểm tra |
|---|---|---|
| AC-01 | Badge dùng đúng tone, không pulse, không spam | Visual review + static test |
| AC-02 | Chỉ có một badge mới cho RTT Telemetry; các badge khác là nâng cấp tại chỗ | Review markup |
| AC-03 | Không còn `backdrop-blur` trong UI | `grep` + test contract |
| AC-04 | Connect success chỉ dùng toast, không dùng modal | Test contract + manual |
| AC-05 | 720/800/960/1280/1920 px không vỡ layout, không horizontal scroll body | Visual matrix |
| AC-06 | Header luôn hiển thị traffic lights, ribbon, refresh, settings | Visual matrix |
| AC-07 | Sidebar rail vẫn truy cập được 4 view và Ping Link Test | Click test |
| AC-08 | Bảng File/Telemetry cuộn ngang trong card, không đẩy body | Visual + DOM check |
| AC-09 | Modal/toast không vượt viewport; nội dung dài cuộn được | Visual ở 720×600 |
| AC-10 | Dual mock connect 2 cửa sổ không giật/chớp rõ | Manual 3 lần |
| AC-11 | `uv run pytest -q` pass toàn bộ (282+ test) | CI/local |
| AC-12 | `uv run python scripts/build_modular_web.py` không tạo drift ngoài dự kiến | Git diff |
| AC-13 | Không có comment/inline comment mới trong code | Review diff |

### 5.6. Regression checklist

- [ ] `SUPPORTED_BAUDRATES` vẫn là `(2400, 4800, 9600, 19200)` và UI render đúng 4 nút.
- [ ] `test_web_ui_dual_com_contract.py` vẫn pass, đặc biệt forbidden markers.
- [ ] `test_backend_bridge.py`, `test_config.py`, `test_dual_serial_transport.py` không bị ảnh hưởng.
- [ ] QWebChannel vẫn nhận `portConnected`, `errorOccurred`, `transferStateChanged`.
- [ ] `index.html` vẫn load được font local và Tailwind offline.
- [ ] Không có `animate-ping`, `status-pulse`, `backdrop-blur`, `modal-pop-in` trong artifact.
- [ ] Không sửa `index.html` bằng tay; mọi thay đổi đi qua builder.

---

## 6. RỦI RO VÀ BIỆN PHÁP GIẢM THIỂU

| Rủi ro | Mức | Giảm thiểu |
|---|---|---|
| Container query không được hỗ trợ trong QtWebEngine môi trường build | Trung bình | Fallback media query; test sớm ở 720/800/960 px |
| CSS mới xung đột Tailwind | Trung bình | Dùng class riêng `.ui-badge`, `.app-header`, `.split-grid`; tránh `!important` toàn cục |
| Sidebar rail làm mất label gây khó dùng | Thấp | Thêm `title` tooltip; giữ icon rõ; kiểm tra click |
| Badge quá nhiều gây nhiễu | Trung bình | Ma trận badge cố định; review visual; không badge cho history rows |
| Min window 720 quá nhỏ gây vỡ | Thấp | Test 720×600; nếu vẫn xấu, nâng lên 800×640 |
| Bỏ modal success làm người dùng không biết đã kết nối | Thấp | Toast + badge + ribbon + footer đều đổi trạng thái; toast 2.8 s |
| Toast bị bỏ lỡ khi cửa sổ không active | Thấp | Badge/ribbon vẫn là nguồn trạng thái persistent |
| GPU flag gây lỗi trên máy khác | Cao | Không đưa flag GPU vào mặc định; chỉ profiling và tài liệu hóa |
| Builder fallback template bị lệch | Trung bình | Cập nhật fallback trong `build_modular_web.py`; test asset wiring |
| `index.html` drift do sửa tay | Cao | Quy ước chỉ sửa component; chạy builder; test contract |

---

## 7. DEFINITION OF DONE

Kế hoạch được coi là hoàn thành khi:

1. `docs/plans/2026-11-04_UI_POLISH_AND_RESPONSIVE_PLAN.md` được review và duyệt.
2. Implementation tuân thủ đúng danh sách file, không sửa `index.html` bằng tay.
3. Toàn bộ test pass, bao gồm test contract mới.
4. Visual matrix 720–3840 px không có lỗi layout/tràn chữ/mất control.
5. Dual mock connect hai cửa sổ không còn hiện tượng giật/chớp rõ rệt.
6. Không có comment/inline comment mới trong code.
7. Không còn `backdrop-blur` hoặc success modal full-screen trong luồng connect.

---

## 8. PHỤ LỤC: CHECKLIST TRIỂN KHAI NHANH

```text
[ ] Tạo assets/css/app.css
[ ] Tạo js/ui_badges.js
[ ] Sửa chrome_header.html
[ ] Sửa sidebar_nav.html
[ ] Sửa view_hardware.html
[ ] Sửa view_file.html
[ ] Sửa view_chat.html
[ ] Sửa view_telemetry.html
[ ] Sửa ui_feedback.html
[ ] Sửa settings_modal.html
[ ] Sửa js/ui_feedback.js
[ ] Sửa js/bridge_client.js
[ ] Sửa js/file_controller.js
[ ] Sửa js/telemetry_controller.js
[ ] Sửa js/chat_controller.js nếu cần
[ ] Sửa js/app.js
[ ] Sửa scripts/build_modular_web.py
[ ] Sửa src/ir_terminal/ui/main_window_web.py
[ ] Cập nhật scripts/capture_all_screens.py
[ ] Tạo tests/unit/test_web_ui_responsive_contract.py
[ ] Cập nhật tests/unit/test_web_ui_dual_com_contract.py
[ ] Chạy uv run python scripts/build_modular_web.py
[ ] Chạy uv run pytest -q
[ ] Chụp ảnh 720/800/960/1280/1920 px
[ ] Chạy dual mock connect 2 cửa sổ
[ ] Review diff không có comment mới
```
