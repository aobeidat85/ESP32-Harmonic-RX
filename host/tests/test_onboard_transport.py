import copy
import json
import unittest
import zlib

from esp32_harmonic_rx import ReceiverClient, CaptureIntegrityError, ReceiverProtocolError, parse_payload


def report():
    hex_payload = "aa0014ab1402ba52011808015c0fc9010101b296b3"
    return dict(version=1, count=4285, elapsed_us=150000, failures=0,
                decode_us=500000, invalid=False, valid_packets=[dict(
                    payload_hex=hex_payload, preamble_time_us=86000,
                    clock_period_us=88.5, weather=parse_payload(hex_payload))])


class Port:
    timeout = 3
    baudrate = 500000

    def __init__(self, payload, corrupt=False, short=False):
        self.raw = json.dumps(payload).encode()
        self.crc = (zlib.crc32(self.raw) ^ int(corrupt)) & 0xffffffff
        self.short = short

    def write(self, data):
        self.sent = data

    def readline(self):
        return f"WX {len(self.raw)} {self.crc:08x}\n".encode()

    def read(self, size):
        return self.raw[:-1] if self.short else self.raw


class OnboardTransportTests(unittest.TestCase):
    def client(self, payload=None, **kwargs):
        client = ReceiverClient.__new__(ReceiverClient)
        client.s = Port(report() if payload is None else payload, **kwargs)
        return client

    def test_weather_only_transfer_and_monitor_command(self):
        client = self.client()
        result = client.weather(monitor=True)
        self.assertEqual(client.s.sent, b"WM 150\n")
        self.assertTrue(result["onboard"])
        self.assertNotIn("rows", result)
        self.assertEqual(result["result"]["valid_packets"][0]["weather"]["humidity_percent"], 82)
        self.assertEqual(client.s.timeout, 3)

    def test_corrupt_and_short_reports_rejected(self):
        for options, error in [({"corrupt": True}, CaptureIntegrityError), ({"short": True}, ReceiverProtocolError)]:
            client = self.client(**options)
            with self.assertRaises(error):
                client.weather()
            self.assertEqual(client.s.timeout, 3)

    def test_weather_payload_mismatch_or_invalid_timing_rejected(self):
        for kind in ("weather", "payload", "timing", "incomplete", "invalid"):
            value = copy.deepcopy(report())
            if kind == "weather":
                value["valid_packets"][0]["weather"]["temperature_C"] = 99
            elif kind == "payload":
                value["valid_packets"][0]["payload_hex"] = "00" * 21
            elif kind == "timing":
                value["decode_us"] = -1
            elif kind == "incomplete":
                value["failures"] = 1
            else:
                value["invalid"] = True
            with self.subTest(kind=kind), self.assertRaises(ReceiverProtocolError):
                self.client(value).weather()
