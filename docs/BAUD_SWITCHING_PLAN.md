# Baudrate Switching & Serial Stability Plan

**Project:** LabHongNgoai / SpectraLink IR-OWC Workstation  
**Scope:** PC application ↔ STM32 optical baudrate switching, control-message handling, and serial link stability  
**Status:** Implemented — see `docs/PROTOCOL.md` §8 for the wire contract  
**Date:** 2026-11-05

---

## 1. Executive Summary

The current application conflates two different baudrates:

1. The **physical PC ↔ STM32 COM/UART link**, which must stay fixed at **115200 bps**.
2. The **optical/IR channel baudrate** selected in the UI (`4800`, `9600`, `19200`, `38400`), which is controlled by the STM32 firmware.

Today the UI passes the selected optical baudrate directly into the serial connection path. The result is that choosing `4800`, `19200`, etc. reopens the physical COM port at that baudrate instead of keeping it at `115200`. This causes the exact behaviour the new design must eliminate: a UI baud selection tears down the physical link, resets RX buffers/framer statistics, and interrupts any active ARQ/file-transfer session.

The target design separates the two concepts completely:

- `SerialManager` / `SerialWorker` / `DualSerialTransport` always open the physical COM port at **115200 8-N-1**, and are never restarted when the optical baudrate changes.
- The selected optical baudrate is sent through the already-open transport as a plain ASCII line: `SET_BAUD:<baud>\n`.
- The STM32 acknowledges with `BAUD_OK:<baud>\n` or reports a hardware DIP-switch change with `NOTIFY_BAUD:<baud>\n`.
- The application extracts these ASCII control lines from the RX stream **without** confusing them with binary frames, and updates the UI state.
- Binary framing (`SOF 0xAA55`, `CRC-32`, `EOF 0x55AA`) and the Stop-and-Wait ARQ engine remain untouched and byte-for-byte identical.

---

## 2. Current Architecture and Data Flow

### 2.1 Relevant layers

```
UI (Chromium/JS)
  bridge_client.js
        |
        | QWebChannel @Slot / Signal
        v
BackendBridge (backend_bridge.py)
        |
        v
SerialManager (serial_manager.py)          main thread facade
        |
        | Qt queued signal _sig_open_dual / _sig_send
        v
SerialWorker (serial_worker.py)            dedicated QThread
        |
        | ITransport
        v
DualSerialTransport (dual_serial_transport.py)
        |
        | serial.Serial(port=..., baudrate=...)
        v
Physical COM / UART @ 115200 bps
```

### 2.2 Current baudrate path (the bug)

1. `bridge_client.js` keeps `selectedBaud`, default `9600`, and renders buttons from `DEFAULT_BAUDRATES = [2400, 4800, 9600, 19200]`.
2. Clicking a baud button only changes `selectedBaud`; it does not send any command. See `bridge_client.js:404-414`.
3. The Connect action calls:

   ```js
   bridge.connectDualPorts(txPort, rxPort, selectedBaud);
   ```

   See `bridge_client.js:448`.
4. `BackendBridge.connectDualPorts()` forwards the same number to the serial manager:

   ```python
   self._serial_manager.connect_ports(tx_name, rx_name, baudrate)
   ```

   See `backend_bridge.py:466-503`, specifically line 503.
5. `SerialManager.connect_ports()` emits `_sig_open_dual(tx_port, rx_port, baudrate)`. See `serial_manager.py:175-189`.
6. `SerialWorker.open_ports()` first calls `self.close_port()` and then calls:

   ```python
   create_transport(..., baudrate=baudrate, ...)
   ```

   See `serial_worker.py:204-278`, especially lines 216 and 226-235.
7. `DualSerialTransport._create_serial()` configures both TX and RX handles with `baudrate=self._baudrate`. See `dual_serial_transport.py:62-72`.

Therefore the selected optical baudrate becomes the physical UART baudrate. Changing it causes a full `close_port()` / reopen cycle.

### 2.3 Current RX path and why control messages are lost

`SerialWorker._poll_serial()` reads chunks from the transport, emits raw bytes, appends them to `_rx_accumulation`, and calls `_drain_rx_buffer()`. See `serial_worker.py:456-500`.

`_drain_rx_buffer()` currently calls only:

```python
packets, remaining = parse_frames_with_stats(pending, self._framer_stats)[:2]
```

See `serial_worker.py:378-421`.

`framer.parse_frames_with_stats()` searches for `0x55 0xAA`. Any bytes that are not part of a valid binary frame are classified as `noise_bytes` and discarded. If no SOF is found, all but the last byte are dropped immediately. See `framer.py:178-196`.

Consequences:

- `BAUD_OK:<baud>\n` and `NOTIFY_BAUD:<baud>\n` are currently treated as noise and never reach the application.
- They would not crash the framer, but they would be invisible to the UI and would inflate noise counters.
- A naive line parser inserted before the framer could accidentally match ASCII-looking bytes inside a binary frame payload and corrupt ARQ data. This must be avoided.

### 2.4 Current serial stability behavior

- `SerialWorker.open_ports()` always closes the previous transport first (`serial_worker.py:216`). This is correct for a real Connect/Disconnect action, but wrong for a baudrate change.
- `_poll_serial()` closes the port on any read exception (`serial_worker.py:492-500`). This is a hardware-fault path and should remain. It must not be triggered by control-message parsing.
- `_current_baud` in `SerialManager` and `SerialWorker` currently represents the physical port baudrate (`serial_manager.py:113-136`, `serial_worker.py:185-188`). It must not be repurposed to mean the optical baudrate.
- The hotplug watcher only reports port-list changes; there is no automatic reconnect loop (`serial_worker.py:523-563`). Auto-reconnect is outside this change unless separately requested.
- `PySerialTransport` is a legacy standalone wrapper and is not constructed by `transport_factory.py` for the active physical path, but it also uses `baudrate=self._baudrate` and must remain consistent if used later (`pyserial_transport.py:39-47`).

### 2.5 Current constants and defaults

`protocol/constants.py:77-84`:

