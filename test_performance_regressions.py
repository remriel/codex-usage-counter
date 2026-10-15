"""Correctness and bounded-work regressions using isolated synthetic data."""

import json
from pathlib import Path
import random
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import account_usage
import codex_usage_counter as app
from test_codex_usage_counter import _session_event


def token_event(timestamp, total):
    return {"timestamp": timestamp, "payload": {"type": "token_count", "info": {
        "total_token_usage": {"total_tokens": total, "input_tokens": total - 5},
        "last_token_usage": {"total_tokens": 5}}}}


class TelemetryEdgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.reader = app.CodexTelemetryReader(self.temp.name)
        self.reader.sessions_dir.mkdir()
        self.path = self.reader.sessions_dir / "test.jsonl"

    def write(self, *events):
        self.path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")

    def append(self, *events):
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write("".join(json.dumps(e) + "\n" for e in events))

    def test_token_only_events_update_tokens_without_freshening_allowance(self):
        self.write(_session_event(100, 10, 20), token_event(110, 200))
        first = self.reader.read()
        self.assertEqual(first.total_tokens, 200)
        self.assertEqual(first.timestamp, 100)
        self.append(token_event(120, 300))
        second = self.reader.read()
        self.assertEqual(second.total_tokens, 300)
        self.assertEqual(second.timestamp, 100)

    def test_out_of_order_context_does_not_replace_newer_context(self):
        self.write(_session_event(200, 20, 30))
        self.reader.read()
        self.append({"timestamp": 100, "payload": {"model": "old", "effort": "low"}})
        result = self.reader.read()
        self.assertEqual((result.model, result.reasoning_effort), ("gpt-6-astra", "ultra"))

    def test_older_token_event_does_not_replace_newer_tokens(self):
        self.write(_session_event(100, 10, 20), token_event(200, 300), token_event(150, 100))
        self.assertEqual(self.reader.read().total_tokens, 300)

    def test_appends_do_not_reparse_the_existing_tail(self):
        self.write(*(_session_event(i, 10, 20) for i in range(1000)))
        self.reader.read()
        self.append(_session_event(2000, 30, 40))
        with patch.object(app.json, "loads", wraps=json.loads) as loads:
            result = self.reader.read()
        self.assertEqual(result.used_percent, 30)
        self.assertLessEqual(loads.call_count, 3)

    def test_partial_utf8_line_is_retried_after_append(self):
        self.write(_session_event(100, 10, 20))
        raw = json.dumps({"timestamp": 200, "payload": {"model": "modèle", "effort": "high"}},
                         ensure_ascii=False).encode("utf-8")
        split = raw.index("è".encode("utf-8")) + 1
        with self.path.open("ab") as stream:
            stream.write(raw[:split])
        self.reader.read()
        with self.path.open("ab") as stream:
            stream.write(raw[split:] + b"\n")
        self.assertEqual(self.reader.read().model, "modèle")

    def test_replaced_larger_file_does_not_inherit_previous_context(self):
        self.write(_session_event(100, 10, 20))
        self.reader.read()
        replacement = self.path.with_suffix(".tmp")
        event = _session_event(200, 30, 40)
        del event["payload"]["model"]
        del event["payload"]["effort"]
        replacement.write_text(json.dumps(event) + "\n" + " " * 1000, encoding="utf-8")
        replacement.replace(self.path)
        result = self.reader.read()
        self.assertEqual(result.used_percent, 30)
        self.assertIsNone(result.model)

    def test_empty_directory_does_not_force_recursive_scan_every_poll(self):
        with patch.object(app.time, "monotonic", return_value=100):
            self.reader.read()
        with patch.object(app.time, "monotonic", return_value=102), \
                patch.object(Path, "rglob", side_effect=AssertionError("repeated recursive scan")):
            self.reader.read()


class HistoryEdgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "history.json"
        self.history = app.UsageHistory(self.path)

    def test_invalid_history_timestamps_are_rejected(self):
        self.assertIsNone(app.UsageHistory._point({"timestamp": 1e308, "used_percent": 10}))

    def test_epoch_zero_is_stale(self):
        with patch.object(app.time, "time", return_value=1000):
            self.assertTrue(app.UsageSnapshot(timestamp=0, used_percent=10).is_stale)

    def test_record_only_validates_recent_history(self):
        now = time.time()
        self.history.points = [{"timestamp": now - (10000 - i) * 60, "used_percent": 10.0}
                               for i in range(10000)]
        self.history.save = Mock()
        with patch.object(app.UsageHistory, "_point", wraps=app.UsageHistory._point) as validate:
            self.history.record(app.UsageSnapshot(timestamp=now, used_percent=11))
        self.assertLess(validate.call_count, 20)
        self.assertEqual(self.history.points[-1]["used_percent"], 11)

    def test_incremental_record_matches_full_sanitization(self):
        now = time.time()
        rng = random.Random(42)
        expected = []
        self.history.save = Mock()
        for i in range(350):
            timestamp = now - 500 + i * 2
            usage = rng.choice([20, 20, 20, 10, 30])
            resets = now + 10000 if i % 7 else None
            point = {"timestamp": timestamp, "used_percent": float(usage)}
            if resets is not None:
                point["resets_at"] = resets
            expected = app.UsageHistory._sanitize([*expected, point])
            with patch.object(app.time, "time", return_value=timestamp):
                self.history.record(app.UsageSnapshot(timestamp=timestamp, used_percent=usage, resets_at=resets))
            self.assertEqual(self.history.points, expected, f"sample {i}")

    def test_failed_history_replace_keeps_previous_json(self):
        self.path.write_text('[{"timestamp":100,"used_percent":10}]', encoding="utf-8")
        previous = self.path.read_bytes()
        self.history.points = [{"timestamp": 200, "used_percent": 20}]
        with patch.object(app.os, "replace", side_effect=OSError("disk busy")):
            self.history.save(force=True)
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])


class TransportCleanupTests(unittest.TestCase):
    def test_disconnect_tolerates_process_exit_during_kill(self):
        client = account_usage.AccountUsageClient(Path("."))
        process = Mock()
        process.wait.side_effect = account_usage.subprocess.TimeoutExpired("codex", 2)
        process.kill.side_effect = OSError("already exited")
        client._process = process
        client._disconnect()
        self.assertIsNone(client._process)
        process.stdin.close.assert_called_once()
        process.stdout.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
