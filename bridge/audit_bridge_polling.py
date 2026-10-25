"""Audit EDN8 polling patterns used by the hardware bridge.

This script uses bridge_core.cc_client.CCClient, so it exercises the current
checksum-correct CC framing rather than the older audit helpers.

Examples:
    uv run python audit_bridge_polling.py --port COM5 --pattern tloz_all --duration 300
    uv run python audit_bridge_polling.py --port COM5 --pattern inventory --method array --hz 10 --duration 120
    uv run python audit_bridge_polling.py --port COM5 --pattern inventory --method addrs --hz 10 --duration 120
    uv run python audit_bridge_polling.py --port COM5 --pattern map --hz 2 --stop-on-anomaly
"""
from __future__ import annotations

import argparse
import time
from collections.abc import Iterable

from bridge_core.cc_client import MAX_READ_ADDRS, CCClient
from bridge_core.modes import tloz_all
from bridge_core.sync_engine import SyncEngine

Range = tuple[int, int]
ADDRS_CHUNK_SIZE = MAX_READ_ADDRS
SAFE_ADDRS_RANGE_LIMIT = 64


def ranges_for_pattern(pattern: str, *, addr: int | None = None, length: int | None = None) -> list[Range]:
    if pattern == "tloz_all":
        return list(tloz_all.READ_RANGES)
    if pattern == "running":
        return [(tloz_all.RUNNING_ADDR, 1)]
    if pattern == "inventory":
        return [(0x0657, 0x26)]
    if pattern == "map":
        return [(0x067F, 0x80), (0x06FF, 0x80), (0x077F, 0x80)]
    if pattern == "custom":
        if addr is None or length is None:
            raise ValueError("--addr and --length are required for --pattern custom")
        return [(addr, length)]
    raise ValueError(f"unknown pattern: {pattern}")


def addresses_for_ranges(ranges: Iterable[Range]) -> list[int]:
    addresses: list[int] = []
    for base, length in ranges:
        addresses.extend(range(base, base + length))
    return addresses