```python
DEFAULT_BAUDRATE: int = 9600
SUPPORTED_BAUDRATES: tuple[int, ...] = (
    2400,
    4800,
    9600,
    19200,
)
```

`config.py:31`:

```python
DEFAULT_BAUD: int = 9600
```

`bridge_client.js:5`:

```js
const DEFAULT_BAUDRATES = [2400, 4800, 9600, 19200];
```

Problems:

- No `38400` option.
- No distinction between physical `115200` and optical rates.
- `DEFAULT_BAUDRATE` is used by transport constructors, so changing it blindly would affect mock and test behaviour; the plan below handles that deliberately.

---

## 3. Target Architecture and Invariants

### 3.1 Invariants

| ID | Invariant | Enforcement point |
| :--- | :--- | :--- |
| I1 | Physical PC ↔ STM32 COM/UART is always opened at 115200 8-N-1. | `BackendBridge.connectDualPorts()`, `SerialWorker.open_ports()`, `DualSerialTransport` |
| I2 | Changing optical baudrate never calls `disconnect_port()`, `close_port()`, or `open_ports()`. | `BackendBridge.setOpticalBaud()`; UI baud click handler |
| I3 | Optical baudrate commands use the existing open transport as ASCII lines. | `SerialManager.send_control_line()` → existing `_sig_send` / `send_bytes()` |
| I4 | `BAUD_OK:<baud>` and `NOTIFY_BAUD:<baud>` update authoritative optical baud state and UI. | `SerialWorker.control_message_received` → `BackendBridge` state machine |
| I5 | Binary framing and ARQ are never modified by baud control. | Dedicated frame-aware RX demultiplexer; no use of `pack_frame()` for control lines |
| I6 | No source comments are introduced. | Code review / lint policy; documentation may use Markdown comments |

### 3.2 Two-baudrate model

| Concept | Value source | Ownership | Persistent? |
| :--- | :--- | :--- | :--- |
| Physical UART baudrate | Constant `PHYSICAL_BAUDRATE = 115200` | `SerialManager` / `SerialWorker` / transport | No |
| Optical/IR baudrate | UI selector, launch `--baud`, `BAUD_OK`, `NOTIFY_BAUD` | `BackendBridge._optical_baud` | Optional (see §8.4) |

`SerialManager.current_baud` remains the physical baudrate. The optical baudrate lives in `BackendBridge`, because it is an application/protocol concern, not a serial-port property.

---

## 4. Wire Protocol Extension: ASCII Control Channel

### 4.1 Messages

| Direction | Message | Meaning |
| :--- | :--- | :--- |
| PC → STM32 | `SET_BAUD:<baud>\n` | Request optical baudrate change |
| STM32 → PC | `BAUD_OK:<baud>\n` | Command accepted; optical baudrate is now `<baud>` |
| STM32 → PC | `NOTIFY_BAUD:<baud>\n` | Hardware DIP switch SW1/SW2 changed the optical baudrate |
| STM32 → PC | `BAUD_ERR:<reason>\n` | Optional error response; App keeps the previous rate |
| PC → STM32 | `SET_BAUD:<baud>\n` may be echoed | Ignore echoed command; do not re-send automatically |

### 4.2 Grammar

```text
CONTROL_LINE  := PREFIX ":" ARGUMENT "\n"
PREFIX        := "SET_BAUD" | "BAUD_OK" | "NOTIFY_BAUD" | "BAUD_ERR"
ARGUMENT      := BAUD_VALUE | REASON_TEXT
BAUD_VALUE    := 1*6 DIGIT
REASON_TEXT   := 1*48 printable ASCII character
```

- TX terminator is exactly `\n` (`0x0A`). RX should tolerate `\r\n`.
- Line length is bounded: recommended `CONTROL_LINE_MAX_BYTES = 64`.
- Baud values must be one of `OPTICAL_BAUDRATES`.
- Control lines are never wrapped in `pack_frame()` and never contain `CRC-32`.
- A control line must be emitted only at a frame boundary: before a binary frame starts or after a complete binary frame ends. It must never be inserted inside a frame.
- The STM32 must not send arbitrary text on this channel other than the messages above.

### 4.3 Examples

```text
PC  -> STM32 : SET_BAUD:19200\n
STM32 -> PC  : BAUD_OK:19200\n

User flips SW1/SW2 on STM32:
STM32 -> PC  : NOTIFY_BAUD:4800\n
```

### 4.4 Ordering and timing rules

1. The PC writes complete frames or complete control lines; it never writes half a line.
2. All writes go through the same worker-thread queue (`_sig_send`), so ordering is preserved.
3. The App may send `SET_BAUD` while the link is idle. Recommended policy: reject or defer user-initiated baud changes while a file transfer is in `HANDSHAKING`, `TRANSFERRING`, or `FINALIZING`.
4. `NOTIFY_BAUD` is authoritative hardware state and always wins over a pending `SET_BAUD`.
5. `BAUD_OK` is accepted only if it matches the pending request; if there is no pending request, it is still accepted as an idempotent confirmation.
6. There is no request ID in the wire format, so the bridge uses a generation counter to reject stale `BAUD_OK` after a newer `NOTIFY_BAUD`.
7. Recommended `BAUD_ACK_TIMEOUT_MS = 1000` for an optional UI warning. A timeout must not close or reopen the port.

---

## 5. Design Details

### 5.1 Constants and semantics

**File:** `src/ir_terminal/protocol/constants.py`

Recommended additions/changes:

```python
PHYSICAL_BAUDRATE: int = 115200
DEFAULT_OPTICAL_BAUDRATE: int = 9600
OPTICAL_BAUDRATES: tuple[int, ...] = (4800, 9600, 19200, 38400)
SUPPORTED_BAUDRATES: tuple[int, ...] = OPTICAL_BAUDRATES
AVAILABLE_BAUDRATES: tuple[int, ...] = SUPPORTED_BAUDRATES
DEFAULT_BAUDRATE: int = PHYSICAL_BAUDRATE
```

Notes:

