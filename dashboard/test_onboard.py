"""Onboard reports must bypass PC RF decoding and never fabricate RF evidence."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import Receiver, parse_payload


def report():
    payload = "aa0014ab1402ba52011808015c0fc9010101b296b3"
    packet = dict(payload_hex=payload, weather=parse_payload(payload),
                  preamble_time_us=86000, clock_period_us=88.5,
                  checksum_valid=True, counter_valid=True, clock_source="whole")
    return dict(onboard=True, count=4285, failures=0, report_bytes=940,
                estimated_window_host_start=time.perf_counter()-.15,
                result=dict(valid_packets=[packet], decode_us=500000, clock_period_us=88.5,
                            clock_coherence=.6, transitions=399, candidates=1,
                            header_failures=0, checksum_failures=0, counter_failures=0))


class OnboardDashboardTests(unittest.TestCase):
    def test_report_is_saved_without_pc_decode_or_raw_file(self):
        with tempfile.TemporaryDirectory() as directory:
            receiver = Receiver(directory, seed=False, sensor_id="14AB")
            with patch("app.decode_clocked", side_effect=AssertionError("PC RF decoding forbidden")):
                accepted, synced = receiver.process_capture(report())
            self.assertTrue(accepted and synced)
            state = receiver.snapshot()
            self.assertEqual(state["readings"][-1]["temperature_C"], 19.8)
            self.assertIsNone(state["trace"])
            self.assertEqual(state["diagnostics"][0]["processing"], "esp32")
            self.assertEqual(state["diagnostics"][0]["reasons"], ["packet_accepted"])
            self.assertFalse(list(Path(directory).rglob("*.npz")))

    def test_quiet_or_failed_reports_do_not_create_readings(self):
        with tempfile.TemporaryDirectory() as directory:
            receiver = Receiver(directory, seed=False)
            for quiet in (True, False):
                value = report()
                value["result"].update(valid_packets=[], quiet=quiet, checksum_failures=1)
                self.assertEqual(receiver.process_capture(value), (False, False))
                diagnostic = receiver.snapshot()["diagnostics"][0]
                self.assertEqual(diagnostic["outcome"], "quiet" if quiet else "decode_failed")
            self.assertFalse(receiver.snapshot()["readings"])

    def test_worker_selects_onboard_weather_protocol(self):
        class Client:
            info = "VEVOR_FSK 1 weather=1"
            def __init__(self, **kwargs): pass
            def command(self, command): return "OK"
            def capture(self, *args, **kwargs): raise AssertionError("Raw transfer forbidden")
            def weather(self, *args, **kwargs): return report()
            def close(self): pass
        with tempfile.TemporaryDirectory() as directory:
            receiver = Receiver(directory, seed=False, client_factory=Client, serial_port="TEST", sensor_id="14AB")
            with patch("app.decode_clocked", side_effect=AssertionError("PC decode forbidden")):
                receiver.start()
                deadline = time.monotonic()+2
                while not receiver.snapshot()["stats"]["accepted"] and time.monotonic()<deadline:
                    time.sleep(.01)
                receiver.stop()
                receiver.worker.join(timeout=2)
            self.assertEqual(receiver.snapshot()["stats"]["accepted"], 1)
