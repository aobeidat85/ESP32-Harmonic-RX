# SPDX-License-Identifier: GPL-3.0-only
"""CRC-checked client for the included USB bridge example."""

import serial, time, zlib
import numpy as np


class CaptureIntegrityError(RuntimeError):
    """A complete serial frame was read, but its CRC failed. The frame is discarded."""

    def __init__(self, message, *, raw=None, details=None):
        super().__init__(message)
        self.raw, self.details = raw, details or {}


class ReceiverProtocolError(RuntimeError):
    """The serial boundary is uncertain; reset/reconnect before another command."""

    def __init__(self, message, *, raw=None, details=None):
        super().__init__(message)
        self.raw, self.details = raw, details or {}


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

    def __init__(self, reset=True, port="COM10", preferred_baud=500000):
        if preferred_baud not in (500000, 1000000):
            raise ValueError("Supported transfer rates are 500000 and 1000000")
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
            if preferred_baud != 1000000 and "baud_switch=1" in self.info.split():
                if self.command("BAUD " + str(preferred_baud)) != "OK":
                    raise ReceiverProtocolError("Baud switch was not acknowledged")
                self.s.baudrate = preferred_baud
                self.info = self.command("INFO")
                if (
                    not self.info.startswith("VEVOR_FSK ")
                    or "baud=" + str(preferred_baud) not in self.info.split()
                ):
                    raise ReceiverProtocolError(
                        "Receiver did not confirm the transfer rate"
                    )
            self.snapshot_samples = next(
                (int(t[2:]) for t in self.info.split() if t.startswith("N=")), 128
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

    def capture(self, ms=120, monitor=False):
        start = time.perf_counter()
        try:
            line = self.command(("MON " if monitor else "CAP ") + str(ms))
        except RuntimeError as error:
            raise ReceiverProtocolError(
                "Capture command failed; reconnect required"
            ) from error
        header_received = time.perf_counter()
        fields = line.split()
        if monitor and len(fields) == 8 and fields[0] == "IDLE":
            try:
                count, elapsed, failures, gain, lag, edges = map(int, fields[1:7])
                coherence = float(fields[7])
                if not (
                    128 <= count <= 6144
                    and 0 <= coherence <= 1
                    and 0 <= failures <= 10000
                    and 0 <= gain <= 81
                    and 0 < elapsed <= 1000000
                    and 0 <= lag <= 1000000
                    and edges >= 0
                ):
                    raise ValueError("Invalid monitor metadata")
            except ValueError as error:
                raise ReceiverProtocolError("Invalid monitor reply") from error
            return dict(
                idle=True,
                count=count,
                elapsed_us=elapsed,
                failures=failures,
                gain=gain,
                gate_edges=edges,
                gate_coherence=coherence,
                host_start=start,
                host_end=time.perf_counter(),
            )
        if len(fields) not in (6, 7) or fields[0] != "FSK":
            raise ReceiverProtocolError(
                "Invalid capture header; USB framing lost",
                details={"header": line[:512]},
            )
        try:
            count = int(fields[1])
            expected_crc = int(fields[2], 16)
            elapsed, failures, gain = map(int, fields[3:6])
            lag = int(fields[6]) if len(fields) == 7 else 0
            if not (
                0 < elapsed <= 1000000
                and 0 <= failures <= 10000
                and 0 <= gain <= 81
                and 0 <= lag <= 1000000
            ):
                raise ValueError("Invalid capture metadata")
        except ValueError as error:
            raise ReceiverProtocolError("Invalid capture metadata") from error
        if not 0 < count <= 6144:
            raise ReceiverProtocolError("Invalid observation count")
        size = count * 12
        raw = self.s.read(size)
        if len(raw) != size:
            raise ReceiverProtocolError(
                "Short capture; reconnect required",
                raw=raw,
                details={"expected_bytes": size, "received_bytes": len(raw)},
            )
        if zlib.crc32(raw) & 0xFFFFFFFF != expected_crc:
            try:
                self.resynchronize()
            except RuntimeError as error:
                raise ReceiverProtocolError(
                    "USB framing recovery failed",
                    raw=raw,
                    details={
                        "expected_crc": expected_crc,
                        "received_crc": zlib.crc32(raw) & 0xFFFFFFFF,
                    },
                ) from error
            raise CaptureIntegrityError(
                "Capture CRC mismatch; complete frame discarded",
                raw=raw,
                details={
                    "expected_crc": expected_crc,
                    "received_crc": zlib.crc32(raw) & 0xFFFFFFFF,
                },
            )
        rows = np.frombuffer(
            raw, dtype=[("us", "<u4"), ("p0", "<f4"), ("p1", "<f4")]
        ).copy()
        window_start = (
            header_received
            - (elapsed + lag) / 1e6
            - (len(line) + 1) * 10 / getattr(self.s, "baudrate", 1000000)
        )
        return dict(
            raw=raw,
            rows=rows,
            count=count,
            elapsed_us=elapsed,
            failures=failures,
            gain=gain,
            host_start=start,
            host_end=time.perf_counter(),
            estimated_window_host_start=window_start,
            snapshot_samples=getattr(self, "snapshot_samples", 128),
        )

    def close(self):
        self.s.close()
