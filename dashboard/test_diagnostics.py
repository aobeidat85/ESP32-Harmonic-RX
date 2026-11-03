import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import numpy as np

from app import Receiver, Handler, ThreadingHTTPServer, FIXTURES
from capture_log import CaptureLog
from timing import BurstTiming


def capture(name):
    with np.load(FIXTURES / name, allow_pickle=False) as saved:
        rows = saved["rows"].copy()
    return dict(
        rows=rows, failures=0, estimated_window_host_start=time.perf_counter() - 0.2
    )


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def failed_receiver(self):
        receiver = Receiver(
            self.temp.name, seed=False, serial_port="TEST", sensor_id="14AB"
        )
        receiver.process_capture(capture("vevor-rejected-payload.npz"))
        return receiver

    def test_usb_io_failure_is_not_mislabeled_as_rf_or_framing_failure(self):
        clients = []

        class Client:
            info = "VEVOR_FSK io_test"

            def __init__(self, **kwargs):
                self.broken = not clients
                clients.append(self)

            def command(self, cmd):
                return "OK"

            def capture(self, ms):
                if self.broken:
                    raise OSError(22, "USB write failed")
                return capture("vevor-yt60234-packet.npz")

            def close(self):
                pass

        receiver = Receiver(
            self.temp.name, seed=False, client_factory=Client, serial_port="TEST"
        )
        receiver.start()
        deadline = time.monotonic() + 3
        while (
            not receiver.snapshot()["stats"]["accepted"] and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        receiver.stop()
        receiver.worker.join(timeout=3)
        record = receiver.snapshot()["recent_failures"][0]
        self.assertEqual(record["outcome"], "usb_io")
        self.assertEqual(record["reasons"], ["usb_io_failed"])
        self.assertEqual(record["transport"]["errno"], 22)
        self.assertEqual(receiver.snapshot()["stats"]["accepted"], 1)

    def test_failed_payload_raw_checks_and_journal_survive_restart(self):
        receiver = self.failed_receiver()
        record = receiver.snapshot()["recent_failures"][0]
        self.assertEqual(record["outcome"], "decode_failed")
        self.assertIn("payload_checksum_failed", record["reasons"])
        self.assertEqual(
            {p["stage"] for p in record["candidate_checks"]}, {"whole", "local", "sync"}
        )
        self.assertTrue(
            all(not p["checksum_valid"] for p in record["candidate_checks"])
        )
        raw = receiver.capture_log.directory / record["raw_file"]
        with np.load(raw, allow_pickle=False) as saved:
            self.assertGreater(len(saved["rows"]), 4000)
        restarted = Receiver(self.temp.name, seed=False, serial_port="TEST")
        self.assertEqual(restarted.snapshot()["recent_failures"][0]["id"], record["id"])
        self.assertFalse(restarted.snapshot()["readings"])

    def test_sensor_filter_also_filters_timing(self):
        receiver = Receiver(
            self.temp.name, seed=False, serial_port="TEST", sensor_id="FFFF"
        )
        accepted, synced = receiver.process_capture(capture("vevor-yt60234-packet.npz"))
        self.assertFalse(accepted)
        self.assertFalse(synced)
        self.assertIsNone(receiver.anchor)
        self.assertEqual(
            receiver.snapshot()["recent_failures"][0]["reasons"],
            ["station_id_filtered"],
        )

    def test_raw_retention_preserves_metadata_and_usb_evidence(self):
        log = CaptureLog(self.temp.name, raw_limit=2)
        first = log.write("usb_crc", ["usb_crc_failed"], raw=b"first corrupt frame")
        second = log.write("usb_framing", ["usb_boundary_lost"], raw=b"second")
        third = log.write("usb_crc", ["usb_crc_failed"], raw=b"third")
        self.assertFalse((log.directory / first["raw_file"]).exists())
        self.assertEqual((log.directory / second["raw_file"]).read_bytes(), b"second")
        self.assertTrue((log.directory / third["raw_file"]).exists())
        self.assertEqual(len(log.journal.read_text().splitlines()), 3)
        for _ in range(60):
            log.write("quiet", ["activity_gate_below_threshold"])
        reloaded = CaptureLog(self.temp.name)
        self.assertEqual(len(reloaded.failures), 3)

    def test_timing_tracks_drift_without_learning_copy_hops(self):
        timing = BurstTiming()
        timing.observe(100, 0.03354, True)
        for cycle in range(1, 25):
            jitter = [-0.001, 0.001, 0][cycle % 3]
            copy = 0.03354 if cycle % 4 == 0 else 0
            timing.observe(100 + cycle * 19.997 + jitter + copy, 0.03354, True)
        self.assertAlmostEqual(timing.period, 19.997, delta=0.002)
        self.assertLess(abs(timing.anchor - (100 + 24 * 19.997)), 0.015)
        self.assertGreater(len(timing.intervals), 3)
        period = timing.period
        timing.observe(timing.anchor + 20, 0.03354, False)
        self.assertEqual(timing.period, period)

    def test_timing_ignores_same_burst_and_reacquires_verified_shift(self):
        timing = BurstTiming(anchor=100)
        timing.observe(100.03354, 0.03354, True)
        self.assertEqual(timing.anchor, 100)
        timing.observe(125, 0.03354, False)
        self.assertEqual(timing.anchor, 100)
        timing.observe(125, 0.03354, True)
        self.assertEqual(timing.anchor, 125)
        self.assertEqual(timing.period, 20)

    def test_http_diagnostic_export_and_bounded_raw_download(self):
        receiver = self.failed_receiver()

        class TestHandler(Handler):
            def log_message(self, *args):
                pass

        TestHandler.receiver = receiver
        server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base + "/api/diagnostics/export.jsonl") as response:
                record = json.loads(response.read().splitlines()[0])
            with urlopen(
                base + "/api/diagnostics/raw/" + record["raw_file"]
            ) as response:
                self.assertTrue(response.read().startswith(b"PK"))
            with self.assertRaises(HTTPError) as rejected:
                urlopen(base + "/api/diagnostics/raw/%2e%2e%2fLICENSE")
            self.assertEqual(rejected.exception.code, 400)
            rejected.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
