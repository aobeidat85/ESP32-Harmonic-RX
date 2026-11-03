"""Compare the portable C++ decoder (or actual board) with recorded RF fixtures.

python tests/verify_onboard.py --native /path/to/decoder
python tests/verify_onboard.py --port COM10
Replay results are test evidence, never appended to live dashboard history.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time

import numpy as np
from esp32_harmonic_rx import ReceiverClient, decode_clocked


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--native")
    group.add_argument("--port")
    args = parser.parse_args()
    client = ReceiverClient(port=args.port) if args.port else None
    if client:
        print(client.info, flush=True)
        client.command("STATION 14AB")
    failures = []
    try:
        with tempfile.TemporaryDirectory() as directory:
            for fixture in sorted((Path(__file__).parent / "fixtures").glob("*.npz")):
                rows = np.load(fixture, allow_pickle=False)["rows"]
                reference, _ = decode_clocked(rows["us"], rows["p0"], rows["p1"], preferred_sensor_id="14AB")
                expected = {p["payload_hex"] for p in reference["valid_packets"] if p["payload_hex"][4:8] == "14ab"}
                start = time.perf_counter()
                if client:
                    report = client.weather(replay_rows=rows)
                    actual = {p["payload_hex"] for p in report["result"]["valid_packets"]}
                    detail = {k: report["result"][k] for k in ("decode_us", "heap")}
                    detail["report_bytes"] = report["report_bytes"]
                else:
                    binary = Path(directory) / "rows.bin"
                    binary.write_bytes(rows.tobytes())
                    output = subprocess.check_output([args.native, str(binary)], text=True)
                    actual = {line.split()[0] for line in output.splitlines()[1:]}
                    detail = {"runtime_s": round(time.perf_counter()-start, 3)}
                passed = actual == expected
                print(json.dumps(dict(fixture=fixture.name, passed=passed, expected=sorted(expected), actual=sorted(actual), **detail)), flush=True)
                if not passed:
                    failures.append(fixture.name)
    finally:
        if client:
            client.close()
    if failures:
        raise SystemExit("Mismatching fixtures: " + ", ".join(failures))


if __name__ == "__main__":
    main()
