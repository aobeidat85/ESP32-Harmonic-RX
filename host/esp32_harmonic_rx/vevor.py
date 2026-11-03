# SPDX-License-Identifier: GPL-3.0-only
# Physical clock recovery precedes packet checks; no checksum-guided timing search.
# VEVOR field/checksum interpretation follows rtl_433 vevor_7in1.c by Bruno OCTAU.
import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import correlate

PREAMBLE = np.unpackbits(np.frombuffer(bytes.fromhex("aaaacaca54"), np.uint8))
PATTERN = 2 * PREAMBLE.astype(float) - 1


def decode_clocked(t, p0, p1):
    t = np.asarray(t, dtype=float)
    p0 = np.asarray(p0, dtype=float)
    p1 = np.asarray(p1, dtype=float)
    if t.ndim != 1 or len(t) < 128 or len(t) != len(p0) or len(t) != len(p1):
        raise ValueError(
            "Expected equal one-dimensional observation arrays with at least128 entries"
        )
    if not (np.isfinite(t).all() and np.isfinite(p0).all() and np.isfinite(p1).all()):
        raise ValueError("Observations must be finite")
    if not (np.diff(t) > 0).all() or (p0 < 0).any() or (p1 < 0).any():
        raise ValueError("Timestamps must increase and tone powers must be nonnegative")
    total = p0 + p1
    quiet = total <= np.percentile(total, 30)
    b0 = max(float(np.median(p0[quiet])), 1e-9)
    b1 = max(float(np.median(p1[quiet])), 1e-9)
    ratio = np.clip(np.log((p1 / b1 + 1e-6) / (p0 / b0 + 1e-6)), -8, 8)
    active = median_filter(total, size=9) > np.percentile(total, 10) * 3
    strong = np.flatnonzero(active & (abs(ratio) > 1))
    edges = []
    weights = []
    for left, right in zip(strong[:-1], strong[1:]):
        gap = t[right] - t[left]
        if gap < 145 and (ratio[left] > 0) != (ratio[right] > 0):
            edges.append((t[left] + t[right]) / 2)
            weights.append(min(abs(ratio[left]), abs(ratio[right])) / gap)
    if len(edges) < 20:
        return dict(
            valid_packets=[],
            reason="Too few strong FSK transitions",
            transitions=len(edges),
        ), ratio
    edges = np.array(edges)
    weights = np.array(weights)
    periods = np.arange(86, 90.501, 0.005)
    coherence = np.array(
        [
            abs(np.sum(weights * np.exp(2j * np.pi * edges / T))) / sum(weights)
            for T in periods
        ]
    )
    k = int(np.argmax(coherence))
    period = float(periods[k])
    phasor = np.sum(weights * np.exp(2j * np.pi * edges / period))
    phase = float(np.angle(phasor) * period / (2 * np.pi) % period)
    # Group observations inside the clock cells. Recover bits without consulting packet checksum.
    cells = np.floor((t - phase) / period).astype(int)
    first = min(cells)
    cells -= first
    soft = np.bincount(cells, weights=ratio)
    bits = (soft > 0).astype(np.uint8)
    valid = []
    candidates = []
    minimum = 40
    for inverted in [False, True]:
        decoded = 1 - bits if inverted else bits
        score = correlate(
            2 * decoded.astype(float) - 1, PATTERN, mode="valid", method="direct"
        )
        minimum = min(minimum, int(round((40 - max(score)) / 2)))
        for pos in np.flatnonzero(score >= 36 - 1e-6):
            if pos + 264 > len(decoded):
                continue
            b = np.packbits(decoded[pos + 40 : pos + 40 + 168]).tobytes()
            header = b[:2] == b"\xaa\x00"
            checksum = (sum(b[:19]) & 255) == b[19]
            counter = b[20] == ((b[18] + 1) & 255)
            candidate = dict(
                payload_hex=b.hex(),
                preamble_time_us=float(phase + (first + pos) * period),
                preamble_bit_errors=int(round((40 - score[pos]) / 2)),
                header_valid=header,
                checksum_valid=checksum,
                counter_valid=counter,
                inverted=inverted,
            )
            candidates.append(candidate)
            if header and checksum and counter:
                valid.append(
                    dict(
                        **candidate,
                        sensor_id=f"{b[2]:02x}{b[3]:02x}",
                        temperature_C=(((b[5] & 15) << 8 | b[6]) - 500) / 10,
                        humidity=b[7],
                    )
                )
    return dict(
        valid_packets=valid,
        clock_period_us=period,
        clock_phase_us=phase,
        clock_coherence=float(coherence[k]),
        transitions=len(edges),
        nearest_preamble_bit_errors=minimum,
        unvalidated_candidates=candidates,
    ), ratio


def parse_payload(payload):
    """Validate and unpack the21-byte VEVOR payload; no bit correction is applied."""
    b = bytes.fromhex(payload) if isinstance(payload, str) else bytes(payload)
    if len(b) != 21 or b[:2] != b"\xaa\x00":
        raise ValueError("Invalid VEVOR payload header")
    if (sum(b[:19]) & 255) != b[19]:
        raise ValueError("VEVOR checksum mismatch")
    if b[20] != ((b[18] + 1) & 255):
        raise ValueError("VEVOR counter relationship mismatch")
    adj = lambda i: (b[i] - 1) & 255
    temperature = (((b[5] & 15) << 8 | b[6]) - 500) / 10
    light = (adj(16) << 8) | adj(17)
    if light & 32768:
        light = (light & 32767) * 10
    return dict(
        sensor_id=f"{b[2]:02X}{b[3]:02X}",
        temperature_C=temperature,
        temperature_F=round(temperature * 1.8 + 32, 2),
        humidity_percent=b[7],
        wind_avg_km_h=round(((adj(8) << 8) | adj(9)) / 8.333, 2),
        wind_gust_km_h=round(b[10] * 1.25, 2),
        wind_direction_degrees=(adj(11) << 8) | adj(12),
        total_rain_mm=round(((adj(13) << 8) | adj(14)) * 0.233, 3),
        uv_index=(b[15] & 31) - 1,
        light_lux=light,
        battery_ok=not bool(b[4] & 128),
        counter=b[18],
        next_counter=b[20],
        checksum_valid=True,
        counter_valid=True,
        payload_hex=b.hex(),
    )
