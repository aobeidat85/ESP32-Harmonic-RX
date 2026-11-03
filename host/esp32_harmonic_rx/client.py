# SPDX-License-Identifier: GPL-3.0-only
"""CRC-checked client for the included USB bridge example."""

import serial, time, zlib
import numpy as np


class CaptureIntegrityError(RuntimeError):
    """A complete serial frame was read, but its CRC failed. The frame is discarded."""


class ReceiverClient:
    def resynchronize(self):
        """Drain a bounded pending frame, then establish a fresh INFO boundary."""
        deadline = time.perf_counter() + 2
        quiet_since = None
        while time.perf_counter() < deadline:
            waiting = self.s.in_waiting
            if waiting:
                self.s.read(min(waiting, 65536))
                quiet_since = None
            else:
                if quiet_since is None:
                    quiet_since = time.perf_counter()
                if time.perf_counter() - quiet_since >= 0.06:
                    break
                time.sleep(0.01)
        else:
            raise RuntimeError("Serial frame boundary could not be re-established")
        self.s.reset_input_buffer()
        info = self.command("INFO")
        if not info.startswith("VEVOR_FSK "):
            raise RuntimeError("Receiver did not resynchronize; reconnect required")

    def __init__(self, reset=True, port="COM10"):
        self.s = serial.Serial(port, 1000000, timeout=3, write_timeout=2)
        try:
            self.s.dtr = False
            if reset:
                self.s.rts = True
                time.sleep(0.1)
                self.s.rts = False
                time.sleep(3)
            self.s.reset_input_buffer()
            self.info = self.command("INFO")
            if not self.info.startswith("VEVOR_FSK "):
                raise RuntimeError(
                    "The board is not running the VEVOR receiver firmware"
                )
        except BaseException:
            self.s.close()
            raise

    def command(self, cmd):
        self.s.write((cmd + "\n").encode())
        line = self.s.readline().decode(errors="replace").strip()
        if not line or line.startswith("ERR"):
            raise RuntimeError(cmd + " " + repr(line))
        return line

    def capture(self, ms=120):
        start = time.perf_counter()
        line = self.command("CAP " + str(ms))
        header_received = time.perf_counter()
        fields = line.split()
        if len(fields) != 6 or fields[0] != "FSK":
            raise RuntimeError("Unexpected capture header " + line)
        count = int(fields[1])
        if not 0 < count <= 6144:
            raise RuntimeError("Invalid observation count")
        size = count * 12
        raw = self.s.read(size)
        if len(raw) != size:
            raise RuntimeError("Short capture")
        if zlib.crc32(raw) & 0xFFFFFFFF != int(fields[2], 16):
            self.resynchronize()
            raise CaptureIntegrityError(
                "Capture CRC mismatch; complete frame discarded"
            )
        rows = np.frombuffer(
            raw, dtype=[("us", "<u4"), ("p0", "<f4"), ("p1", "<f4")]
        ).copy()
        elapsed = int(fields[3])
        window_start = header_received - elapsed / 1e6 - (len(line) + 1) * 10 / 1000000
        return dict(
            raw=raw,
            rows=rows,
            count=count,
            elapsed_us=elapsed,
            failures=int(fields[4]),
            gain=int(fields[5]),
            host_start=start,
            host_end=time.perf_counter(),
            estimated_window_host_start=window_start,
        )

    def close(self):
        self.s.close()