- `SUPPORTED_BAUDRATES` and `AVAILABLE_BAUDRATES` remain the UI/optical list.
- If `2400` must stay for legacy firmware, expose it only through settings, not as a default UI option.
- `DEFAULT_BAUDRATE` becomes the physical default so any legacy transport constructor is safe by default.
- `config.DEFAULT_BAUD` stays `9600` and represents the default optical rate selected in the UI.
- Add control-channel wire tokens either here or in a dedicated `protocol/control_channel.py`. Prefer the dedicated module for parsing constants, and only expose physical/optical baud constants in `constants.py`.

### 5.2 Transport layer: never change the COM baudrate

**Files:** `dual_serial_transport.py`, `pyserial_transport.py`, `serial_worker.py`

Required behaviour:

- `DualSerialTransport` continues to receive `baudrate=PHYSICAL_BAUDRATE` only.
- Its `baudrate` property remains the physical baudrate.
- No new method such as `set_baudrate()` should be added to the transport. The optical baudrate is not a transport setting.
- `SerialWorker.open_ports()` should be called only for a real connection lifecycle action. It must not be triggered by `setOpticalBaud`.
- If the application ever uses `PySerialTransport` directly, it must also be opened at `PHYSICAL_BAUDRATE`.
- `SerialWorker` may keep the parameter name `baudrate` for API compatibility, but its contract/documentation becomes "physical UART baudrate" and all application callers pass `PHYSICAL_BAUDRATE`.

Recommended defensive measure in `BackendBridge.connectDualPorts()`:

```python
self._serial_manager.connect_ports(tx_name, rx_name, PHYSICAL_BAUDRATE)
```

The third argument from the UI is treated as the requested optical baudrate, not as a physical port setting.

### 5.3 New RX demultiplexer for ASCII control lines

**New file:** `src/ir_terminal/protocol/control_channel.py`  
**New file or framer extension:** `src/ir_terminal/protocol/stream_demux.py` (recommended)  
**Integration point:** `SerialWorker._drain_rx_buffer()`

The demuxer must satisfy two hard requirements:

1. It must recognise complete control lines even when they are split across multiple `read()` calls.
2. It must never look for control-line prefixes inside bytes that belong to a valid binary frame payload.

Recommended algorithm: single-pass frame-aware scanner.

**Inputs:** current accumulated byte buffer, `FramerStats` accumulator.

**Outputs:** ordered events or separate results:

```python
@dataclass(slots=True)
class DemuxResult:
    packets: list[Packet]
    control_lines: list[str]
    remaining: bytes
    consumed: int
```

**Algorithm:**

1. Start at offset `idx = 0`.
2. If bytes at `idx` begin with SOF `0x55 0xAA`:
   - Try to parse one binary frame at `idx` using the same header/LEN/EOF/CRC-32 rules as `framer.py`.
   - If the frame is complete and valid: append `Packet`, advance `idx` by the full frame length, and reset any partial control-line candidate because a frame boundary was reached.
   - If complete but invalid (bad EOF/CRC/len): update the existing `FramerStats` counters and advance/resynchronise exactly like `framer.py` does.
   - If incomplete: return `remaining = buffer[idx:]` so the worker keeps the partial frame for the next chunk.
3. If bytes at `idx` do not begin with SOF:
   - Scan forward to the next SOF or end of buffer.
   - Feed the intervening bytes to a bounded ASCII line accumulator.
   - Printable ASCII `0x20..0x7E`, `\r`, and `\n` are line candidates.
   - On `\n`, validate the line against the control grammar:
     - Recognised control line → append to `control_lines`, do not count its bytes as framer noise.
     - Unrecognised line → discard/count as noise like today.
   - On a non-printable binary byte outside a frame → discard the partial line, count as noise, continue.
   - If the buffer ends while a plausible control line is incomplete, return it in `remaining` so the line can complete on the next chunk.
   - If there is no partial line and no SOF, retain at least the final byte (to handle a split SOF across chunks), matching current framer behaviour.
4. Enforce `CONTROL_LINE_MAX_BYTES`:
   - Exceeding the cap discards the candidate line as noise and resets the accumulator.
   - This prevents unbounded memory growth from binary garbage that happens to be printable.
5. Preserve ordering:
   - A control line that appears before a frame is emitted before that frame.
   - A control line that appears after a frame is emitted after that frame.
   - The demuxer is the only component that decides whether bytes are frame, control, or noise.

**Implementation note:** To avoid duplicating CRC/EOF/LEN logic, extract the frame-at-offset parsing portion of `framer.parse_frames_with_stats()` into a private/shared helper (for example `parse_frame_at(view, offset, stats)`), then use that helper from both the existing framer and the new demuxer. The public `parse_frames_from_buffer()` and `parse_frames_with_stats()` APIs must remain unchanged to avoid breaking existing tests and callers.

**Framer statistics:**

- Add optional control-line counters (`control_lines`, `control_bytes`) if useful for diagnostics.
- Recognised control lines must not increment `crc_errors`, `eof_errors`, `len_errors`, or `noise_bytes`.
- Unknown ASCII lines may still count as `noise_bytes`.

### 5.4 SerialWorker and SerialManager changes

**File:** `src/ir_terminal/core/serial_worker.py`

- Add a signal:

  ```python
  control_message_received: ClassVar[Signal] = Signal(str)
  ```

  The payload is the normalised line without the trailing newline, e.g. `"BAUD_OK:19200"`.

- Replace the direct `parse_frames_with_stats()` call inside `_drain_rx_buffer()` with the new demuxer call.
- Keep `_rx_accumulation`, `_rx_consumed`, `_rx_dropped`, and compaction logic. Adjust consumed-length calculation to use `DemuxResult.consumed` or equivalent.
- For each recognised control line, log at INFO with a new `LogTag.BAUD_CTRL` and emit `control_message_received`.
- Wrap the demuxer call in a `try/except` that logs the failure and falls back to the existing framer for that chunk. A parser bug must not close the physical port.
- `send_bytes()` remains the single TX path. The worker writes both binary frames and ASCII lines; no special transport logic is needed.
- Do not reset `_framer_stats`, `_rx_accumulation`, or the transport when a control message is received.

