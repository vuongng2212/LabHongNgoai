"""Cross-platform PTY-based virtual dual COM bridge for Linux and macOS.

Creates four virtual serial endpoints and cross-routes them:
    Node A TX (/tmp/LINK_A_TX) ===> Node B RX (/tmp/LINK_B_RX)
    Node B TX (/tmp/LINK_B_TX) ===> Node A RX (/tmp/LINK_A_RX)
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import FrameType


class VirtualDualComBridge:
    """Manages 4 PTY file descriptors cross-connected into two simplex links."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path("/tmp")
        self.stop_event = threading.Event()
        self.threads: list[threading.Thread] = []
        self.processes: list[subprocess.Popen[bytes]] = []

        self.m_a_tx = -1
        self.s_a_tx = -1
        self.m_a_rx = -1
        self.s_a_rx = -1
        self.m_b_tx = -1
        self.s_b_tx = -1
        self.m_b_rx = -1
        self.s_b_rx = -1

        self.path_a_tx = self.base_dir / "LINK_A_TX"
        self.path_a_rx = self.base_dir / "LINK_A_RX"
        self.path_b_tx = self.base_dir / "LINK_B_TX"
        self.path_b_rx = self.base_dir / "LINK_B_RX"

        self.bytes_a_to_b = 0
        self.bytes_b_to_a = 0

    def start(self) -> None:
        """Opens PTY handles, creates symlinks, and starts forwarding threads."""
        if not hasattr(os, "openpty"):
            raise OSError("PTY virtual serial ports are only supported on POSIX systems")

        self.m_a_tx, self.s_a_tx = os.openpty()
        self.m_a_rx, self.s_a_rx = os.openpty()
        self.m_b_tx, self.s_b_tx = os.openpty()
        self.m_b_rx, self.s_b_rx = os.openpty()

        self._create_symlink(self.path_a_tx, os.ttyname(self.s_a_tx))
        self._create_symlink(self.path_a_rx, os.ttyname(self.s_a_rx))
        self._create_symlink(self.path_b_tx, os.ttyname(self.s_b_tx))
        self._create_symlink(self.path_b_rx, os.ttyname(self.s_b_rx))

        t_ab = threading.Thread(
            target=self._forward_loop,
            args=(self.m_a_tx, self.m_b_rx, "A_TX -> B_RX"),
            name="relay-a-to-b",
            daemon=True,
        )
        t_ba = threading.Thread(
            target=self._forward_loop,
            args=(self.m_b_tx, self.m_a_rx, "B_TX -> A_RX"),
            name="relay-b-to-a",
            daemon=True,
        )

        t_ab.start()
        t_ba.start()
        self.threads.extend([t_ab, t_ba])

    def _create_symlink(self, link_path: Path, target_dev: str) -> None:
        try:
            if link_path.is_symlink() or link_path.exists():
                link_path.unlink()
            link_path.symlink_to(target_dev)
        except OSError as exc:
            sys.stderr.write(
                f"Warning: could not create symlink {link_path} -> {target_dev}: {exc}\n"
            )

    def _forward_loop(self, src_fd: int, dst_fd: int, channel_name: str) -> None:
        while not self.stop_event.is_set():
            try:
                data = os.read(src_fd, 4096)
                if not data:
                    time.sleep(0.01)
                    continue
                os.write(dst_fd, data)
                if "A_TX" in channel_name:
                    self.bytes_a_to_b += len(data)
                else:
                    self.bytes_b_to_a += len(data)
            except OSError:
                if self.stop_event.is_set():
                    break
                time.sleep(0.01)

    def launch_nodes(self, extra_args: list[str] | None = None) -> None:
        """Launches Node A and Node B connected to the virtual dual COM ports."""
        cmd_a = [
            "uv",
            "run",
            "python",
            "-m",
            "ir_terminal",
            "--instance",
            "A",
            "--tx-port",
            str(self.path_a_tx),
            "--rx-port",
            str(self.path_a_rx),
            "--baud",
            "115200",
            "--auto-connect",
            "--window-pos",
            "left",
        ] + (extra_args or [])

        cmd_b = [
            "uv",
            "run",
            "python",
            "-m",
            "ir_terminal",
            "--instance",
            "B",
            "--tx-port",
            str(self.path_b_tx),
            "--rx-port",
            str(self.path_b_rx),
            "--baud",
            "115200",
            "--auto-connect",
            "--window-pos",
            "right",
        ] + (extra_args or [])

        sys.stdout.write("Launching Node A...\n")
        proc_a = subprocess.Popen(cmd_a)
        self.processes.append(proc_a)
        time.sleep(1.0)

        sys.stdout.write("Launching Node B...\n")
        proc_b = subprocess.Popen(cmd_b)
        self.processes.append(proc_b)

    def close(self) -> None:
        """Stops threads, terminates children, and removes symlinks."""
        self.stop_event.set()

        for proc in self.processes:
            try:
                proc.terminate()
                proc.wait(timeout=2.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        for path in (self.path_a_tx, self.path_a_rx, self.path_b_tx, self.path_b_rx):
            try:
                if path.is_symlink() or path.exists():
                    path.unlink()
            except OSError:
                pass

        for fd in (
            self.m_a_tx,
            self.s_a_tx,
            self.m_a_rx,
            self.s_a_rx,
            self.m_b_tx,
            self.s_b_tx,
            self.m_b_rx,
            self.s_b_rx,
        ):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Virtual Dual COM PTY Bridge & Runner")
    parser.add_argument("--launch", action="store_true", help="Launch Node A and Node B apps")
    args, unknown = parser.parse_known_args()

    bridge = VirtualDualComBridge()
    try:
        bridge.start()
    except OSError as exc:
        sys.stderr.write(f"Error initializing PTY bridge: {exc}\n")
        return 1

    banner = f"""
================================================================================
          IR-OWC DUAL-PORT COM VIRTUAL PIPELINE (CROSS-ROUTED PTY)
================================================================================
Node A Endpoints:
  TX Port (Push to HW):    {bridge.path_a_tx} ({os.ttyname(bridge.s_a_tx)})
  RX Port (Pull from HW):  {bridge.path_a_rx} ({os.ttyname(bridge.s_a_rx)})

Node B Endpoints:
  TX Port (Push to HW):    {bridge.path_b_tx} ({os.ttyname(bridge.s_b_tx)})
  RX Port (Pull from HW):  {bridge.path_b_rx} ({os.ttyname(bridge.s_b_rx)})

Data Routing Topologies:
  [Node A TX] ===> os.read(master) ===> os.write(slave) ===> [Node B RX]
  [Node B TX] ===> os.read(master) ===> os.write(slave) ===> [Node A RX]
================================================================================
"""
    sys.stdout.write(banner)

    def handle_signal(sig: int, frame: FrameType | None) -> None:
        sys.stdout.write("\nShutting down virtual COM bridge...\n")
        bridge.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    if args.launch:
        bridge.launch_nodes(unknown)

    sys.stdout.write("Bridge running. Press Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        handle_signal(signal.SIGINT, None)

    return 0


if __name__ == "__main__":
    sys.exit(main())
