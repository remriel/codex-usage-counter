import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

import codex_usage_counter as app


class TelemetryTests(unittest.TestCase):
    def test_model_labels(self):
        for family in ('astra', 'sol', 'terra', 'luna'):
            self.assertEqual(app.format_prominent_context('gpt-6-' + family, 'high'), family.upper() + ' · HIGH')
        self.assertEqual(app.format_prominent_context('astra', 'ultra'), 'ASTRA · ULTRA')
        self.assertEqual(app.format_prominent_context('custom-model', None), 'CUSTOM-MODEL')

    def test_nonfinite_numbers(self):
        for value in ('NaN', 'Infinity', '-Infinity', float('nan'), 10 ** 400):
            self.assertIsNone(app.number(value))
        self.assertIsNone(app.parse_timestamp(float('inf')))
        self.assertEqual(app.number('12.5'), 12.5)

    def test_astra_and_malformed_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            events = [
                ['model', 'rate_limits'],
                {'timestamp': 100, 'type': 'turn_context', 'payload': {'model': 'gpt-6-astra', 'effort': 'ultra'}},
                {'timestamp': 101, 'payload': {'rate_limits': {
                    'primary': {'window_minutes': 10080, 'used_percent': 12},
                    'secondary': {'window_minutes': 300, 'used_percent': 34},
                }}},
                {'timestamp': 102, 'payload': {'rate_limits': {'primary': {'window_minutes': 'Infinity', 'used_percent': 'NaN'}}}},
            ]
            path.write_text('\n'.join(map(json.dumps, events)), encoding='utf-8')
            reader = app.CodexTelemetryReader()
            result = reader._read_file(path, 103, scan_full_context=True)
            self.assertEqual(result.model, 'gpt-6-astra')
            self.assertEqual(result.reasoning_effort, 'ultra')
            self.assertEqual(result.used_percent, 12)
            self.assertEqual(result.five_hour_used_percent, 34)

    def test_discovery_keeps_newest_48_and_skips_deleted_files(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = app.CodexTelemetryReader()
            reader.sessions_dir = Path(directory)
            for index in range(60):
                path = reader.sessions_dir / f'{index}.jsonl'
                path.touch()
                os.utime(path, (1000 + index, 1000 + index))
            missing = reader.sessions_dir / 'deleted.jsonl'
            paths = list(reader.sessions_dir.glob('*.jsonl')) + [missing]
            with patch.object(Path, 'rglob', return_value=iter(paths)):
                result = reader._candidate_files()
            self.assertEqual([int(path.stem) for path in result], list(range(59, 11, -1)))


class AppRegressionTests(unittest.TestCase):
    def make_app(self):
        counter = app.UsageApp.__new__(app.UsageApp)
        counter.settings = app.AppSettings()
        counter.last_alert_buckets = {}
        counter.last_alert_windows = {}
        counter.tray_popup = Mock()
        counter._play_sound_alert = Mock()
        return counter

    def test_resets_combined_deduplicated_and_respect_display(self):
        counter = self.make_app()
        counter.settings.display_mode = 'remaining'
        counter.settings.sound_alert = True
        with patch.object(app.time, 'time', return_value=10000):
            counter._maybe_show_milestone(app.UsageSnapshot(timestamp=9990,
                used_percent=90, five_hour_used_percent=80, resets_at=9995, five_hour_resets_at=9995))
            counter.tray_popup.show.assert_not_called()
            reset = app.UsageSnapshot(timestamp=10000, used_percent=2,
                five_hour_used_percent=3, resets_at=614795, five_hour_resets_at=27995)
            counter._maybe_show_milestone(reset)
            counter._maybe_show_milestone(reset)
            counter._maybe_show_milestone(app.UsageSnapshot(timestamp=10001, used_percent=2,
                five_hour_used_percent=3, resets_at=614795, five_hour_resets_at=27995))
        counter.tray_popup.show.assert_called_once_with('5H reset · 97% remaining  ·  WEEK reset · 98% remaining')
        counter._play_sound_alert.assert_called_once()

    def test_corrections_stale_and_out_of_order_do_not_alert(self):
        counter = self.make_app()
        with patch.object(app.time, 'time', return_value=10000):
            for timestamp, value in ((9900, 80), (9950, 70), (9940, 95), (100, 99)):
                counter._maybe_show_milestone(app.UsageSnapshot(timestamp=timestamp,
                    used_percent=value, resets_at=11000))
        counter.tray_popup.show.assert_not_called()

    def test_reset_without_percentage_drop(self):
        counter = self.make_app()
        with patch.object(app.time, 'time', return_value=10000):
            counter._maybe_show_milestone(app.UsageSnapshot(timestamp=9990, used_percent=1, resets_at=9995))
            counter._maybe_show_milestone(app.UsageSnapshot(timestamp=10000, used_percent=2, resets_at=614795))
        self.assertIn('reset', counter.tray_popup.show.call_args.args[0])

    def test_historical_eta_uses_sample_clock(self):
        with patch.object(app.time, 'time', return_value=100000):
            self.assertEqual(app.UsageApp._format_eta(50, 10, 4600, 1000), 'after reset')
            self.assertEqual(app.UsageApp._format_eta(50, 10, 4600), '5.0h')

    def test_pan_redraws_are_coalesced(self):
        counter = self.make_app()
        counter.root = Mock()
        counter.root.after.return_value = 'timer'
        counter.stats_canvas = Mock()
        counter.stats_daily_view = counter.stats_weekly_view = False
        counter.stats_pan_start_x = 50
        counter.stats_pan_start_end = 10000
        counter.stats_pan_redraw_id = None
        counter.stats_plot_left, counter.stats_plot_right = 0, 100
        counter.stats_period_hours = 1
        counter.history = Mock(points=[{'timestamp': 1000}])
        counter._render_statistics = Mock()
        with patch.object(app.time, 'time', return_value=10000):
            for x in (55, 60, 65):
                counter._pan_statistics(Mock(x=x))
        counter.root.after.assert_called_once()
        counter._render_statistics.assert_not_called()
        counter._end_statistics_pan(None)
        counter._render_statistics.assert_called_once()
        self.assertIsNone(counter.stats_pan_redraw_id)

    def test_cards_have_one_outline(self):
        counter = self.make_app()
        counter.canvas = Mock()
        counter._rounded_rect(0, 0, 100, 50, 10, 'black', 'white')
        counter.canvas.create_polygon.assert_called_once()
        counter.canvas.create_oval.assert_not_called()


if __name__ == '__main__':
    unittest.main()