**File:** `src/ir_terminal/core/serial_manager.py`

- Add and forward `control_message_received` from worker to manager:

  ```python
  self._worker.control_message_received.connect(self.control_message_received)
  ```

- Add a validated convenience method:

  ```python
  def send_control_line(self, message: str) -> None:
      ...
  ```

  Behaviour:
  - Strip trailing `\r\n`.
  - Reject empty, non-ASCII, or overlong lines.
  - Reject embedded `\r`/`\n`.
  - Append exactly one `\n`.
  - Emit through `self._sig_send`.
  - Log at INFO with `LogTag.BAUD_CTRL`.
  - Do not call `send_packet()` and do not touch ARQ sequence numbers.

- Keep `current_baud` and `_current_baud` as physical baudrate. Add a docstring clarification.
- `disconnect_port()` remains the only normal path that closes the port.

### 5.5 BackendBridge state machine

**File:** `src/ir_terminal/ui/backend_bridge.py`

#### New state

```python
self._optical_baud: int = DEFAULT_OPTICAL_BAUDRATE
self._pending_optical_baud: int | None = None
self._baud_request_generation: int = 0
```

#### New signals

```python
opticalBaudChanged: ClassVar[Signal] = Signal(int)
opticalBaudPending: ClassVar[Signal] = Signal(int)
baudControlStatus: ClassVar[Signal] = Signal(str, str)
```

Minimum requirement is `opticalBaudChanged(int)`. The other two are recommended for UI feedback.

#### Signal rewiring

Current code connects `SerialManager.ports_connected` directly to `BackendBridge.portConnected` (`backend_bridge.py:126`). This bypasses the bridge state and cannot carry the optical baudrate. Change it so:

- `ports_connected` connects only to `_on_link_up`.
- `_on_link_up(tx_port, rx_port, physical_baud)`:
  - asserts/logs `physical_baud == PHYSICAL_BAUDRATE`;
  - starts the health timer;
  - emits `portConnected(tx_port, rx_port, self._optical_baud)` so existing UI code displays the optical rate;
  - emits `opticalBaudChanged(self._optical_baud)`;
  - schedules `SET_BAUD:<self._optical_baud>` once the transport is known open.

`portConnected` therefore keeps its existing 3-argument shape, but the third argument is documented as the **optical** baudrate. Physical baudrate is exposed separately via `getConnectedPorts()` and `getLaunchConfig()`.

#### `connectDualPorts(tx_port, rx_port, baudrate)`

- Validate the requested value as an optical baudrate.
- Store it as the desired optical baudrate (`self._optical_baud = baudrate`).
- Call `serial_manager.connect_ports(tx_name, rx_name, PHYSICAL_BAUDRATE)`.
- Never pass the optical value into the transport.

If backward compatibility with arbitrary values is required, accept any positive integer but only send `SET_BAUD` for values in `SUPPORTED_BAUDRATES`; otherwise surface a validation error. Recommended strict behaviour: reject unsupported optical rates.

#### New slot: `setOpticalBaud(baudrate)`

Recommended signature:

```python
@Slot(int, result=str)
def setOpticalBaud(self, baudrate: int) -> str:
    ...
```

Behaviour:

1. Validate `baudrate` against `SUPPORTED_BAUDRATES`.
2. If the link is not connected:
   - update the desired optical baudrate for the next connect;
   - return `{"ok": true, "connected": false, "baudrate": ...}`.
3. If a file transfer is active (`HANDSHAKING`, `TRANSFERRING`, `FINALIZING`):
   - reject with an error result;
   - do not send a command and do not close the port.
4. If already at the requested rate and no pending request:
   - return success without sending.
5. Set `_pending_optical_baud = baudrate`, increment `_baud_request_generation`, emit `opticalBaudPending(baudrate)`.
6. Call `self._serial_manager.send_control_line(f"SET_BAUD:{baudrate}")`.
7. Start an optional single-shot timer for `BAUD_ACK_TIMEOUT_MS`; on expiry emit `baudControlStatus("timeout", ...)` and clear pending. Do not change the active `_optical_baud`.

#### Control message handler

Connect manager signal:

```python
self._serial_manager.control_message_received.connect(self._on_control_message)
```

Handler rules:

| Message | Action |
| :--- | :--- |
| `BAUD_OK:<n>` | If `n == _pending_optical_baud`, accept. If no pending, accept idempotently. Set `_optical_baud = n`, clear pending, emit `opticalBaudChanged(n)` and `baudControlStatus("ok", ...)`. |
| `NOTIFY_BAUD:<n>` | Authoritative. Set `_optical_baud = n`, clear pending, increment generation to invalidate stale ACKs, emit `opticalBaudChanged(n)` and `baudControlStatus("notify", ...)`. |
| `BAUD_ERR:<reason>` | Clear pending, emit `baudControlStatus("error", reason)`, keep the previous `_optical_baud`. |
| `SET_BAUD:<n>` | Ignore as a local echo; optionally log at DEBUG. |
| Unknown line | Log at DEBUG/WARNING; do not emit a packet; do not affect ARQ. |

All updates happen on the main/Qt thread because the worker emits a queued signal.

#### `getConnectedPorts()`

Extend the JSON object without removing existing keys:

```json
{
  "tx_port": "COM5",
  "rx_port": "COM6",
  "physical_baudrate": 115200,
  "baudrate": 9600,
  "pending_baudrate": null
}
```

- `baudrate` remains the optical/UI value for compatibility.
- `physical_baudrate` is always `115200` for a physical serial link.

#### `getLaunchConfig()`

Extend with:

```json
{
  "physical_baudrate": 115200,
  "default_optical_baudrate": 9600
}
```

Keep `baud`, `default_baudrate`, and `supported_baudrates` for existing UI code.

#### Health checks

`_on_health_tick()` already skips active file transfers (`backend_bridge.py:239-254`). Add a skip while `_pending_optical_baud is not None` so the `BAUD_OK` response is not competing with a health ping. This is a minor robustness improvement and does not affect ARQ state.