def estimated_cc_transactions(ranges: Iterable[Range], *, method: str) -> int:
    if method == "array":
        return sum(1 for _base, _length in ranges)
    if method == "addrs":
        return sum((length + ADDRS_CHUNK_SIZE - 1) // ADDRS_CHUNK_SIZE for _base, length in ranges)
    raise ValueError(f"unknown read method: {method}")


def audit_shape_warning(ranges: Iterable[Range], *, method: str, allow_heavy: bool) -> str | None:
    ranges = list(ranges)
    if method != "addrs" or allow_heavy:
        return None
    large_ranges = [(base, length) for base, length in ranges if length > SAFE_ADDRS_RANGE_LIMIT]
    if not large_ranges:
        return None
    formatted = " ".join(f"0x{base:04X}+{length}" for base, length in large_ranges)
    tx = estimated_cc_transactions(ranges, method=method)
    return (
        "refusing high-risk addrs audit over large ranges "
        f"({formatted}); this shape would issue about {tx} CC transactions per tick. "
        "Use --method array for full sweeps, narrow --pattern custom/--pattern inventory, "
        "or pass --allow-heavy only for deliberate stress testing."
    )


def _range_values(snapshot: dict[int, int], base: int, length: int) -> list[int]:
    return [snapshot.get(base + offset, 0) for offset in range(length)]


def analyze_snapshot(snapshot: dict[int, int], ranges: Iterable[Range]) -> list[str]:
    anomalies: list[str] = []
    engine = SyncEngine(endpoint=None, mode=tloz_all)
    reason = engine.implausible_snapshot_reason(snapshot)
    if reason:
        anomalies.append(reason)

    for base, length in ranges:
        if length < 8:
            continue
        values = _range_values(snapshot, base, length)
        ff_count = sum(1 for value in values if value == 0xFF)
        if ff_count / length >= 0.75:
            anomalies.append(f"0x{base:04X}+{length} is {ff_count * 100 // length}% FF")
    return anomalies


def _hex_range(snapshot: dict[int, int], base: int, length: int, max_bytes: int = 64) -> str:
    values = _range_values(snapshot, base, min(length, max_bytes))
    suffix = "..." if length > max_bytes else ""
    return "".join(f"{value:02X}" for value in values) + suffix


def _read_ranges_with_timings(
    client: CCClient,
    ranges: list[Range],
    timeout_ms: int,
    *,
    method: str = "array",
) -> tuple[dict[int, int], list[tuple[int, int, float | None]]]:
    snapshot: dict[int, int] = {}
    timings: list[tuple[int, int, float | None]] = []
    for base, length in ranges:
        started = time.perf_counter()
        if method == "array":
            data = client.read_array(base, length, timeout_ms=timeout_ms)
        elif method == "addrs":
            data = client.read_addrs(addresses_for_ranges([(base, length)]), timeout_ms=timeout_ms)
        else:
            raise ValueError(f"unknown read method: {method}")
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if data is None or len(data) != length:
            timings.append((base, length, None))
            continue
        timings.append((base, length, elapsed_ms))
        for offset, value in enumerate(data):
            snapshot[base + offset] = value
    return snapshot, timings


def _format_timings(timings: list[tuple[int, int, float | None]]) -> str:
    parts = []
    for base, length, elapsed_ms in timings:
        if elapsed_ms is None:
            parts.append(f"0x{base:04X}+{length}:timeout")
        else:
            parts.append(f"0x{base:04X}+{length}:{elapsed_ms:.1f}ms")
    return " ".join(parts)


def run_audit(args: argparse.Namespace) -> int:
    import serial

    ranges = ranges_for_pattern(args.pattern, addr=args.addr, length=args.length)
    warning = audit_shape_warning(ranges, method=args.method, allow_heavy=args.allow_heavy)
    if warning:
        print("ERROR: " + warning)
        return 2

    sp = serial.Serial(args.port, baudrate=args.baud, timeout=0)
    client = CCClient(sp)
    period = 1.0 / args.hz
    deadline = time.time() + args.duration
    tick = 0
    total_reads = 0
    total_timeouts = 0
    consecutive_timeouts = 0

    print("# EDN8 bridge polling audit")
    print(
        f"# pattern={args.pattern} method={args.method} hz={args.hz} "
        f"duration={args.duration}s timeout_ms={args.timeout_ms}"
    )
    print("# ranges=" + " ".join(f"0x{base:04X}+{length}" for base, length in ranges))
    print(
        f"# estimated_cc_transactions_per_tick={estimated_cc_transactions(ranges, method=args.method)} "
        f"estimated_cc_transactions_per_second={estimated_cc_transactions(ranges, method=args.method) * args.hz:.1f}"
    )
    print("# Uses bridge_core.cc_client.CCClient checksum-correct framing.")
    print()

    started_at = time.time()
    while time.time() < deadline:
        tick += 1
        loop_start = time.time()
        snapshot, timings = _read_ranges_with_timings(client, ranges, args.timeout_ms, method=args.method)
        total_reads += len(ranges)
        timeouts = sum(1 for _base, _length, elapsed_ms in timings if elapsed_ms is None)
        total_timeouts += timeouts
        if timeouts:
            consecutive_timeouts += 1
        else:
            consecutive_timeouts = 0

        anomalies = analyze_snapshot(snapshot, ranges) if snapshot else []
        elapsed = time.time() - started_at
        if args.verbose or timeouts or anomalies or tick % args.summary_every == 0:
            print(
                f"tick={tick} t={elapsed:.1f}s reads={total_reads} "
                f"timeouts={total_timeouts} timings={_format_timings(timings)}"
            )
        if anomalies:
            print("  ANOMALY: " + " | ".join(anomalies))
            for base, length in ranges:
                print(f"  RAW 0x{base:04X}+{length}: {_hex_range(snapshot, base, length)}")
            if args.stop_on_anomaly:
                break
        if consecutive_timeouts >= args.abort_after_timeouts:
            print(f"ABORT: {consecutive_timeouts} consecutive timeout ticks")
            break

        sleep = period - (time.time() - loop_start)
        if sleep > 0:
            time.sleep(sleep)

    print()
    print(f"# Summary: ticks={tick} reads={total_reads} timeouts={total_timeouts}")
    client.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--pattern",
        choices=("tloz_all", "running", "inventory", "map", "custom"),
        default="tloz_all",
    )
    parser.add_argument("--addr", type=lambda value: int(value, 0))
    parser.add_argument("--length", type=lambda value: int(value, 0))
    parser.add_argument("--method", choices=("array", "addrs"), default="array")
    parser.add_argument("--hz", type=float, default=10.0)
    parser.add_argument("--duration", type=float, default=120.0)
    parser.add_argument("--timeout-ms", type=int, default=300)
    parser.add_argument("--summary-every", type=int, default=10)
    parser.add_argument("--abort-after-timeouts", type=int, default=5)
    parser.add_argument("--stop-on-anomaly", action="store_true")
    parser.add_argument("--allow-heavy", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return run_audit(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
