import unittest

from pathlib import Path
import numpy as np

from esp32_harmonic_rx import (
    ReceiverClient,
    CaptureIntegrityError,
    decode_clocked,
    parse_payload,
    ReceiverProtocolError,
)

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


class ReceiverTests(unittest.TestCase):
    def test_repeated_measured_copies_recover_without_checksum_search(self):
        from unittest.mock import patch

        with np.load(FIXTURES / "vevor-repetitions.npz", allow_pickle=False) as saved:
            rows = saved["rows"]
        args = (rows["us"], rows["p0"], rows["p1"])
        with patch("esp32_harmonic_rx.vevor._combine_repetitions", return_value=[]):
            previous, _ = decode_clocked(*args)
        self.assertFalse(previous["valid_packets"])
        result, _ = decode_clocked(*args)
        self.assertEqual(len(result["valid_packets"]), 1)
        packet = result["valid_packets"][0]
        self.assertEqual(
            packet["payload_hex"], "aa0014ab1402af55010c02015b0fc9010101915a92"
        )
        self.assertEqual(packet["repetition_count"], 2)
        self.assertEqual(packet["clock_source"], "repetition")
        self.assertEqual(parse_payload(packet["payload_hex"])["humidity_percent"], 85)

    def test_baud_negotiation_and_legacy_fallback(self):
        from unittest.mock import patch

        class Port:
            def __init__(self, modern):
                self.modern = modern
                self.baudrate = 1000000
                self.commands = []

            def reset_input_buffer(self):
                pass

            def write(self, command):
                self.commands.append(command)

            def readline(self):
                if self.commands[-1].startswith(b"BAUD "):
                    return b"OK\n"
                return (
                    f"VEVOR_FSK 1 baud_switch=1 baud={self.baudrate}\n".encode()
                    if self.modern
                    else b"VEVOR_FSK 1 legacy\n"
                )

            def close(self):
                pass

        for modern, expected in [(True, 500000), (False, 1000000)]:
            port = Port(modern)
            with patch("esp32_harmonic_rx.client.serial.Serial", return_value=port):
                client = ReceiverClient(reset=False, port="TEST")
            self.assertEqual(client.s.baudrate, expected)
            self.assertEqual(b"BAUD 500000\n" in port.commands, modern)

    def test_repetition_recovery_on_fresh_holdout_and_control(self):
        from unittest.mock import patch

        with np.load(
            FIXTURES / "vevor-repetitions-holdout.npz", allow_pickle=False
        ) as saved:
            rows = saved["rows"]
        args = (rows["us"], rows["p0"], rows["p1"])
        with patch("esp32_harmonic_rx.vevor._combine_repetitions", return_value=[]):
            previous, _ = decode_clocked(*args)
        self.assertFalse(previous["valid_packets"])
        result, _ = decode_clocked(*args)
        packet = result["valid_packets"][0]
        self.assertEqual(
            packet["payload_hex"], "aa0014ab1402a35a010a0201630fc9010101864e87"
        )
        self.assertEqual(packet["repetition_count"], 2)
        with np.load(
            FIXTURES / "vevor-repetitions-control.npz", allow_pickle=False
        ) as saved:
            control = saved["rows"]
        rejected, _ = decode_clocked(control["us"], control["p0"], control["p1"])
        self.assertEqual(rejected["valid_packets"], [])

    def test_monitor_idle_reply_and_invalid_serial_boundary(self):
        class Reply:
            def __init__(self, line):
                self.line = line

            def write(self, data):
                self.sent = data

            def readline(self):
                return self.line

        client = ReceiverClient.__new__(ReceiverClient)
        client.s = Reply(b"IDLE 4271 150010 0 65 2000 430 0.0542\n")
        capture = client.capture(150, monitor=True)
        self.assertEqual(client.s.sent, b"MON 150\n")
        self.assertTrue(capture["idle"])
        self.assertNotIn("rows", capture)
        self.assertEqual(capture["count"], 4271)
        for line in [
            b"\xff\xff\x00 binary\n",
            b"FSK broken 0 150000 0 65\n",
            b"IDLE 4271 150010 0 65 2000 430 nan\n",
        ]:
            client.s = Reply(line)
            with self.assertRaises(ReceiverProtocolError):
                client.capture(150, monitor=True)

    def test_local_clock_recovers_fresh_capture_without_bit_repair(self):
        from esp32_harmonic_rx.vevor import _decode_window

        with np.load(FIXTURES / "vevor-local-clock.npz", allow_pickle=False) as saved:
            rows = saved["rows"]
        args = (rows["us"], rows["p0"], rows["p1"])
        before, _ = _decode_window(*args)
        self.assertEqual(before["valid_packets"], [])
        after, _ = decode_clocked(*args)
        packet = after["valid_packets"][0]
        self.assertEqual(packet["clock_source"], "local")
        self.assertEqual(
            packet["payload_hex"], "aa0014ab1402af5501080501600fc90101016a376b"
        )
        reading = parse_payload(packet["payload_hex"])
        self.assertEqual(
            (reading["temperature_C"], reading["humidity_percent"]), (18.7, 85)
        )

    def test_recorded_weather_packet(self):
        with np.load(
            FIXTURES / "vevor-yt60234-packet.npz", allow_pickle=False
        ) as saved:
            rows = saved["rows"]
        result, _ = decode_clocked(rows["us"], rows["p0"], rows["p1"])
        self.assertTrue(result["valid_packets"])
        reading = parse_payload(result["valid_packets"][0]["payload_hex"])
        self.assertEqual(
            (
                reading["sensor_id"],
                reading["temperature_C"],
                reading["humidity_percent"],
            ),
            ("14AB", 19.8, 82),
        )
        self.assertEqual(reading["counter"] + 1, reading["next_counter"])

    def test_recorded_between_burst_control(self):
        with np.load(
            FIXTURES / "between-burst-control.npz", allow_pickle=False
        ) as saved:
            rows = saved["rows"]
        result, _ = decode_clocked(rows["us"], rows["p0"], rows["p1"])
        self.assertEqual(result["valid_packets"], [])

    def test_corrupt_payload_checks(self):
        payload = bytearray.fromhex("aa0014ab1402ba52011808015c0fc9010101b296b3")
        for index in [0, 19, 20]:
            bad = payload.copy()
            bad[index] ^= 1
            with self.assertRaises(ValueError):
                parse_payload(bad)

    def test_observation_validation(self):
        t = np.arange(128, dtype=float)
        p = np.ones(128)
        for bad in [t[::-1], np.full(128, np.nan)]:
            with self.assertRaises(ValueError):
                decode_clocked(bad, p, p)
        with self.assertRaises(ValueError):
            decode_clocked(t, -p, p)

    def test_crc_failure_is_distinct_from_short_read(self):
        payload = bytes(128 * 12)

        class SerialFrame:
            def __init__(self, short=False):
                self.short = short
                self.last_command = b""
                self.in_waiting = 0

            def write(self, data):
                self.last_command = data

            def readline(self):
                if self.last_command == b"INFO\n":
                    return b"VEVOR_FSK 1 fixture\n"
                return b"FSK 128 00000001 10000 0 65\n"

            def reset_input_buffer(self):
                pass

            def read(self, n):
                return payload[:-1] if self.short else payload

        client = ReceiverClient.__new__(ReceiverClient)
        client.s = SerialFrame()
        with self.assertRaises(CaptureIntegrityError):
            client.capture(10)
        client.s = SerialFrame(short=True)
        with self.assertRaises(RuntimeError) as error:
            client.capture(10)
        self.assertNotIsInstance(error.exception, CaptureIntegrityError)


if __name__ == "__main__":
    unittest.main()