### 5.6 UI / JavaScript changes

**File:** `src/ir_terminal/ui/web/js/bridge_client.js`

- Replace the default list:

  ```js
  const DEFAULT_BAUDRATES = [4800, 9600, 19200, 38400];
  ```

- Keep `selectedBaud` as the optical rate.
- Track an additional `pendingBaud = null`.
- `renderBaudrateSelector(baudrates, activeBaud, pendingBaud)`:
  - active button uses the active style;
  - pending button may use an info/pending style and a `data-pending="true"` attribute;
  - all other buttons remain normal.
- Baud button click handler (`bridge_client.js:404-414`):
  - update `selectedBaud` visually;
  - if the link is connected and `bridge.setOpticalBaud` exists, call `bridge.setOpticalBaud(baudVal)`;
  - if not connected, only update the local selection;
  - **never** call `connectDualPorts()` and never call `disconnectPort()` from this handler.
- `initBridgeListeners()`:
  - connect `bridge.opticalBaudChanged`:

    ```js
    bridge.opticalBaudChanged.connect((baud) => {
      selectedBaud = baud;
      pendingBaud = null;
      renderBaudrateSelector(currentOptions, baud, null);
      updateBaudrateDisplays(baud);
    });
    ```

  - connect `bridge.opticalBaudPending` to show the pending state;
  - connect `bridge.baudControlStatus` to show success/error toasts.
- `setConnectedUi(txPort, rxPort, baud)`:
  - `baud` is now the optical rate;
  - update the ribbon/footer to show the optical rate;
  - optionally include the physical rate in the port summary/tooltip:

    ```text
    TX: COM5 • RX: COM6 • UART 115200 fixed • Optical 9600 bps
    ```

- The connect handler (`bridge_client.js:417-448`) still passes `selectedBaud`; the backend interprets it as the optical rate and always opens the physical port at 115200.

**File:** `src/ir_terminal/ui/web/components/view_hardware.html`

- Change the label `Baudrate (bps)` to `Optical Baudrate (bps)`.
- Add a small fixed-physical indicator, for example:
  - `UART LINK: 115200 bps (FIXED)`
  - `OPTICAL RATE • DIP SWITCH`
- Keep `id="baudrate-selector-container"` and `id="baudrate-source-label"` so existing contracts/tests remain valid.
- Add an optional `id="hw-physical-baud"` element for the fixed `115200` label.

**Generated artifact:** `src/ir_terminal/ui/web/index.html`

- Do not edit `index.html` directly.
- After modifying `components/view_hardware.html` and JS, run:

  ```bash
  uv run python scripts/build_modular_web.py
  ```

  This regenerates `index.html` from `components/` per `scripts/README.md:13`.

### 5.7 Mock transport and tests support

**File:** `src/ir_terminal/core/mock_serial.py`

Add optional simulation support so baud control can be tested without hardware:

- Recognise ASCII control writes in `MockSerialPort.write()` before/alongside frame parsing.
- On `SET_BAUD:<n>\n`, optionally update the simulated optical rate and enqueue `BAUD_OK:<n>\n` after a small latency.
- Add a test helper such as `notify_baud(baud: int)` that injects `NOTIFY_BAUD:<baud>\n` into the RX buffer.
- Do not change the existing frame/PING/ACK simulation behaviour.

This keeps unit/integration tests deterministic and allows testing both the command/ack path and the hardware-notification path.

### 5.8 ARQ and binary framing safety

The following rules are mandatory:

1. `SET_BAUD`, `BAUD_OK`, and `NOTIFY_BAUD` are **not** protocol opcodes. They do not call `pack_frame()`, do not consume a sequence number, and do not produce a `Packet`.
2. `BackendBridge` must not call `send_packet()` for baud control.
3. `FileTransferService`, `TxManager`, and `RxManager` are not modified for baud control. Their ARQ state machine continues to see only binary `Packet` objects.
4. The RX demuxer never searches for control prefixes inside a validated binary frame. This prevents accidental matches in file chunks or chat text.
5. Control lines are sent only at frame boundaries. Because all writes are queued through the same `SerialWorker` thread, a line can never be interleaved inside a frame.
6. If a control message arrives during an active transfer, the parser may still deliver it, but the bridge should not apply a user-initiated optical change mid-transfer. `NOTIFY_BAUD` from hardware is logged and reflected in the UI; the ARQ engine itself is not touched.
7. No `close_port()`, `open_ports()`, `create_transport()`, or serial handle reassignment occurs in the baud-change path.
8. Framer errors and control-message parsing are isolated: a demuxer exception must not close the port or reset ARQ.

---

## 6. File-by-File Change Plan

