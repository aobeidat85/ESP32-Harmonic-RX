"""Persistent capture outcomes and bounded raw-failure retention."""

import json
import re
import uuid
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


class CaptureLog:
    def __init__(self, data_dir, raw_limit=1000):
        self.directory = Path(data_dir) / "diagnostics"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.journal = self.directory / "captures.jsonl"
        self.raw_limit = raw_limit
        self.session = uuid.uuid4().hex[:8]
        self.sequence = 0
        self.lock = threading.Lock()
        self.recent = deque(maxlen=50)
        self.failures = deque(maxlen=20)
        if self.journal.exists():
            # Stream the journal so a long-running experiment does not load it all.
            with self.journal.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        record = json.loads(line)
                        if not isinstance(record, dict):
                            continue
                        self.recent.append(record)
                        if record.get("outcome") not in ("accepted", "quiet"):
                            self.failures.append(record)
                    except ValueError:
                        pass

    def write(self, outcome, reasons, *, rows=None, raw=None, **details):
        with self.lock:
            return self._write(outcome, reasons, rows=rows, raw=raw, **details)

    def export_bytes(self):
        with self.lock:
            return self.journal.read_bytes() if self.journal.exists() else b""

    def _write(self, outcome, reasons, *, rows=None, raw=None, **details):
        self.sequence += 1
        now = datetime.now(timezone.utc)
        identifier = (
            now.strftime("%Y%m%dT%H%M%S%fZ")
            + "-"
            + self.session
            + f"-{self.sequence:06d}"
        )
        record = dict(
            id=identifier,
            session=self.session,
            sequence=self.sequence,
            time=now.isoformat(),
            outcome=outcome,
            reasons=list(reasons),
            **details,
        )
        if rows is not None:
            name = "failure-" + identifier + ".npz"
            np.savez_compressed(
                self.directory / name,
                rows=rows,
                snapshot_samples=np.uint16(details.get("snapshot_samples", 128)),
            )
            record["raw_file"] = name
        elif raw is not None:
            name = "usb-" + identifier + ".bin"
            (self.directory / name).write_bytes(raw)
            record["raw_file"] = name
        with self.journal.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, allow_nan=False) + "\n")
        self.recent.append(record)
        if outcome not in ("accepted", "quiet"):
            self.failures.append(record)
        if rows is not None or raw is not None:
            owned = sorted(
                p
                for p in self.directory.iterdir()
                if re.fullmatch(
                    r"(?:failure|usb)-[0-9TZ]+-[0-9a-f]+-\d+\.(?:npz|bin)", p.name
                )
            )
            for old in owned[: -self.raw_limit]:
                if old.parent.resolve() == self.directory.resolve() and old.is_file():
                    try:
                        old.unlink()
                    except OSError:
                        pass  # A concurrent raw download must not stop reception.
        return record


def decode_reasons(result):
    """Describe recorded checks; never infer weak RF from checksum failure alone."""
    candidates = result.get(
        "candidate_checks", result.get("unvalidated_candidates", [])
    )
    if not candidates:
        if result.get("reason") == "Too few strong FSK transitions":
            return ["insufficient_fsk_transitions"]
        errors = result.get("nearest_preamble_bit_errors")
        return [
            "sync_pattern_mismatch"
            if errors is None or errors > 2
            else "no_complete_payload_candidate"
        ]
    reasons = []
    for check, label in [
        ("header_valid", "payload_header_failed"),
        ("checksum_valid", "payload_checksum_failed"),
        ("counter_valid", "paired_counter_failed"),
    ]:
        if any(not p.get(check) for p in candidates):
            reasons.append(label)
    return reasons or ["no_accepted_packet"]
