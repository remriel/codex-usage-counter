"""Repeatable synthetic hot-path benchmark; never reads a user's sessions/history."""

import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import codex_usage_counter as app


def measure(action, repeats=5):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        action()
        samples.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(samples), 3)


def run():
    now = time.time()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        history = app.UsageHistory(root / "history.json")
        history.points = [
            {"timestamp": now - (43200 - i) * 60, "used_percent": (i % 1000) / 10,
             "resets_at": now + 604800, "five_hour_used_percent": (i % 100) / 2,
             "five_hour_resets_at": now + 18000, "session_id": "benchmark",
             "total_tokens": i * 500}
            for i in range(43200)
        ]
        # Separate history computation from disk throughput.
        history.save = lambda **kwargs: None
        counter = 0

        def record():
            nonlocal counter
            counter += 1
            history.record(app.UsageSnapshot(timestamp=now + counter, used_percent=20 + counter / 100,
                                            resets_at=now + 604800, total_tokens=21600000 + counter * 500,
                                            source_path="benchmark.jsonl"))

        results = {"history_points": len(history.points), "record_ms": measure(record),
                   "recent_chart_ms": measure(lambda: history.chart_points(1)),
                   "recent_rate_ms": measure(lambda: history.rate_series(1))}
        reader = app.CodexTelemetryReader(str(root))
        reader.sessions_dir.mkdir()
        log = reader.sessions_dir / "benchmark.jsonl"

        def event(index):
            return json.dumps({"timestamp": now + index, "payload": {
                "rate_limits": {"primary": {"window_minutes": 10080, "used_percent": 20 + index / 100}},
                "model": "benchmark", "effort": "high"}}) + "\n"

        # Dense valid telemetry in a multi-megabyte file exposes repeated parsing.
        log.write_text("".join(event(i / 1000) for i in range(20000)), encoding="utf-8")
        reader.read()

        def append_and_read():
            nonlocal counter
            counter += 1
            with log.open("a", encoding="utf-8") as stream:
                stream.write(event(30 + counter))
            reader.read()

        results["session_bytes"] = log.stat().st_size
        results["append_refresh_ms"] = measure(append_and_read)
        results["unchanged_refresh_ms"] = measure(reader.read)
        return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = json.dumps(run(), indent=2)
    if args.output:
        args.output.write_text(report + "\n", encoding="utf-8")
    print(report)
