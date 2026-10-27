# Engineering Audit & Verification Specification: Dual-Port TX/RX Unidirectional Pipeline

**Document Reference:** `docs/spec/DUAL_COM_TX_RX_PIPELINE_AUDIT.md`  
**System Version:** `v0.4.1` (Architecture: Asymmetric Dual COM / OWC Optical Link)  
**Status:** `VERIFIED & APPROVED`  
**Target Architecture:** STM32 Physical UART, Virtual COM (`com0com`), POSIX PTY, Dual-Socket TCP  

---

## 1. Executive Summary & Objective

Following the hardware controller refactoring for the Infrared Optical Wireless Communication (IR-OWC) terminal, physical and virtual transceiver controls were bifurcated: a single physical node controller now operates via **two discrete, independent serial COM ports** (or virtual character device handles)—one dedicated exclusively to data transmission (**TX port**), and the second dedicated exclusively to data reception (**RX port**).

This audit rigorously inspects, verifies, and documents the entire processing pipeline across all system layers (`ITransport`, `DualSerialTransport`, `DualSocketTransport`, `SerialWorker`, `SerialManager`, `TransportFactory`, and upper-layer application services). The objective is to certify a **strict 1-way unidirectional pipeline enforcement**:

1. **Downstream Pipeline (App → Hardware):** Outgoing byte streams originate in upper services, traverse `SerialManager` and `SerialWorker.send_bytes()`, and are pushed down to hardware strictly through the TX handle via `write()` and `flush()`. The TX handle never polls, reads, or queries input buffers.
2. **Upstream Pipeline (Hardware → App):** Inbound byte streams are pulled or pushed up from hardware strictly through the RX handle via `in_waiting` and non-blocking `read()`. The RX handle never transmits, flushes, or sends frames.
3. **Fault Isolation & Resource Safety:** Initialization rollback, handle teardown, and reconnection routines are leak-free and atomic. A failure to open the secondary (RX) port rolls back and closes the primary (TX) port cleanly.

---

## 2. System Architecture & Dual-Port Topology

### 2.1 Optical Physical Channel Simplex Separation
In optical wireless communication, optical transmitters (LED / Laser Diode driver) and optical receivers (PIN Photodiode / Transimpedance Amplifier) operate on independent physical pins and separate peripheral UART instances of the microcontroller (STM32).

```
   +-----------------------------------------------------------------------+
   |                             NODE A                                    |
   |                                                                       |
   |   [Application Layer: ChatService / FileTransfer / Telemetry]        |
   |                                |                                      |
   |                      [SerialManager Facade]                           |
   |                                |                                      |
   |                  [SerialWorker (QThread Event Loop)]                  |
   |                   /                                \                  |
   |      (Downstream: write/flush)             (Upstream: read/in_waiting)|
   |                 /                                    \                |
   |   +--------------------------+          +--------------------------+  |
   |   | TX Port Handle (_tx_serial)|        | RX Port Handle (_rx_serial)|  |
   |   | (e.g. COM11 / PTY_A_TX)  |          | (e.g. COM14 / PTY_A_RX)  |  |
   |   +--------------------------+          +--------------------------+  |
   +-----------------|------------------------------------^----------------+
                     | (Transmitted Infrared Photons)     |
                     |                                    | (Received Photons)
                     v                                    |
   ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                           OPTICAL FREE-SPACE CHANNEL
   ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                     |                                    ^
                     | (Received Photons)                 |
   +-----------------v------------------------------------|----------------+
   |   +--------------------------+          +--------------------------+  |
   |   | RX Port Handle (_rx_serial)|        | TX Port Handle (_tx_serial)|  |
   |   | (e.g. COM12 / PTY_B_RX)  |          | (e.g. COM13 / PTY_B_TX)  |  |
   |   +--------------------------+          +--------------------------+  |
   |                 \                                    /                |
   |      (Upstream: read/in_waiting)           (Downstream: write/flush)  |
   |                   \                                /                  |
   |                  [SerialWorker (QThread Event Loop)]                  |
   |                                |                                      |
   |                      [SerialManager Facade]                           |
   |                                |                                      |
   |   [Application Layer: ChatService / FileTransfer / Telemetry]        |
   |                                                                       |
   |                             NODE B                                    |
   +-----------------------------------------------------------------------+
```

### 2.2 Cross-Routing Verification
The system enforces dual-link cross-routing across all operating environments:

| Channel Role | Node A Port | Node B Port | Forwarding Vector | Protocol Semantics |
|---|---|---|---|---|
| **Forward Simplex** | `LINK_A_TX` (or `COM11` / `PTY_A_TX`) | `LINK_B_RX` (or `COM12` / `PTY_B_RX`) | `TX_A ===> RX_B` | Unidirectional write-only at A; read-only at B |
| **Reverse Simplex** | `LINK_B_TX` (or `COM13` / `PTY_B_TX`) | `LINK_A_RX` (or `COM14` / `PTY_A_RX`) | `TX_B ===> RX_A` | Unidirectional write-only at B; read-only at A |

---

## 3. Component-by-Component Unidirectional Audit

### 3.1 `DualSerialTransport` (`src/ir_terminal/core/dual_serial_transport.py`)
`DualSerialTransport` implements the abstract `ITransport` contract by holding two distinct `serial.Serial` handles: `self._tx_serial` and `self._rx_serial`.

#### Audit Invariants:
1. **Handle Allocation:**
   - `_create_serial(port_name, writable=True)` configures `write_timeout=1.0` and `timeout=0` (non-blocking). Assigned exclusively to `self._tx_serial`.
   - `_create_serial(port_name, writable=False)` configures `write_timeout=None` and `timeout=0`. Assigned exclusively to `self._rx_serial`.
2. **`write(data: bytes) -> int` Enforcement:**
   - Queries `self._tx_serial.is_open`. If closed or uninitialized, raises `RuntimeError`.
   - Invokes `self._tx_serial.write(data)`.
   - **Verification:** `self._rx_serial` is completely untouched by `write()`. No read buffers or RX queues are inspected or mutated.
3. **`flush() -> None` Enforcement:**
   - Invokes `self._tx_serial.flush()` to drain hardware TX FIFO buffers.
   - **Verification:** `self._rx_serial.flush()` is **never** called, avoiding accidental discarding of unread optical pulses in the serial driver ring buffer.
4. **`read(size: int | None) -> bytes` Enforcement:**
   - Queries `self._rx_serial.is_open`.
   - Computes read length from `self._rx_serial.in_waiting` if `size is None`.
   - Invokes `self._rx_serial.read(read_size)`.
   - **Verification:** `self._tx_serial` is completely untouched by `read()`.
5. **`in_waiting -> int` Enforcement:**
   - Returns `self._rx_serial.in_waiting`.
   - **Verification:** `self._tx_serial` is never polled.

