# SPDX-License-Identifier: GPL-3.0-only
"""CRC-checked client for the included USB bridge example."""

import serial, time, zlib, json, math
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

    def weather(self, ms=150, *, monitor=False, replay_rows=None):
        """Acquire/decode on the board. Transfer only a CRC-framed weather report."""
        start = time.perf_counter()
        old_timeout = self.s.timeout
        try:
            self.s.timeout = 20
            if replay_rows is None:
                line = self.command(("WM " if monitor else "WX ") + str(ms))
            else:
                raw_rows = replay_rows.tobytes()
                if not 128 <= len(replay_rows) <= 6144 or len(raw_rows) != len(replay_rows)*12:
                    raise ValueError("Invalid replay observation layout")
                if self.command(f"REPLAY {len(replay_rows)} {zlib.crc32(raw_rows)&0xffffffff:08x}") != "READY":
                    raise ReceiverProtocolError("Replay was not acknowledged")
                self.s.write(raw_rows)
                line = self.s.readline().decode(errors="replace").strip()
            header_received = time.perf_counter()
            fields = line.split()
            if len(fields) != 3 or fields[0] != "WX":
                raise ReceiverProtocolError("Invalid onboard weather header")
            try:
                size, expected = int(fields[1]), int(fields[2], 16)
            except ValueError as error:
                raise ReceiverProtocolError("Invalid weather frame size/CRC") from error
            if not 0 < size <= 6144:
                raise ReceiverProtocolError("Weather report exceeds limit")
            raw = self.s.read(size)
            if len(raw) != size:
                raise ReceiverProtocolError("Short weather report", raw=raw,
                                            details={"expected_bytes": size, "received_bytes": len(raw)})
            if zlib.crc32(raw) & 0xffffffff != expected:
                raise CaptureIntegrityError("Weather report CRC mismatch", raw=raw)
            try:
                report = json.loads(raw)
                if report["version"] != 1 or not 128 <= report["count"] <= 6144:
                    raise ValueError("Invalid report version/count")
                if not 0 < report["elapsed_us"] <= 1000000 or not 0 <= report["decode_us"] < 19000000:
                    raise ValueError("Invalid board timing")
                if not isinstance(report["valid_packets"], list) or len(report["valid_packets"]) > 8:
                    raise ValueError("Invalid packet list")
                # Independent transport sanity check; RF timing/bit recovery ran on ESP32.
                from .vevor import parse_payload
                for packet in report["valid_packets"]:
                    expected_fields = parse_payload(packet["payload_hex"])
                    for key, value in expected_fields.items():
                        actual = packet["weather"][key]
                        if isinstance(value, float):
                            if not isinstance(actual, (int, float)) or not math.isfinite(actual) or abs(actual-value) > .011:
                                raise ValueError("Weather fields do not match validated payload")
                        elif actual != value:
                            raise ValueError("Weather fields do not match validated payload")
                    if not 0 <= packet["preamble_time_us"] <= report["elapsed_us"] or not 86 <= packet["clock_period_us"] <= 90.5:
                        raise ValueError("Invalid packet timing")
                if report["invalid"] or report["failures"]:
                    raise ValueError("Board rejected incomplete/invalid measurements")
            except (ValueError, KeyError, TypeError) as error:
                raise ReceiverProtocolError("Invalid onboard weather report: " + str(error)) from error
            return dict(onboard=True, result=report, count=report["count"], failures=report["failures"],
                        elapsed_us=report["elapsed_us"], host_start=start, host_end=time.perf_counter(),
                        report_bytes=size, snapshot_samples=getattr(self, "snapshot_samples", 128),
                        estimated_window_host_start=header_received-(report["elapsed_us"]+report["decode_us"])/1e6
                        -(len(line)+1)*10/getattr(self.s,"baudrate",500000))
        finally:
            self.s.timeout = old_timeout

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
