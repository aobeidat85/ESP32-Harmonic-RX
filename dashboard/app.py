"""Local USB weather dashboard. Run with the experiment's Python environment."""

import argparse
import csv
import io
import json
import logging
import math
import mimetypes
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

from receiver.client import FSKClient, CaptureIntegrityError
from esp32_harmonic_rx import parse_payload
from serial.tools import list_ports
from receiver.decoder import decode_clocked

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT.parent / "tests" / "fixtures"
ROW_DTYPE = np.dtype([("us", "<u4"), ("p0", "<f4"), ("p1", "<f4")])
LOG = logging.getLogger("vevor")


def utc(timestamp=None):
    return datetime.fromtimestamp(
        time.time() if timestamp is None else timestamp, timezone.utc
    ).isoformat()


def weather_fields(packet):
    fields = parse_payload(packet["payload_hex"])
    fields["preamble_bit_errors"] = packet.get("preamble_bit_errors")
    return fields


class Receiver:
    def __init__(
        self,
        data_dir=ROOT / "data",
        seed=True,
        client_factory=FSKClient,
        serial_port=None,
        sensor_id=None,
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.client_factory = client_factory
        usb_ports = [p.device for p in list_ports.comports() if p.vid is not None]
        serial_port = serial_port or (usb_ports[0] if len(usb_ports) == 1 else None)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.worker = None
        self.anchor = None
        self.last_seen_payload = {}
        self.state = {
            "running": False,
            "phase": "idle",
            "message": "Ready to connect to the USB receiver.",
            "error": None,
            "next_capture_at": None,
            "connected": False,
            "config": {
                "port": serial_port,
                "gain": 65,
                "tuning_mhz": 2744,
                "sensor_id": sensor_id.upper() if sensor_id else None,
            },
            "stats": {
                "windows": 0,
                "accepted": 0,
                "missed": 0,
                "rejected": 0,
                "crc_errors": 0,
            },
            "readings": [],
            "events": [],
            "trace": None,
            "firmware": None,
        }
        if seed:
            self._load_saved()

    def _event(self, message, kind="info"):
        with self.lock:
            self.state["events"].insert(
                0, {"time": utc(), "message": message, "kind": kind}
            )
            del self.state["events"][80:]

    def _load_saved(self):
        demo = FIXTURES / "saved-readings.json"
        if demo.exists():
            for saved in json.loads(demo.read_text()):
                packet = weather_fields(saved)
                packet.update(source="saved", received_at=None)
                self.state["readings"].append(packet)
        persisted = self.data_dir / "readings.jsonl"
        if persisted.exists():
            for line in persisted.read_text().splitlines()[-300:]:
                try:
                    stored = json.loads(line)
                    weather_fields(stored)
                    stored["source"] = "saved"
                    self.state["readings"].append(stored)
                except (ValueError, KeyError):
                    pass
        self.state["readings"] = self.state["readings"][-300:]
        self._event(
            "Recorded example readings loaded. Start reception for new packets."
        )
        fixture = FIXTURES / "vevor-yt60234-packet.npz"
        if fixture.exists():
            with np.load(fixture, allow_pickle=False) as saved:
                rows = saved["rows"].copy()
            result, ratio = decode_clocked(
                rows["us"].astype(float),
                rows["p0"].astype(float),
                rows["p1"].astype(float),
            )
            self.state["trace"] = self._trace(rows, ratio, result, "saved", None)

    def snapshot(self):
        with self.lock:
            value = json.loads(json.dumps(self.state, allow_nan=False))
        value["server_time"] = utc()
        return value

    def start(self):
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                return False
            self.stop_event.clear()
            self.state.update(
                running=True,
                phase="connecting",
                connected=False,
                error=None,
                message="Connecting to COM10…",
                next_capture_at=None,
            )
            self.worker = threading.Thread(
                target=self._run, name="vevor-receiver", daemon=True
            )
            self.worker.start()
            return True

    def stop(self):
        with self.lock:
            if not self.state["running"]:
                return
            self.state.update(
                phase="stopping",
                message="Finishing the current capture…",
                next_capture_at=None,
            )
            self.stop_event.set()

    def _trace(self, rows, ratio, result, source, received_at):
        # Decoding uses every observation; only the browser chart is downsampled.
        indices = np.linspace(0, len(rows) - 1, min(500, len(rows)), dtype=int)
        power = 10 * np.log10(
            rows["p0"].astype(float) + rows["p1"].astype(float) + 1e-9
        )
        return {
            "source": source,
            "received_at": received_at,
            "points": [
                [
                    round(float(rows["us"][i]) / 1000, 3),
                    round(float(power[i]), 3),
                    round(float(ratio[i]), 3),
                ]
                for i in indices
            ],
            "observations": len(rows),
            "spacing_us": round(float(np.median(np.diff(rows["us"].astype(float)))), 1),
            "clock_period_us": result.get("clock_period_us"),
            "preamble_errors": result.get("nearest_preamble_bit_errors"),
            "valid_packets": len(result["valid_packets"]),
        }

    def process_capture(self, capture):
        with self.lock:
            self.state["stats"]["windows"] += 1
        rows = capture["rows"]
        if capture["failures"] or len(rows) < 256:
            raise ValueError("The receiver reported an incomplete capture.")
        if not np.isfinite(rows["p0"]).all() or not np.isfinite(rows["p1"]).all():
            raise ValueError("The capture contains invalid tone measurements.")
        if not (np.diff(rows["us"].astype(float)) > 0).all():
            raise ValueError("Capture timestamps are not increasing.")
        if len(np.unique(rows["p0"])) < 10:
            raise ValueError(
                "The receiver measurements are constant. Reconnect the receiver."
            )
        result, ratio = decode_clocked(
            rows["us"].astype(float), rows["p0"].astype(float), rows["p1"].astype(float)
        )
        received_at = utc()
        accepted = []
        for packet in result["valid_packets"]:
            fields = weather_fields(packet)
            if (
                self.state["config"]["sensor_id"]
                and fields["sensor_id"] != self.state["config"]["sensor_id"]
            ):
                self._event("A packet from another sensor was ignored.", "warning")
                continue
            key = packet["payload_hex"]
            now = time.monotonic()
            if now - self.last_seen_payload.get(key, -100) < 2:
                continue
            self.last_seen_payload[key] = now
            fields.update(
                source="live",
                received_at=received_at,
                clock_period_us=result.get("clock_period_us"),
            )
            accepted.append(fields)
            with (self.data_dir / "readings.jsonl").open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write(json.dumps(fields, allow_nan=False) + "\n")
        with self.lock:
            self.state["stats"]["accepted"] += len(accepted)
            if not accepted:
                self.state["stats"]["missed"] += 1
            self.state["readings"].extend(accepted)
            self.state["readings"] = self.state["readings"][-300:]
            self.state["trace"] = self._trace(rows, ratio, result, "live", received_at)
        if accepted:
            last = accepted[-1]
            self._event(
                f"Packet accepted · {last['temperature_C']:.1f}°C · {last['humidity_percent']}% RH",
                "success",
            )
            # Save the actual energy measurements alongside the accepted payload.
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            np.savez_compressed(
                self.data_dir / ("capture-" + stamp + ".npz"), rows=rows
            )
        sync = result["valid_packets"] or [
            x for x in result.get("unvalidated_candidates", []) if x.get("header_valid")
        ]
        if sync:
            self.anchor = (
                capture["estimated_window_host_start"]
                + sync[0]["preamble_time_us"] / 1e6
            )
        return bool(accepted), bool(sync)

    def _run(self):
        client = None
        try:
            port = self.state["config"]["port"]
            if not port:
                raise RuntimeError(
                    "Specify --serial-port: no unique USB serial receiver was found"
                )
            client = self.client_factory(port=port)
            client.command("GAIN 65")
            client.command("FREQ 2744")
            with self.lock:
                self.state.update(connected=True, firmware=client.info)
            self._event(f"USB receiver connected on {port}.")
            missed = 0
            while not self.stop_event.is_set():
                now = time.perf_counter()
                anchored = (
                    self.anchor is not None
                    and 0 <= now - self.anchor < 3600
                    and missed < 3
                )
                if anchored:
                    cycle = max(1, math.ceil((now + 0.15 + 0.065 - self.anchor) / 20))
                    target = self.anchor + cycle * 20 - 0.065
                    with self.lock:
                        self.state.update(
                            phase="waiting",
                            message="Waiting for the next sensor burst.",
                            next_capture_at=utc(time.time() + max(0, target - now)),
                        )
                    while target > time.perf_counter() and not self.stop_event.is_set():
                        self.stop_event.wait(
                            min(0.1, max(0, target - time.perf_counter()))
                        )
                else:
                    with self.lock:
                        self.state.update(
                            phase="searching",
                            message="Searching for a weather packet…",
                            next_capture_at=None,
                        )
                if self.stop_event.is_set():
                    break
                with self.lock:
                    self.state.update(
                        phase="capturing",
                        message="Capturing and checking the packet…",
                        next_capture_at=None,
                    )
                try:
                    capture = client.capture(150)
                except CaptureIntegrityError as error:
                    with self.lock:
                        self.state["stats"]["windows"] += 1
                        self.state["stats"]["crc_errors"] += 1
                        self.state["stats"]["rejected"] += 1
                    self._event(str(error), "warning")
                    missed += 1
                    continue
                try:
                    accepted, synced = self.process_capture(capture)
                    missed = 0 if synced else missed + 1
                except ValueError as error:
                    with self.lock:
                        self.state["stats"]["rejected"] += 1
                    self._event(str(error), "warning")
                    missed += 1
                self.stop_event.wait(0.1)
        except Exception as error:
            LOG.exception("Receiver stopped with an error")
            message = str(error)
            if "CRC" in message:
                with self.lock:
                    self.state["stats"]["crc_errors"] += 1
            with self.lock:
                self.state.update(
                    error=message,
                    phase="error",
                    message="Receiver disconnected. Check the serial port and try again.",
                )
            self._event(message, "error")
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    LOG.exception("Could not close the serial port")
            with self.lock:
                self.state.update(running=False, connected=False, next_capture_at=None)
                if self.state["phase"] != "error":
                    self.state.update(
                        phase="idle",
                        message="Reception stopped. Last readings are retained.",
                    )
            self._event("USB port released.")


class Handler(BaseHTTPRequestHandler):
    receiver = None

    def log_message(self, fmt, *args):
        LOG.info(fmt, *args)

    def send_bytes(
        self, code, data, mime="application/json; charset=utf-8", extra=None
    ):
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, code, value):
        self.send_bytes(code, json.dumps(value, allow_nan=False).encode())

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/status":
            self.send_json(200, self.receiver.snapshot())
        elif path == "/api/export.csv":
            records = self.receiver.snapshot()["readings"]
            out = io.StringIO(newline="")
            columns = [
                "received_at",
                "source",
                "sensor_id",
                "temperature_C",
                "temperature_F",
                "humidity_percent",
                "wind_avg_km_h",
                "wind_gust_km_h",
                "wind_direction_degrees",
                "total_rain_mm",
                "uv_index",
                "light_lux",
                "battery_ok",
                "counter",
                "checksum_valid",
                "counter_valid",
                "preamble_bit_errors",
                "payload_hex",
            ]
            writer = csv.DictWriter(out, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(records)
            self.send_bytes(
                200,
                out.getvalue().encode("utf-8-sig"),
                "text/csv; charset=utf-8",
                {"Content-Disposition": 'attachment; filename="vevor-readings.csv"'},
            )
        elif path in ["/", "/app.js", "/style.css"]:
            file = ROOT / "static" / ("index.html" if path == "/" else path[1:])
            self.send_bytes(
                200,
                file.read_bytes(),
                mimetypes.guess_type(file.name)[0] or "text/plain",
            )
        else:
            self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + self.headers.get("Host", ""):
            self.send_json(403, {"error": "Use the dashboard on this computer."})
            return
        if self.headers.get_content_type() != "application/json":
            self.send_json(415, {"error": "Expected JSON"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 1024:
                raise ValueError("Request too large")
            json.loads(self.rfile.read(size) or "{}")
        except (ValueError, json.JSONDecodeError):
            self.send_json(400, {"error": "Invalid request"})
            return
        if self.path == "/api/start":
            self.receiver.start()
            self.send_json(200, {"ok": True})
        elif self.path == "/api/stop":
            self.receiver.stop()
            self.send_json(200, {"ok": True})
        else:
            self.send_json(404, {"error": "Not found"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--serial-port",
        help="USB receiver port, e.g.COM10 or /dev/ttyUSB0; automatically selected when exactly one USB serial port is present",
    )
    parser.add_argument("--sensor-id", help="Optional hexadecimal sensor ID filter")
    parser.add_argument(
        "--no-saved",
        action="store_true",
        help="Do not load recorded example or saved readings",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    Handler.receiver = Receiver(
        serial_port=args.serial_port, sensor_id=args.sensor_id, seed=not args.no_saved
    )
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"VEVOR dashboard: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        Handler.receiver.stop()
        if Handler.receiver.worker:
            Handler.receiver.worker.join(timeout=5)
        server.server_close()


if __name__ == "__main__":
    main()
