import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import codex_usage_counter as app
from test_codex_usage_counter import _session_event


class RealtimeTests(unittest.TestCase):
    def make_reader(self, directory):
        reader = app.CodexTelemetryReader(directory)
        reader.sessions_dir.mkdir()
        return reader

    def write(self, path, timestamp, weekly=10, five_hour=20):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_session_event(timestamp, weekly, five_hour)) + '\n', encoding='utf-8')
        # Same-size rewrites can share a filesystem timestamp on fast Windows runs.
        os.utime(path, ns=(int(timestamp * 1e9), int(timestamp * 1e9)))

    def test_newer_downward_correction_replaces_both_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = self.make_reader(directory)
            path = reader.sessions_dir / 'task.jsonl'
            self.write(path, 100, 80, 90)
            reader.read()
            self.write(path, 200, 70, 75)
            result = reader.read()
            self.assertEqual((result.used_percent, result.five_hour_used_percent), (70, 75))
            self.assertEqual(result.timestamp, 200)

    def test_older_event_does_not_overwrite_newer_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = self.make_reader(directory)
            path = reader.sessions_dir / 'task.jsonl'
            self.write(path, 200, 70, 75)
            reader.read()
            self.write(path, 100, 90, 90)
            result = reader.read()
            self.assertEqual(result.timestamp, 200)
            self.assertEqual(result.used_percent, 70)

    def test_history_keeps_confirmed_same_window_correction(self):
        points = [
            {'timestamp': 100, 'used_percent': 80, 'resets_at': 10000},
            {'timestamp': 200, 'used_percent': 70, 'resets_at': 10000},
        ]
        self.assertEqual([p['used_percent'] for p in app.UsageHistory._sanitize(points)], [80, 70])

    def test_watcher_covers_session_beyond_first_eight(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = self.make_reader(directory)
            for index in range(12):
                self.write(reader.sessions_dir / f'{index}.jsonl', 100 + index)
            reader.read()
            self.assertEqual(len(reader._watch_signatures), 12)
            self.assertFalse(reader.local_signal_changed())
            path = Path(list(reader._watch_signatures)[-1])
            with path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(_session_event(300, 30, 40)) + '\n')
            self.assertTrue(reader.local_signal_changed())
            self.assertEqual(reader.read().timestamp, 300)

    def test_fast_refresh_avoids_recursive_discovery_until_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = self.make_reader(directory)
            self.write(reader.sessions_dir / 'task.jsonl', 100)
            with patch.object(app.time, 'monotonic', return_value=100):
                reader.read()
            original = Path.rglob
            with patch.object(app.time, 'monotonic', return_value=105), patch.object(Path, 'rglob', side_effect=AssertionError('recursive scan')):
                self.assertEqual(reader.read().timestamp, 100)
            with patch.object(app.time, 'monotonic', return_value=131), patch.object(Path, 'rglob', autospec=True, side_effect=original) as scan:
                self.assertTrue(reader.local_signal_changed())
                reader.read()
                self.assertEqual(scan.call_count, 1)

    def test_new_today_session_is_discovered_without_recursive_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = self.make_reader(directory)
            self.write(reader.sessions_dir / 'old.jsonl', 100)
            reader.read()
            self.write(reader._current_day_directory() / 'new.jsonl', 300, 30, 40)
            self.assertTrue(reader.local_signal_changed())
            with patch.object(Path, 'rglob', side_effect=AssertionError('recursive scan')):
                result = reader.read()
            self.assertEqual(result.timestamp, 300)

    def test_day_rollover_detected_even_when_both_directories_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = self.make_reader(directory)
            self.write(reader.sessions_dir / 'task.jsonl', 100)
            reader.read()
            with patch.object(reader, '_current_day_directory', return_value=reader.sessions_dir / 'tomorrow'):
                self.assertTrue(reader.local_signal_changed())

    def test_new_yesterday_session_discovered_on_fast_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = self.make_reader(directory)
            self.write(reader.sessions_dir / 'old.jsonl', 100)
            reader.read()
            yesterday = datetime.now() - timedelta(days=1)
            path = reader.sessions_dir / yesterday.strftime('%Y/%m/%d') / 'new.jsonl'
            self.write(path, 300)
            with patch.object(Path, 'rglob', side_effect=AssertionError('recursive scan')):
                self.assertEqual(reader.read().timestamp, 300)

    def test_full_context_ignores_non_object_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'task.jsonl'
            path.write_text('["model"]\n' + json.dumps(_session_event(100, 10, 20)), encoding='utf-8')
            self.assertEqual(app.CodexTelemetryReader(directory)._full_file_context(path)[0], 'gpt-6-astra')

    def test_tray_error_cannot_strand_completed_reader(self):
        counter = app.UsageApp.__new__(app.UsageApp)
        counter.root = Mock()
        counter.tray = Mock()
        counter.tray.poll_actions.side_effect = RuntimeError('tray action failed')
        counter._refresh_results = app.queue.Queue()
        snapshot = app.UsageSnapshot(used_percent=40)
        counter._refresh_results.put(snapshot)
        counter._finish_refresh = Mock()
        with patch.object(app, '_log_exception'):
            counter._poll_tray()
        counter._finish_refresh.assert_called_once_with(snapshot)
        counter.root.after.assert_called_once_with(100, counter._poll_tray)

    def test_legacy_polling_setting_is_ignored_and_not_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            with patch.object(app, 'CONFIG_FILE', path), patch.object(app, 'CONFIG_DIR', Path(directory)):
                path.write_text(json.dumps({'refresh_interval_seconds': 900, 'display_mode': 'remaining'}), encoding='utf-8')
                settings = app.AppSettings.load()
                self.assertFalse(hasattr(settings, 'refresh_interval_seconds'))
                self.assertEqual(settings.display_mode, 'remaining')
                settings.save()
                self.assertNotIn('refresh_interval_seconds', json.loads(path.read_text(encoding='utf-8')))

    def test_automatic_fallback_runs_every_two_seconds(self):
        counter = app.UsageApp.__new__(app.UsageApp)
        counter.refresh_after_id = None
        counter.root = Mock()
        counter._schedule_next_refresh()
        counter.root.after.assert_called_once_with(2000, counter._poll_refresh)

    def test_hidden_main_window_updates_tray_without_rendering_canvas(self):
        counter = app.UsageApp.__new__(app.UsageApp)
        counter.root = Mock()
        counter.root.winfo_viewable.return_value = False
        counter.snapshot = app.UsageSnapshot(used_percent=40)
        counter.canvas = Mock()
        counter._update_tray = Mock()
        counter._draw()
        counter._update_tray.assert_called_once_with(counter.snapshot)
        counter.canvas.delete.assert_not_called()

    def test_stale_tray_cannot_look_like_live_numeric_usage(self):
        counter = app.UsageApp.__new__(app.UsageApp)
        counter.settings = app.AppSettings(display_mode='remaining')
        counter.root = Mock()
        counter.tray = Mock()
        counter.last_tray_tooltip = None
        counter._set_tray_icon = Mock()
        with patch.object(app.time, 'time', return_value=1000):
            counter._update_tray(app.UsageSnapshot(used_percent=40, timestamp=100))
        counter._set_tray_icon.assert_called_once_with(None)
        self.assertIn('STALE', counter.tray.update_tooltip.call_args.args[0])

    def test_statistics_redraw_only_for_changed_visible_data(self):
        for changed, visible in ((False, True), (True, False), (True, True)):
            with self.subTest(changed=changed, visible=visible):
                counter = app.UsageApp.__new__(app.UsageApp)
                counter.snapshot = app.UsageSnapshot(used_percent=10)
                result = app.UsageSnapshot(used_percent=20) if changed else counter.snapshot
                counter.refresh_button = Mock()
                counter._maybe_show_milestone = Mock()
                counter.history = Mock()
                counter._draw = Mock()
                counter._render_statistics = Mock()
                counter.stats_window = Mock()
                counter.stats_window.winfo_viewable.return_value = visible
                counter._schedule_next_refresh = Mock()
                counter._finish_refresh(result)
                self.assertEqual(counter._render_statistics.call_count, int(changed and visible))
                self.assertFalse(counter.refresh_in_flight)
                counter._schedule_next_refresh.assert_called_once()


if __name__ == '__main__':
    unittest.main()
