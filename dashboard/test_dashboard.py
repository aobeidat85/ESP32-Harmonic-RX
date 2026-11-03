"""Integration checks use an actual saved RF capture, never synthetic weather."""

import csv
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np

from app import (
    Receiver,
    Handler,
    ThreadingHTTPServer,
    FIXTURES,
    ROW_DTYPE,
    weather_fields,
)
from esp32_harmonic_rx import CaptureIntegrityError


def real_capture():
    with np.load(FIXTURES / "vevor-yt60234-packet.npz", allow_pickle=False) as data:
        values = data["rows"].copy()
    return {
        "rows": values,
        "failures": 0,
        "estimated_window_host_start": time.perf_counter() - 0.2,
    }


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def test_real_packet_is_saved_and_duplicates_are_suppressed(self):
        receiver = Receiver(self.temp.name, seed=False)
        accepted, synced = receiver.process_capture(real_capture())
        self.assertTrue(accepted and synced)
        latest = receiver.snapshot()["readings"][-1]
        self.assertEqual(
            (latest["sensor_id"], latest["temperature_C"], latest["humidity_percent"]),
            ("14AB", 19.8, 82),
        )
        self.assertEqual(latest["source"], "live")
        receiver.process_capture(real_capture())
        self.assertEqual(receiver.snapshot()["stats"]["accepted"], 1)
        self.assertEqual(
            len((Path(self.temp.name) / "readings.jsonl").read_text().splitlines()), 1
        )

    def test_checksum_and_counter_corruption_are_not_weather(self):
        payload = bytearray.fromhex("aa0014ab1402ba52011808015c0fc9010101b296b3")
        for index in [19, 20]:
            corrupted = payload.copy()
            corrupted[index] ^= 1
            with self.assertRaises(ValueError):
                weather_fields({"payload_hex": corrupted.hex()})

    def test_capture_faults_do_not_create_readings(self):
        receiver = Receiver(self.temp.name, seed=False)
        capture = real_capture()
        capture["rows"]["us"][20] = 0
        with self.assertRaises(ValueError):
            receiver.process_capture(capture)
        self.assertFalse(receiver.snapshot()["readings"])

    def test_archive_is_not_labeled_live(self):
        receiver = Receiver(self.temp.name)
        snapshot = receiver.snapshot()
        self.assertGreaterEqual(len(snapshot["readings"]), 4)
        self.assertTrue(all(r["source"] == "saved" for r in snapshot["readings"]))
        self.assertEqual(snapshot["stats"]["accepted"], 0)
        self.assertFalse(snapshot["running"])

    def test_device_error_releases_worker_for_retry(self):
        def unavailable(**kwargs):
            raise OSError("COM10 unavailable")

        receiver = Receiver(
            self.temp.name, seed=False, client_factory=unavailable, serial_port="TEST"
        )
        receiver.start()
        receiver.worker.join(timeout=3)
        snapshot = receiver.snapshot()
        self.assertFalse(snapshot["running"])
        self.assertEqual(snapshot["phase"], "error")
        self.assertIn("COM10", snapshot["error"])

    def test_complete_crc_bad_frame_is_discarded_then_reception_continues(self):
        class DamagedThenRecorded:
            info = "VEVOR_FSK CRC_RECOVERY_TEST"

            def __init__(self, **kwargs):
                self.calls = 0

            def command(self, cmd):
                return "OK"

            def capture(self, ms):
                self.calls += 1
                if self.calls == 1:
                    raise CaptureIntegrityError("Complete CRC-bad frame discarded")
                return real_capture()

            def close(self):
                pass

        receiver = Receiver(
            self.temp.name,
            seed=False,
            client_factory=DamagedThenRecorded,
            serial_port="TEST",
        )
        receiver.start()
        deadline = time.monotonic() + 3
        while (
            not receiver.snapshot()["stats"]["accepted"] and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        receiver.stop()
        receiver.worker.join(timeout=3)
        snapshot = receiver.snapshot()
        self.assertEqual(snapshot["stats"]["crc_errors"], 1)
        self.assertEqual(snapshot["stats"]["accepted"], 1)
        self.assertIsNone(snapshot["error"])

    def test_http_start_stop_and_csv_with_real_capture(self):
        clients = []

        class RecordedClient:
            info = "VEVOR_FSK RECORDED_CAPTURE_TEST"

            def __init__(self, **kwargs):
                self.closed = False
                clients.append(self)

            def command(self, cmd):
                return "OK"

            def capture(self, ms):
                return real_capture()

            def close(self):
                self.closed = True

        receiver = Receiver(
            self.temp.name,
            seed=False,
            client_factory=RecordedClient,
            serial_port="TEST",
        )

        class TestHandler(Handler):
            def log_message(self, *args):
                pass

        TestHandler.receiver = receiver
        server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        base = f"http://127.0.0.1:{server.server_port}"

        def post(path, origin=None):
            headers = {"Content-Type": "application/json"}
            if origin:
                headers["Origin"] = origin
            return urlopen(Request(base + path, data=b"{}", headers=headers), timeout=3)

        try:
            with self.assertRaises(HTTPError) as error:
                post("/api/start", "http://example.com")
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
            self.assertFalse(clients)
            post("/api/start").close()
            deadline = time.monotonic() + 3
            while (
                not receiver.snapshot()["stats"]["accepted"]
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            self.assertEqual(receiver.snapshot()["stats"]["accepted"], 1)
            post("/api/start").close()
            self.assertEqual(len(clients), 1)
            post("/api/stop").close()
            receiver.worker.join(timeout=3)
            self.assertTrue(clients[0].closed)
            self.assertFalse(receiver.snapshot()["running"])
            with urlopen(base + "/api/export.csv") as response:
                exported = list(
                    csv.DictReader(io.StringIO(response.read().decode("utf-8-sig")))
                )
            self.assertEqual(exported[0]["source"], "live")
            self.assertEqual(exported[0]["sensor_id"], "14AB")
            with urlopen(base + "/api/status") as response:
                self.assertFalse(json.load(response)["running"])
        finally:
            receiver.stop()
            if receiver.worker:
                receiver.worker.join(timeout=3)
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