| File | Change |
| :--- | :--- |
| `src/ir_terminal/protocol/constants.py` | Add `PHYSICAL_BAUDRATE`, `DEFAULT_OPTICAL_BAUDRATE`, `OPTICAL_BAUDRATES`; update `SUPPORTED_BAUDRATES`; set physical default for `DEFAULT_BAUDRATE`. |
| `src/ir_terminal/protocol/control_channel.py` (new) | Control-line grammar, prefix constants, `ControlMessage` dataclass, parser/validator helpers. |
| `src/ir_terminal/protocol/stream_demux.py` (new) | Frame-aware RX demultiplexer that returns ordered packets and control lines, preserves partial frames/lines, and updates `FramerStats`. |
| `src/ir_terminal/protocol/framer.py` | Extract reusable frame-at-offset parser; keep public APIs unchanged; optionally expose control counters. |
| `src/ir_terminal/core/serial_worker.py` | Add `control_message_received`; use demuxer in `_drain_rx_buffer`; preserve accumulation/compaction; add safe fallback. |
| `src/ir_terminal/core/serial_manager.py` | Forward `control_message_received`; add `send_control_line()`; keep `current_baud` physical. |
| `src/ir_terminal/core/dual_serial_transport.py` | No functional change required; document that `baudrate` is always physical 115200 in the active path. |
| `src/ir_terminal/core/pyserial_transport.py` | No functional change if unused; ensure future callers pass physical 115200. |
| `src/ir_terminal/core/mock_serial.py` | Add SET_BAUD/BAUD_OK simulation and NOTIFY injection helper for tests. |
| `src/ir_terminal/ui/backend_bridge.py` | Add optical state/signals; route link-up through `_on_link_up`; force physical 115200; add `setOpticalBaud`; handle control lines; extend JSON contracts. |
| `src/ir_terminal/ui/web/js/bridge_client.js` | New default list; baud click sends `setOpticalBaud` when connected; listen to optical signals; separate physical/optical display. |
| `src/ir_terminal/ui/web/components/view_hardware.html` | Rename baud label; add fixed 115200 indicator. |
| `src/ir_terminal/ui/web/index.html` | Regenerate with `scripts/build_modular_web.py`; do not hand-edit. |
| `src/ir_terminal/utils/logger.py` | Add `LogTag.BAUD_CTRL` for traceability. |
| `src/ir_terminal/config.py` | Clarify `DEFAULT_BAUD` as default optical; optionally expose physical constant/property. |
| `docs/PROTOCOL.md` | Add section for ASCII control channel, grammar, examples, and frame-boundary rule. |
| `tests/unit/test_backend_bridge.py` | Update baud expectations; add `setOpticalBaud`, `BAUD_OK`, `NOTIFY_BAUD`, no-disconnect tests. |
| `tests/unit/test_web_ui_dual_com_contract.py` | Update default baud list and assert the new JS/UI contract. |
| `tests/unit/test_system_logging.py` or new parser tests | Add demux/control parser coverage. |
| `tests/integration/test_serial_manager.py` | Add control-line RX emission and physical-baud invariant tests. |
| `tests/unit/test_mock_serial.py` | Add mock baud control response tests. |

---

## 7. State Machines

### 7.1 Optical baud state

```text
                 +--------------------+
                 |   DISCONNECTED     |
                 | selectedBaud only  |
                 +----------+---------+
                            |
                     Connect (physical 115200)
                            v
                 +--------------------+
                 |    LINKED          |
                 | active = desired   |
                 | sends SET_BAUD     |
                 +----------+---------+
                            |
             +--------------+---------------+
             |                              |
     BAUD_OK:<n>                    NOTIFY_BAUD:<n>
             |                              |
             v                              v
     +---------------+              +---------------+
     | CONFIRMED n   |              | HARDWARE n    |
     | clear pending |              | clear pending |
     +-------+-------+              +-------+-------+
             |                              |
             +--------------+---------------+
                            |
                       BAUD_ERR / timeout
                            |
                            v
                 +--------------------+
                 | PREVIOUS RATE      |
                 | no port close      |
                 +--------------------+
```

### 7.2 User baud selection

```text
Baud button click
      |
      +-- not connected --> update selectedBaud only
      |
      +-- connected ------> setOpticalBaud(n)
                                |
                                +-- transfer active? --> reject, keep port
                                |
                                +-- send SET_BAUD:n\n via worker queue
                                |
                                +-- wait for BAUD_OK / NOTIFY_BAUD
```

---

## 8. Compatibility and Migration Notes

### 8.1 Existing tests that must change

The following current expectations are intentionally replaced:

- `test_web_ui_dual_com_contract.py` expects `SUPPORTED_BAUDRATES == (2400, 4800, 9600, 19200)` and `DEFAULT_BAUDRATES = [2400, 4800, 9600, 19200]`.
- `test_backend_bridge.py` expects `get_available_baudrates_defaults` to return `[2400, 4800, 9600, 19200]`.
- `test_backend_bridge.py::test_connect_dual_ports_emits_tx_rx_baud` passes `57600` and expects the same value back from `portConnected` and `getConnectedPorts()["baudrate"]`.
- Tests that pass `115200` as a connection baudrate remain useful as physical-baud tests, but their meaning should be renamed/clarified.

Recommended updated expectations:

- `SUPPORTED_BAUDRATES == (4800, 9600, 19200, 38400)`.
- `DEFAULT_BAUDRATES == [4800, 9600, 19200, 38400]`.
- `getConnectedPorts()` returns `physical_baudrate == 115200` and optical `baudrate == requested`.
- `portConnected` third argument is the optical rate; physical rate is verified through `getConnectedPorts()` and logs.

### 8.2 Launch/CLI compatibility

- `--baud` continues to select the initial **optical** rate.
- The physical UART is not configurable from the UI or CLI for this scope.
- `LaunchConfig.baud` keeps its name for compatibility but is documented as optical.
- `getLaunchConfig()` adds `physical_baudrate` and `default_optical_baudrate` without removing existing keys.

### 8.3 Settings compatibility

`supported_baudrates` in stored settings remains the override source for the UI list. If an old settings file contains `2400`, the UI may show it unless the settings are migrated. Recommended migration: validate settings against `OPTICAL_BAUDRATES` and ignore unsupported legacy entries, or keep `2400` as a settings-only value if hardware support is confirmed.

### 8.4 Persistence of the last optical rate (optional)

If desired, persist the confirmed optical baudrate in `StorageService` under a key such as `last_optical_baudrate`. This is optional and not required by the stated invariants. If implemented, restore it only after a successful link-up, then send `SET_BAUD` to synchronise the STM32.

---

## 9. Test Plan

### 9.1 Unit tests: control parser / demuxer

Add a new test module such as `tests/unit/test_control_channel.py` and extend framer tests.

Cases:

