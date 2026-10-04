import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import account_usage
import codex_usage_counter as app


def account_payload(used=20):
    return {"rateLimitsByLimitId": {"codex": {
        "limitId": "codex", "planType": "pro",
        "primary": {"usedPercent": used, "windowDurationMins": 300, "resetsAt": 18000},
        "secondary": {"usedPercent": 45, "windowDurationMins": 10080, "resetsAt": 604800},
    }}}


class CombinedReaderTests(unittest.TestCase):
    def reader(self, local, state):
        reader = app.CombinedUsageReader.__new__(app.CombinedUsageReader)
        reader.local = Mock()
        reader.local.read.return_value = local
        reader.local.local_signal_changed.return_value = False
        reader.account = Mock()
        reader.account.snapshot.return_value = state
        reader._account_revision = -1
        return reader

    def test_cloud_usage_updates_with_frozen_local_files(self):
        local = app.UsageSnapshot(timestamp=100, used_percent=5, total_tokens=123,
                                  model="old-model", source_path="old.jsonl")
        reader = self.reader(local, (account_payload(20), 1000, None, 1))
        with patch.object(app.time, "time", return_value=1000):
            first = reader.read()
            reader.account.snapshot.return_value = (account_payload(27), 1015, None, 2)
            self.assertTrue(reader.local_signal_changed())
            second = reader.read()
        self.assertEqual((first.five_hour_used_percent, second.five_hour_used_percent), (20, 27))
        self.assertEqual(second.allowance_source, "account")
        self.assertEqual(second.timestamp, 1015)
        self.assertIsNone(second.total_tokens)
        self.assertIsNone(second.model)
        self.assertEqual(second.source_path, "codex-account-usage")

    def test_account_works_without_any_local_sessions(self):
        reader = self.reader(app.UsageSnapshot(error="no sessions"), (account_payload(), 1000, None, 1))
        result = reader.read()
        self.assertTrue(result.has_data)
        self.assertIsNone(result.error)

    def test_only_recent_local_metadata_is_merged(self):
        local = app.UsageSnapshot(timestamp=990, used_percent=5, total_tokens=123,
                                  model="local-model", source_path="local.jsonl")
        reader = self.reader(local, (account_payload(), 1000, None, 1))
        with patch.object(app.time, "time", return_value=1000):
            result = reader.read()
        self.assertEqual(result.used_percent, 45)
        self.assertEqual(result.total_tokens, 123)
        self.assertEqual(result.model, "local-model")

    def test_failure_does_not_freshen_last_success(self):
        reader = self.reader(app.UsageSnapshot(timestamp=90, used_percent=1),
                             (account_payload(), 100, "Account unavailable", 2))
        with patch.object(app.time, "time", return_value=1000):
            result = reader.read()
            self.assertTrue(result.is_stale)
        self.assertEqual(result.timestamp, 100)
        self.assertEqual(result.error, "Account unavailable")

    def test_failure_uses_newer_local_telemetry(self):
        reader = self.reader(app.UsageSnapshot(timestamp=900, used_percent=50),
                             (account_payload(), 100, "Account unavailable", 2))
        result = reader.read()
        self.assertEqual(result.timestamp, 900)
        self.assertEqual(result.used_percent, 50)
        self.assertEqual(result.allowance_source, "local")

    def test_bucket_isolation_and_swapped_windows(self):
        payload = account_payload()
        bucket = payload["rateLimitsByLimitId"]["codex"]
        bucket["primary"], bucket["secondary"] = bucket["secondary"], bucket["primary"]
        result = app.CombinedUsageReader.account_snapshot(payload, 1000)
        self.assertEqual((result.five_hour_used_percent, result.used_percent), (20, 45))
        payload["rateLimitsByLimitId"] = {"premium": bucket}
        payload["rateLimits"] = bucket
        self.assertIsNone(app.CombinedUsageReader.account_snapshot(payload, 1000))

    def test_legacy_and_invalid_payloads(self):
        bucket = account_payload()["rateLimitsByLimitId"]["codex"]
        self.assertTrue(app.CombinedUsageReader.account_snapshot({"rateLimits": bucket}, 1000).has_data)
        for payload in (None, {}, {"rateLimits": []}, {"rateLimits": {"primary": {"usedPercent": "NaN"}}}):
            self.assertIsNone(app.CombinedUsageReader.account_snapshot(payload, 1000))


class TransportTests(unittest.TestCase):
    def test_handshake_read_profile_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            client = account_usage.AccountUsageClient(Path(directory))
            process = Mock()
            process.poll.return_value = None
            process.stdin = io.StringIO()
            process.stdout = io.StringIO("\n".join(map(json.dumps, [
                {"id": 1, "result": {"userAgent": "test"}},
                {"method": "ignoredNotification", "params": {}},
                {"id": 999, "result": {}},
                {"id": 2, "result": account_payload()},
            ])) + "\n")
            with patch.object(client, "executable", return_value="codex.exe"), \
                    patch.object(account_usage.subprocess, "Popen", return_value=process) as popen, \
                    patch.dict(account_usage.os.environ, {"OPENAI_API_KEY": "not-a-real-key"}):
                client._connect()
                result = client._request("account/rateLimits/read")
                sent = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
                self.assertEqual([item["method"] for item in sent],
                                 ["initialize", "initialized", "account/rateLimits/read"])
                self.assertEqual(result, account_payload())
                environment = popen.call_args.kwargs["env"]
                self.assertEqual(environment["CODEX_HOME"], directory)
                self.assertNotIn("OPENAI_API_KEY", environment)
                self.assertEqual(popen.call_args.kwargs["stderr"], account_usage.subprocess.DEVNULL)
                client._disconnect()
                process.terminate.assert_called_once()
                process.wait.assert_called_once_with(timeout=2)

    def test_errors_are_redacted_and_request_has_deadline(self):
        client = account_usage.AccountUsageClient(Path("."))
        client._process = Mock()
        client._process.poll.return_value = None
        client._messages.put({"id": 1, "error": {"message": "401 private-upstream-value"}})
        with self.assertRaisesRegex(RuntimeError, "^Account sign-in required in Codex$"):
            client._request("account/rateLimits/read")
        with patch.object(account_usage, "REQUEST_TIMEOUT_SECONDS", 0.01):
            with self.assertRaises(TimeoutError):
                client._request("account/rateLimits/read")

    def test_error_preserves_previous_snapshot_timestamp(self):
        client = account_usage.AccountUsageClient(Path("."))
        client._publish(account_payload(), 100)
        client._publish(error="offline")
        self.assertEqual(client._state, (account_payload(), 100, "offline", 2))

    def test_snapshot_is_nonblocking_while_network_waits_and_close_stops_worker(self):
        client = account_usage.AccountUsageClient(Path("."))
        entered = account_usage.threading.Event()
        def blocked_connect():
            entered.set()
            client._stop.wait(2)
            raise OSError("closed")
        with patch.object(client, "_connect", side_effect=blocked_connect):
            start = time.monotonic()
            client.snapshot()
            self.assertLess(time.monotonic() - start, 0.5)
            self.assertTrue(entered.wait(1))
            client.close()
            self.assertFalse(client._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
