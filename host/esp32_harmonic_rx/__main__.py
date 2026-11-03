# SPDX-License-Identifier: GPL-3.0-only
import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
from . import ReceiverClient, CaptureIntegrityError, decode_clocked, parse_payload


def unpack(rows, source, sensor_id=None):
    result, _ = decode_clocked(rows["us"].astype(float), rows["p0"], rows["p1"])
    seen = set()
    for packet in result["valid_packets"]:
        if packet["payload_hex"] in seen:
            continue
        seen.add(packet["payload_hex"])
        fields = parse_payload(packet["payload_hex"])
        if sensor_id and fields["sensor_id"] != sensor_id.upper():
            continue
        fields.update(source=source, preamble_bit_errors=packet["preamble_bit_errors"])
        fields.update(
            repetition_count=packet.get("repetition_count", 1),
            recovery_method=packet.get("clock_source", "whole"),
        )
        print(json.dumps(fields), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Experimental ESP32 receiver for the VEVOR YT60234 example"
    )
    parser.add_argument(
        "--port", help="Receiver serial port, e.g. COM10 or /dev/ttyUSB0"
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=120,
        help="Bounded listening duration, default120 seconds",
    )
    parser.add_argument("--sensor-id", help="Optional hexadecimal sensor ID filter")
    parser.add_argument(
        "--replay",
        type=Path,
        help="Decode a recorded NPZ capture instead of opening USB",
    )
    args = parser.parse_args()
    if args.replay:
        with np.load(args.replay, allow_pickle=False) as saved:
            rows = saved["rows"].copy()
        unpack(rows, "recorded", args.sensor_id)
        return
    if not args.port:
        parser.error("--port is required for live reception")
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("--seconds must be positive")
    client = None
    anchor = None
    missed = 0
    started = time.monotonic()
    try:
        client = ReceiverClient(port=args.port)
        client.command("GAIN 65")
        client.command("FREQ 2744")
        print(
            "Receiver connected; searching for VEVOR bursts.",
            file=sys.stderr,
            flush=True,
        )
        while time.monotonic() - started < args.seconds:
            if anchor is not None and missed < 3:
                target = (
                    anchor
                    + max(1, math.ceil((time.perf_counter() + 0.215 - anchor) / 20))
                    * 20
                    - 0.065
                )
                remaining = min(
                    target - time.perf_counter(),
                    args.seconds - (time.monotonic() - started),
                )
                if remaining > 0:
                    time.sleep(remaining)
                if time.monotonic() - started >= args.seconds:
                    break
            try:
                capture = client.capture(150)
            except CaptureIntegrityError as error:
                print(str(error), file=sys.stderr, flush=True)
                missed += 1
                continue
            if capture["failures"]:
                missed += 1
                continue
            result = unpack(capture["rows"], "live", args.sensor_id)
            sync = result["valid_packets"] or [
                p for p in result.get("unvalidated_candidates", []) if p["header_valid"]
            ]
            if sync:
                anchor = (
                    capture["estimated_window_host_start"]
                    + sync[0]["preamble_time_us"] / 1e6
                )
                missed = 0
            else:
                missed += 1
    except KeyboardInterrupt:
        pass
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    main()