1. Parse `BAUD_OK:19200\n` → kind `BAUD_OK`, baud `19200`.
2. Parse `NOTIFY_BAUD:4800\n` → kind `NOTIFY_BAUD`, baud `4800`.
3. Parse `SET_BAUD:38400\n` → echo/command kind.
4. Parse `BAUD_ERR:unsupported\n` → error kind.
5. Reject malformed values: empty, non-numeric, too long, negative, `0`, `999999`.
6. Tolerate `\r\n`.
7. Reject lines longer than `CONTROL_LINE_MAX_BYTES`.
8. Split a control line across two chunks: no emission until newline, then exactly one emission.
9. Split a binary frame across two chunks: still parsed correctly.
10. Control line before a frame and after a frame in one chunk: order preserved.
11. ASCII bytes that look like `BAUD_OK:` inside a binary frame payload are **not** emitted as control lines.
12. Binary payload containing newline bytes inside a frame is not split into a control line.
13. Noise before a control line does not prevent recognition.
14. Unknown ASCII line is ignored/logged and does not become a packet.
15. Recognised control line does not increment `crc_errors`, `eof_errors`, `len_errors`, or `noise_bytes`.
16. Existing `parse_frames_with_stats()` behaviour is unchanged for frame-only buffers and noise-only buffers.

### 9.2 Unit tests: BackendBridge

Add to `tests/unit/test_backend_bridge.py`:

1. `connectDualPorts(tx, rx, 19200)` calls `SerialManager.connect_ports` with `PHYSICAL_BAUDRATE` (mock/spy), not `19200`.
2. `portConnected` emits the optical rate as its third argument.
3. `getConnectedPorts()` reports `physical_baudrate: 115200` and optical `baudrate`.
4. `setOpticalBaud(19200)` while connected calls `send_control_line("SET_BAUD:19200")` and does **not** call `disconnect_port`, `connect_ports`, or any transport close.
5. `setOpticalBaud` while disconnected only updates the desired rate and sends nothing.
6. `setOpticalBaud` with an unsupported value returns an error and sends nothing.
7. `setOpticalBaud` during `HANDSHAKING`/`TRANSFERRING`/`FINALIZING` is rejected without closing the port.
8. `BAUD_OK:19200` updates `_optical_baud`, emits `opticalBaudChanged(19200)`, clears pending.
9. `BAUD_OK` for a stale generation is ignored.
10. `NOTIFY_BAUD:4800` updates `_optical_baud`, emits `opticalBaudChanged(4800)`, and does not send a `SET_BAUD` echo.
11. `BAUD_ERR` keeps the previous rate and does not close the port.
12. Unknown control line does not affect state.
13. `_on_health_tick` does not send a PING while a baud request is pending.
14. `getLaunchConfig()` contains `physical_baudrate == 115200` and `default_optical_baudrate`.

### 9.3 Integration tests: SerialWorker / SerialManager

1. Connect to `MOCK_COM_LOOPBACK` at 115200; inject `b"BAUD_OK:19200\n"`; assert `control_message_received` emits exactly `"BAUD_OK:19200"`, no `packet_received`, and no framer error/disconnect.
2. Inject a valid binary frame immediately after the control line; assert both the control signal and the packet are delivered in order.
3. Inject a control line split across two `inject_rx_data()` calls; assert one emission after the second call.
4. Inject a frame whose payload contains `b"BAUD_OK:19200\n"`; assert no control message is emitted and the packet payload is exact.
5. Inject `NOTIFY_BAUD:4800\n`; assert no disconnect and no ARQ state change.
6. Verify `manager.current_baud` remains 115200 after control messages.
7. Verify `send_control_line()` writes exactly `SET_BAUD:<baud>\n` and does not produce a binary frame.
8. Verify sending a control line while disconnected produces an error signal but does not raise.

### 9.4 UI contract tests

Add/extend static tests:

1. `DEFAULT_BAUDRATES` contains `[4800, 9600, 19200, 38400]`.
2. `bridge_client.js` contains `setOpticalBaud(` and `opticalBaudChanged.connect`.
3. Baud click handler does not call `connectDualPorts`.
4. HTML contains an optical-baud label and a fixed `115200` indicator.
5. `index.html` was regenerated from components (the existing build/contract tests should still pass).

### 9.5 End-to-end simulated tests

Using `MockSerialPort` or the dual-link broker:

1. Connect Node A and Node B.
2. Send `SET_BAUD:19200`; mock replies `BAUD_OK:19200`; assert both UI state and link remain connected.
3. Inject `NOTIFY_BAUD:4800`; assert state updates without reconnect.
4. Start a small file transfer; assert binary frames still transfer with ARQ after baud control traffic.
5. Assert no `crc_errors`, `eof_errors`, `len_errors` are introduced by control lines.

### 9.6 Manual hardware acceptance checklist

1. Open the app, select a COM port, connect. Confirm the physical UART is 115200 in the log and the UI shows `115200 fixed`.
2. Select `19200` while connected. Confirm:
   - the COM port is not closed or reopened;
   - the log shows `SET_BAUD:19200` TX and `BAUD_OK:19200` RX;
   - the footer/selector updates to `19200`.
3. Flip SW1/SW2 on the STM32 to change the optical rate. Confirm:
   - `NOTIFY_BAUD:<baud>` appears in the log;
   - the UI updates without disconnect or reconnect;
   - the selected button matches the hardware rate.
4. While a file transfer is active, attempt a baud change. Confirm it is rejected/deferred and the transfer completes with matching CRC-32.
5. Disconnect and reconnect after a baud change. Confirm the physical port opens at 115200 and the app re-sends the desired `SET_BAUD` on link-up.
6. Unplug the USB cable while connected. Confirm the existing read-error path reports the failure and does not enter a reopen loop.

---

## 10. Risks and Mitigations

| Risk | Impact | Mitigation |
| :--- | :--- | :--- |
| Control-line scanner matches bytes inside a binary frame | ARQ data corruption / false state changes | Frame-aware demux; only scan outside validated frames; explicit test with control tokens in payload |
| Control line split across `read()` chunks | Lost notifications | Bounded partial-line buffer retained in `remaining`; split-chunk tests |
| `_rx_consumed` offset miscalculated after mixed frame/control stream | Buffer replay, duplicated packets, or buffer growth | Demux returns explicit `consumed`; regression test with 60+ mixed events |
| Optical baud change during file transfer | Lost chunks / ARQ failure | Reject/defer user change during active transfer; hardware NOTIFY is logged but does not mutate ARQ |
| Stale `BAUD_OK` overwrites a newer `NOTIFY_BAUD` | UI shows wrong rate | Request generation counter; `NOTIFY_BAUD` always wins |
| Physical port reopened accidentally | Connection drop, ARQ reset | Code review invariant; tests assert no `disconnect/connect` calls in `setOpticalBaud`; keep transport lifecycle separate |
| Recognised control bytes counted as noise | Inflated loss/noise telemetry | Exclude recognised control bytes from `noise_bytes`; add control counters |
| Old settings contain unsupported `2400` | UI offers a rate firmware may not support | Validate/migrate settings against `OPTICAL_BAUDRATES`; or document 2400 as legacy |
| `index.html` and components drift | UI changes not visible / contract test failures | Modify components only; run `build_modular_web.py`; commit generated artifact |