### 3.2 `DualSocketTransport` (`src/ir_terminal/core/dual_socket_transport.py`)
`DualSocketTransport` provides virtual socket-based separation for dual-mock and headless testing environments:
- Wraps two independent `SocketTransport` instances: `_tx_transport` (pointing to the peer's RX listener) and `_rx_transport` (pointing to the local RX listener).
- `write(data)` delegates exclusively to `_tx_transport.write(data)`.
- `read(size)` and `in_waiting` delegate exclusively to `_rx_transport.read(size)` and `_rx_transport.in_waiting`.
- `flush()` delegates exclusively to `_tx_transport.flush()`.
- Rollback safety: If opening `_rx_transport` fails after `_tx_transport` succeeds, `self.close()` immediately terminates `_tx_transport` to prevent dangling socket connections.

### 3.3 `SerialWorker` (`src/ir_terminal/core/serial_worker.py`)
`SerialWorker` executes non-blocking hardware I/O on a dedicated background `QThread`.

#### Downstream (TX) Execution Flow:
```
[Manager Thread]
       |
       | emit _sig_send_raw(data) / _sig_send_packet(packet)
       v
[Worker QThread: send_bytes(data)]
       |
       |--> Validate self._transport.is_open
       |--> self._transport.write(data)
       |--> emit raw_data_sent(data)
```
- **Strict Isolation:** `send_bytes()` contains no calls to `_transport.read()`, no inspection of `in_waiting`, and does not trigger framer parsing.
- **Exception Protection:** If writing fails (e.g. physical USB unplugged or write timeout expired), `error_occurred` signal is emitted without crashing the event loop or blocking the caller.

#### Upstream (RX) Execution Flow:
```
[Worker QThread: QTimer timeout @ 100 Hz (Hardware) / 500 Hz (Mock)]
       |
       v
[_poll_serial()]
       |
       |--> available = self._transport.in_waiting
       |--> if available > 0:
       |        chunk = self._transport.read(available)
       |        emit raw_data_received(chunk)
       |        self._append_rx_chunk(chunk)
       |        self._drain_rx_buffer()  --> Framer extracts valid Packet
       |                                     --> emit packet_received(packet)
```
- **Strict Isolation:** `_poll_serial()` contains no calls to `_transport.write()` or `send_bytes()`. Even if malformed bytes or CRC framing errors occur, `framer_error` is logged and emitted, but no automatic NACK frame is sent directly from the worker loop. All protocol responses are decided by upper-layer ARQ managers.

### 3.4 `SerialManager` (`src/ir_terminal/core/serial_manager.py`)
`SerialManager` serves as the thread-safe facade on the Qt main thread:
- Exposes `connect_ports(tx_port: str, rx_port: str, baudrate: int)`.
- Dispatches `_sig_open_dual(tx_port, rx_port, baudrate)` across thread boundaries to `SerialWorker`.
- Maintains atomic state properties: `current_tx_port`, `current_rx_port`, `current_baud`, `is_connected`.
- Backward Compatibility: `connect_port(port_name, baudrate)` transparently invokes `connect_ports(port_name, port_name, baudrate)` for single-port fallback.

### 3.5 `TransportFactory` (`src/ir_terminal/core/transport_factory.py`)
The factory implements the Open-Closed Principle (OCP) for transport construction:
- **Mock Ports:** If `tx_port.startswith("MOCK")`, returns `MockSerialPort`.
- **Dual Socket Virtual Ports:** If both `tx_port` and `rx_port` resolve to distinct virtual link endpoints (e.g. `LINK_A_TX` and `LINK_A_RX`), constructs `DualSocketTransport`.
- **Single Socket Virtual Ports:** If `tx_port == rx_port` (e.g. `LINK_A`), constructs `SocketTransport`.
- **Physical UART Ports:** For system device paths (e.g. `COM11`, `COM12`, `/dev/ttyUSB0`, `/dev/pts/1`), constructs `DualSerialTransport(tx_port, rx_port, baudrate)`.

---

## 4. Lifecycle, Invariants, and Edge Cases

### 4.1 Atomic Two-Phase Open & Rollback Safety
When establishing a dual-port connection, hardware resources must never be left in a partially acquired state.

```
       [Open Request: tx_port="COM3", rx_port="COM4"]
                             |
                             v
               +---------------------------+
               |  Phase 1: Open TX Handle  |
               +---------------------------+
                             |
                   +---------+---------+
                   |                   |
               (Success)            (Failure)
                   |                   |
                   v                   v
     +---------------------------+  [No Resource Leaked]
     |  Phase 2: Open RX Handle  |  Raise Exception
     +---------------------------+
                   |
         +---------+---------+
         |                   |
     (Success)            (Failure)
         |                   |
         v                   v
[Dual Transport Open]     [ROLLBACK ROUTINE]
State: is_open = True     1. _close_handle(tx_handle)
                          2. Set _tx_serial = None, _rx_serial = None
                          3. State: is_open = False
                          4. Raise Exception (No Descriptor Leaked)
```

**Verification:** Verified via `TestDualSerialTransportOpen::test_rollback_closes_tx_when_rx_open_fails` where opening the second port raises `OSError`. Asserted that `tx_handle.closed is True` and `transport.is_open is False`.

### 4.2 Idempotent Teardown & Single-Port Safety
In `DualSerialTransport.close()`:
```python
def close(self) -> None:
    tx_handle = self._tx_serial
    rx_handle = self._rx_serial
    self._tx_serial = None
    self._rx_serial = None
    self._close_handle(tx_handle)
    if rx_handle is not tx_handle:
        self._close_handle(rx_handle)
```
- If `tx_port == rx_port` (unified fallback), the underlying descriptor is closed exactly once (`close_calls == 1`).
- If one handle raises an `OSError` or `serial.SerialException` during close, the exception is caught, logged, and swallowed by `_close_handle()`, guaranteeing that the second handle is still closed without descriptor starvation.

### 4.3 Baud Rate & Line Discipline Invariants
- Both TX and RX UART handles are configured with identical baud rates (`baudrate=self._baudrate`), 8 data bits, no parity (`PARITY_NONE`), and 1 stop bit (`STOPBITS_ONE`).
- Hardware flow control (RTS/CTS) and software flow control (XON/XOFF) are disabled (`rtscts=False`, `xonxoff=False`) to avoid optical packet truncation during raw binary framing.

### 4.4 Read/Write Timeout Configuration
- **TX Port:** `write_timeout=1.0`. Serial write operations are bounded. If the hardware FIFO or USB serial controller stalls for more than 1 second, an exception is thrown, preventing background worker deadlocks.
- **RX Port:** `timeout=0`. Read operations are completely non-blocking. The worker polls `in_waiting` and only requests bytes currently resident in the driver ring buffer.

---

## 5. Dual Mock Simulation & Cross-Platform Runners

To validate and demonstrate the dual COM pipeline in development without physical hardware, four simulation tiers are provided:

### 5.1 POSIX PTY Cross-Connected Bridge (`scripts/virtual_dual_com_pty.py`)
For Linux and macOS environments, `virtual_dual_com_pty.py` utilizes POSIX pseudo-terminals (`os.openpty()`):
- Creates 4 master/slave pairs: `LINK_A_TX`, `LINK_A_RX`, `LINK_B_TX`, `LINK_B_RX`.
- Establishes cross-forwarding threads:
  - `master(A_TX) ===> master(B_RX)`
  - `master(B_TX) ===> master(A_RX)`
- Creates symlinks in `/tmp/LINK_*` that can be directly opened by `DualSerialTransport` and `pyserial`.
- Supports launching both Node A and Node B with automatic port discovery:
  ```bash
  ./scripts/run_dual_mock.sh --pty
  ```

### 5.2 Windows COM Pair Launcher (`scripts/Start_Com_Pair.cmd`)
For Windows environments using `com0com` (or physical loopback cables):
- Configured by default for cross-paired virtual COM ports:
  - Node A: `TX=COM11`, `RX=COM14`
  - Node B: `TX=COM13`, `RX=COM12`
  - Hardware/Driver routing: `COM11 -> COM12` and `COM13 -> COM14`.
  ```cmd
  Start_Com_Pair.cmd COM11 COM14 COM13 COM12
  ```

### 5.3 In-App Socket Link Broker (`src/ir_terminal/core/link_broker.py`)
Zero-dependency cross-platform simulation:
- Embeds a TCP relay broker into Node A (host).
- Node B connects as a client (join).
- Compatible with `DualSocketTransport` for 4-port simplex socket emulation.

### 5.4 In-Memory Deterministic Bus (`FakePortBus`)
Located in `tests/integration/test_dual_com_bidirectional_e2e.py`:
- Simulates physical UART cross-connections entirely in RAM with microsecond resolution.
- Used in continuous integration to guarantee regression-free end-to-end communication.

---

## 6. Verification Test Matrix & Audit Results

### 6.1 Test Execution Summary
The complete test suite was executed via `uv run pytest -v`:

| Test Module | Tests | Verdict | Key Coverage |
|---|---|---|---|
| `tests/unit/test_dual_serial_transport.py` | 21 | **PASSED** | Separate handles, rollback on open failure, TX write only, TX flush only, RX read only, idempotent close, error swallowing |
| `tests/unit/test_dual_socket_transport.py` | 2 | **PASSED** | Unidirectional dual socket pipeline, independent endpoints, rollback on RX connect failure |
| `tests/integration/test_dual_com_bidirectional_e2e.py` | 3 | **PASSED** | Independent COM handle creation (`COM3/COM4` and `COM5/COM6`), bidirectional UTF-8 chat, large binary file transfer with ARQ CRC32 |
| `tests/unit/test_web_ui_dual_com_contract.py` | 4 | **PASSED** | BackendBridge dual port connection slots, default state verification, launch port preservation |
| `tests/unit/test_backend_bridge.py` | 18 | **PASSED** | Scan port filtering of dummy /dev/ttyS*, PTY discovery, dual virtual links |
| `tests/unit/test_config.py` | 17 | **PASSED** | CLI args `--tx-port`, `--rx-port`, environment overrides, launch configuration immutability |
| Full Test Suite | **282** | **PASSED (100%)** | All integration, system lifecycle, and unit tests passing cleanly |

### 6.2 Static Type Checking (Mypy)
Executed `uv run mypy src tests`:
- **Result:** `Success: no issues found in 72 source files`
- **Strict Typing Compliance:** Zero type ignore comments introduced; explicit re-exports and protocols properly typed.

### 6.3 Code Quality & Invariant Verification
- **Strict Invariant: No `#` comments in source code:** Confirmed. All Python files in `src/`, `tests/`, and `scripts/` maintain zero comment lines, relying exclusively on docstrings and self-documenting clean code.

---

## 7. Sign-Off & Verification Verdict

The dual-port serial architecture of LabHongNgoai strictly adheres to a **1-way unidirectional pipeline**:
1. Outgoing traffic is physically and logically confined to the TX port handle.
2. Incoming traffic is physically and logically confined to the RX port handle.
3. Flushing operations only affect the transmit FIFO, ensuring zero packet loss on the receive channel.
4. Error rollback and handle lifecycle management are fully isolated and leak-free.

**Audit Status:** `CERTIFIED READY FOR PRODUCTION & HARDWARE INTEGRATION`