---

## 11. Implementation Sequence

### Phase 1 — Protocol primitives

1. Add physical/optical constants.
2. Add `control_channel.py` parser and tests.
3. Extract reusable frame parser helper from `framer.py` without changing public APIs.
4. Add `stream_demux.py` and its unit tests.

### Phase 2 — Worker and manager

1. Add `control_message_received` to `SerialWorker`.
2. Integrate the demuxer into `_drain_rx_buffer()`.
3. Add `control_message_received` forwarding to `SerialManager`.
4. Add `SerialManager.send_control_line()`.
5. Add integration tests for split lines, mixed frames/lines, and physical baud invariance.

### Phase 3 — Bridge state machine

1. Add optical state, pending state, generation counter, and signals.
2. Route `ports_connected` through `_on_link_up`.
3. Force physical `115200` in `connectDualPorts`.
4. Add `setOpticalBaud`.
5. Add `_on_control_message`.
6. Extend `getConnectedPorts` and `getLaunchConfig`.
7. Add health-tick gating.
8. Add bridge unit tests.

### Phase 4 — UI

1. Update `bridge_client.js` defaults, click handler, and signal listeners.
2. Update `view_hardware.html` labels and fixed-baud indicator.
3. Regenerate `index.html` with `build_modular_web.py`.
4. Update UI contract tests.

### Phase 5 — Mock, docs, and hardware validation

1. Extend `MockSerialPort` for `SET_BAUD` / `BAUD_OK` / `NOTIFY_BAUD`.
2. Add end-to-end simulated tests.
3. Update `docs/PROTOCOL.md` with the ASCII control channel.
4. Run the full test suite, mypy, and ruff.
5. Perform the manual hardware checklist in §9.6.

---

## 12. Acceptance Criteria

The change is complete when all of the following are true:

1. A physical serial connection is always opened at `115200 8-N-1`; no UI action can change the physical COM baudrate.
2. Selecting `4800`, `9600`, `19200`, or `38400` on an open connection sends `SET_BAUD:<baud>\n` through the existing transport and never closes/reopens the port.
3. `BAUD_OK:<baud>\n` and `NOTIFY_BAUD:<baud>\n` are received, parsed, and reflected in the UI state.
4. Binary framing and ARQ continue to operate with byte-identical `SOF 0xAA55` / `CRC-32` / `EOF 0x55AA` frames; control lines never appear as `Packet` objects and never alter sequence numbers.
5. Control messages split across chunks and mixed with binary frames are handled correctly.
6. A file transfer remains intact when baud control traffic is present; user-initiated baud changes during an active transfer are rejected or deferred.
7. Existing test suite, new unit/integration tests, `ruff`, and `mypy` pass.
8. No comments are added inside source files; documentation updates live in Markdown files such as this plan and `docs/PROTOCOL.md`.

---

## 13. Open Decisions / Recommended Defaults

| Question | Recommended default |
| :--- | :--- |
| Should `2400` remain selectable? | No, unless firmware explicitly supports it. Use the four requested rates. |
| Should `SET_BAUD` be sent automatically on connect? | Yes, so the STM32 matches the UI selection; send after the link-up event. |
| Should the UI update optimistically or wait for `BAUD_OK`? | Show pending, keep the previous active rate until `BAUD_OK`/`NOTIFY_BAUD`. |
| Should baud changes be allowed during file transfer? | No; reject or defer until `IDLE`/`COMPLETED`/`FAILED`/`ABORTED`. |
| Should `NOTIFY_BAUD` be acknowledged by the App? | No. It is a hardware notification; do not echo `SET_BAUD` back. |
| Should `SET_BAUD` echo from the STM32 be parsed? | Parse and ignore/log it; never treat it as a new command. |
| Should the optical rate be persisted across app restarts? | Optional; if implemented, restore only after link-up and re-send `SET_BAUD`. |
| Should recognized control lines count in telemetry throughput? | They may count as raw RX bytes, but must not count as frame errors or packet loss. Optionally add separate control counters. |
| Should the bridge expose a control-status signal? | Recommended: `opticalBaudChanged(int)` is required; `opticalBaudPending(int)` and `baudControlStatus(str, str)` improve UX. |

---

## 14. Summary of Root Causes

1. **Physical/optical baudrate conflation:** `bridge_client.js:448` → `backend_bridge.py:503` → `serial_manager.py:189` → `serial_worker.py:226-235` → `dual_serial_transport.py:62-72` passes the UI optical rate into the physical serial handle.
2. **Forced reopen on every connect:** `SerialWorker.open_ports()` calls `close_port()` first (`serial_worker.py:216`), so any baud selection that reaches this path drops the link and resets RX/ARQ state.
3. **No control-channel RX path:** `framer.py:178-196` discards non-frame ASCII as noise, so `BAUD_OK` and `NOTIFY_BAUD` never reach `BackendBridge`.
4. **No optical state in the bridge:** `BackendBridge` has no `_optical_baud`, no `setOpticalBaud` slot, and no signal for hardware notifications.
5. **UI click only changes a local variable:** `bridge_client.js:404-414` never sends a baud command and never distinguishes connected vs. disconnected behavior.
6. **Stale supported-rate list:** `constants.py:77-84` and `bridge_client.js:5` omit `38400` and include `2400`.

The plan above fixes these causes without changing the binary protocol, the CRC-32/EOF framing rules, or the Stop-and-Wait ARQ state machine.
