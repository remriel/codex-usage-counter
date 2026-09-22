import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

import codex_usage_counter as app


_FALL_BACK_AT = 1_000_000.0


class _NonWholeHourLocalTime(datetime):
    """datetime stand-in pinning the local timezone to UTC+05:30."""

    @classmethod
    def fromtimestamp(cls, timestamp, tz=None):
        return super().fromtimestamp(timestamp, tz=timezone(timedelta(hours=5, minutes=30)))

    def astimezone(self, tz=None):
        return self if tz is None else super().astimezone(tz)


class _RepeatedHourLocalTime(datetime):
    """datetime stand-in whose local zone falls back UTC-04:00 to UTC-05:00 at a fixed instant."""

    @classmethod
    def fromtimestamp(cls, timestamp, tz=None):
        offset = timedelta(hours=-4) if timestamp < _FALL_BACK_AT else timedelta(hours=-5)
        return super().fromtimestamp(timestamp, tz=timezone(offset))

    def astimezone(self, tz=None):
        return self if tz is None else super().astimezone(tz)


def _history_with_points(points):
    history = app.UsageHistory.__new__(app.UsageHistory)
    history.points = points
    history._last_saved_at = 0.0
    history._daily_cache_key = None
    history._daily_cache = []
    return history


def _history_point(timestamp):
    return {"timestamp": float(timestamp), "used_percent": 1.0}


def _session_event(timestamp, weekly_used, five_hour_used, resets_at=10000.0):
    """One session line carrying model/effort context plus both allowance windows."""

    return {
        'timestamp': timestamp,
        'payload': {
            'model': 'gpt-6-astra',
            'effort': 'ultra',
            'rate_limits': {
                'plan_type': 'pro',
                'primary': {'window_minutes': 10080, 'used_percent': weekly_used, 'resets_at': resets_at},
                'secondary': {'window_minutes': 300, 'used_percent': five_hour_used, 'resets_at': resets_at},
            },
        },
    }


class _QuietTray:
    def __init__(self, *_args):
        pass

    def start(self, *_args):
        pass

    def poll_actions(self):
        pass

    def update_tooltip(self, *_args):
        pass

    def update_icon(self, *_args):
        pass

    def stop(self):
        pass


@unittest.skipUnless(os.name == 'nt', 'Tk statistics view is Windows-only')
class StatisticsRenderTests(unittest.TestCase):
    def test_empty_history_renders_hourly_daily_and_weekly(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(app, 'TrayIcon', _QuietTray), \
                patch.object(app.AppSettings, 'load', return_value=app.AppSettings(codex_home=directory)), \
                patch.object(app, 'history_path_for_codex_home', return_value=Path(directory) / 'empty-history.json'):
            try:
                counter = app.UsageApp()
            except app.tk.TclError as exc:
                self.skipTest(f'Tk display unavailable: {exc}')
            callback_errors = []
            counter.root.report_callback_exception = lambda _type, value, _trace: callback_errors.append(value)
            try:
                counter.root.withdraw()
                counter.open_statistics()
                self.assertIsNotNone(counter.stats_live_card_data.get('timestamp'))
                counter.set_stats_daily()
                counter.set_stats_weekly()
                counter.set_stats_hourly()
                counter.root.update_idletasks()
                self.assertEqual(callback_errors, [])
            finally:
                counter.close_statistics()
                counter.root.destroy()


class UsageHistoryHourlyTests(unittest.TestCase):
    def test_hourly_collapses_by_local_hour_with_non_whole_hour_offset(self):
        # UTC+05:30: 99000/101000 are 03:30/04:03 UTC, both inside the 09:xx local hour.
        points = [_history_point(96300), _history_point(99000), _history_point(101000), _history_point(106200)]
        history = _history_with_points(points)
        with patch.object(app.time, "time", return_value=110000.0), patch.object(app, "datetime", _NonWholeHourLocalTime):
            result = history.hourly(24)
        self.assertEqual([point["timestamp"] for point in result], [96300.0, 101000.0, 106200.0])

    def test_hourly_uses_local_hour_boundary_not_utc_hour_boundary(self):
        # UTC+05:30: local 09:00 begins at 03:30 UTC, mid-way through the UTC hour.
        points = [_history_point(98999), _history_point(99000)]
        history = _history_with_points(points)
        with patch.object(app.time, "time", return_value=110000.0), patch.object(app, "datetime", _NonWholeHourLocalTime):
            result = history.hourly(24)
        self.assertEqual([point["timestamp"] for point in result], [98999.0, 99000.0])

    def test_hourly_keeps_distinct_repeated_dst_hours(self):
        first = _FALL_BACK_AT - 3000.0   # 01:11 -04:00
        second = _FALL_BACK_AT + 600.0   # same 01:11 wall clock, one hour later at -05:00
        history = _history_with_points([_history_point(first), _history_point(second)])
        with patch.object(app.time, "time", return_value=1_010_000.0), patch.object(app, "datetime", _RepeatedHourLocalTime):
            result = history.hourly(24)
        self.assertEqual([point["timestamp"] for point in result], [first, second])


class TelemetryTests(unittest.TestCase):
    def test_token_rate_series_keeps_observed_and_smoothed_rates(self):
        history = app.UsageHistory()
        points = [
            {"timestamp": 1000.0, "used_percent": 1.0, "resets_at": 10000.0, "total_tokens": 100.0, "session_id": "task"},
            {"timestamp": 1060.0, "used_percent": 1.1, "resets_at": 10000.0, "total_tokens": 1100.0, "session_id": "task"},
            {"timestamp": 1120.0, "used_percent": 1.2, "resets_at": 10000.0, "total_tokens": 1700.0, "session_id": "task"},
        ]
        series = history.token_rate_series(1, points)
        self.assertEqual(len(series), 2)
        self.assertEqual(series[0]["raw_token_rate_per_minute"], 1000.0)
        self.assertEqual(series[1]["raw_token_rate_per_minute"], 600.0)
        self.assertLess(series[1]["token_rate_per_minute"], series[0]["token_rate_per_minute"])

    def test_model_labels(self):
        for family in ('astra', 'sol', 'luna'):
            self.assertEqual(app.format_prominent_context('gpt-6-' + family, 'high'), 'GPT-6 ' + family.upper() + ' · HIGH')
        for family in ('sol', 'terra', 'luna'):
            self.assertEqual(app.format_prominent_context('gpt-5.6-' + family, 'high'), 'GPT-5.6 ' + family.upper() + ' · HIGH')
        self.assertEqual(app.format_prominent_context('astra', 'ultra'), 'ASTRA · ULTRA')
        self.assertEqual(app.format_prominent_context('custom-model', None), 'CUSTOM-MODEL')

    def test_nonfinite_numbers(self):
        for value in ('NaN', 'Infinity', '-Infinity', float('nan'), 10 ** 400):
            self.assertIsNone(app.number(value))
        self.assertIsNone(app.parse_timestamp(float('inf')))
        self.assertIsNone(app.parse_timestamp(1e308))
        self.assertEqual(app.number('12.5'), 12.5)

    def test_non_object_settings_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / 'settings.json'
            with patch.object(app, 'CONFIG_FILE', settings_path):
                for value in ('null', '[]', '"invalid"'):
                    with self.subTest(value=value):
                        settings_path.write_text(value, encoding='utf-8')
                        self.assertEqual(app.AppSettings.load(), app.AppSettings())

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

    def test_new_model_context_does_not_make_old_allowances_live(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            events = [
                _session_event(100, 12, 34),
                {'timestamp': 900, 'type': 'turn_context', 'payload': {'model': 'gpt-6-sol', 'effort': 'max'}},
            ]
            path.write_text('\n'.join(map(json.dumps, events)), encoding='utf-8')
            reader = app.CodexTelemetryReader()
            with patch.object(app.time, 'time', return_value=1000):
                result = reader._read_file(path, 900)
                self.assertTrue(result.is_stale)
            self.assertEqual(result.timestamp, 100)
            self.assertEqual(result.context_timestamp, 900)
            self.assertEqual(result.model, 'gpt-6-sol')

    def test_context_only_session_updates_model_without_freshening_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = app.CodexTelemetryReader()
            reader.sessions_dir = Path(directory)
            (reader.sessions_dir / 'allowance.jsonl').write_text(json.dumps(_session_event(100, 12, 34)), encoding='utf-8')
            (reader.sessions_dir / 'context.jsonl').write_text(
                json.dumps({'timestamp': 900, 'type': 'turn_context', 'payload': {'model': 'gpt-6-luna', 'effort': 'high'}}),
                encoding='utf-8',
            )
            with patch.object(app.time, 'time', return_value=1000):
                result = reader.read()
                self.assertTrue(result.is_stale)
            self.assertEqual(result.timestamp, 100)
            self.assertEqual(result.context_timestamp, 900)
            self.assertEqual(result.model, 'gpt-6-luna')
            self.assertEqual(result.reasoning_effort, 'high')
            self.assertEqual(result.used_percent, 12)

    def test_newer_non_codex_limit_does_not_replace_core_allowance(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = app.CodexTelemetryReader()
            reader.sessions_dir = Path(directory)
            core = _session_event(100, 12, 34)
            core['payload']['rate_limits']['limit_id'] = 'codex'
            premium = _session_event(200, 99, 99)
            premium['payload']['rate_limits']['limit_id'] = 'premium'
            path = reader.sessions_dir / 'session.jsonl'
            path.write_text('\n'.join(map(json.dumps, (core, premium))), encoding='utf-8')
            result = reader.read()
            self.assertEqual(result.timestamp, 100)
            self.assertEqual(result.used_percent, 12)
            self.assertEqual(result.five_hour_used_percent, 34)

    def test_refresh_worker_hands_result_to_main_thread_without_tk_call(self):
        counter = app.UsageApp.__new__(app.UsageApp)
        counter.refresh_in_flight = False
        counter.refresh_button = Mock()
        counter.root = Mock()
        counter.reader = Mock()
        result = app.UsageSnapshot(used_percent=12)
        counter.reader.read.return_value = result
        counter._refresh_results = app.queue.Queue()
        with patch.object(app.threading, 'Thread') as thread_type:
            counter.refresh_async()
            worker = thread_type.call_args.kwargs['target']
            worker()
            thread_type.return_value.start.assert_called_once()
        counter.root.after.assert_not_called()
        counter.tray = Mock()
        counter._finish_refresh = Mock()
        counter.root.winfo_exists.return_value = True
        counter._poll_tray()
        counter._finish_refresh.assert_called_once_with(result)
        counter.root.after.assert_called_once_with(100, counter._poll_tray)

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
            self.assertEqual([int(path.stem) for path, _metadata in result], list(range(59, 11, -1)))

    def test_unchanged_file_second_read_stats_candidate_once_and_preserves_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = app.CodexTelemetryReader()
            reader.sessions_dir = Path(directory)
            session_path = Path(directory) / 'session.jsonl'
            session_path.write_text(json.dumps(_session_event(100, 10, 20)), encoding='utf-8')
            real_stat = Path.stat
            candidate_stats = []

            def counting_stat(path, *args, **kwargs):
                if path == session_path:
                    candidate_stats.append(path)
                return real_stat(path, *args, **kwargs)

            with patch.object(Path, 'stat', counting_stat):
                first = reader.read()
                candidate_stats.clear()
                second = reader.read()
                second_refresh_stats = len(candidate_stats)
            # The cache-hit refresh must not re-stat the selected candidate after discovery.
            self.assertEqual(second_refresh_stats, 1)
            self.assertEqual(first, second)
            self.assertEqual(second.used_percent, 10)
            self.assertEqual(second.five_hour_used_percent, 20)
            self.assertEqual(second.timestamp, 100.0)

    def test_changed_file_invalidates_cache_and_refreshes_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = app.CodexTelemetryReader()
            reader.sessions_dir = Path(directory)
            session_path = Path(directory) / 'session.jsonl'
            session_path.write_text(json.dumps(_session_event(100, 10, 20)), encoding='utf-8')
            first = reader.read()
            session_path.write_text(json.dumps(_session_event(200, 30, 40)), encoding='utf-8')
            os.utime(session_path, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
            second = reader.read()
            self.assertEqual(first.used_percent, 10)
            self.assertNotEqual(first, second)
            self.assertEqual(second.used_percent, 30)
            self.assertEqual(second.five_hour_used_percent, 40)
            self.assertEqual(second.timestamp, 200.0)
            metadata = os.stat(session_path)
            cached = reader._file_cache[str(session_path)]
            self.assertEqual(cached[:2], (metadata.st_mtime_ns, metadata.st_size))
            self.assertEqual(cached[2], second)


class SourceSelectionTests(unittest.TestCase):
    def test_explicit_setting_wins_over_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            explicit = Path(directory) / 'explicit-chatgpt'
            legacy = Path(directory) / 'legacy-deepseek'
            (legacy / 'sessions').mkdir(parents=True)
            with patch.dict(os.environ, {'CODEX_HOME': str(legacy)}), \
                    patch.object(app, 'chatgpt_codex_home', return_value=legacy):
                reader = app.CodexTelemetryReader(str(explicit))
            self.assertEqual(reader.codex_home, explicit)
            self.assertEqual(reader.sessions_dir, explicit / 'sessions')

    def test_chatgpt_home_preferred_over_environment_when_present(self):
        with tempfile.TemporaryDirectory() as directory:
            chatgpt = Path(directory) / '.codex-chatgpt'
            legacy = Path(directory) / 'legacy-deepseek'
            (chatgpt / 'sessions').mkdir(parents=True)
            (legacy / 'sessions').mkdir(parents=True)
            with patch.dict(os.environ, {'CODEX_HOME': str(legacy)}), \
                    patch.object(app, 'chatgpt_codex_home', return_value=chatgpt):
                reader = app.CodexTelemetryReader()
            self.assertEqual(reader.codex_home, chatgpt)
            self.assertEqual(reader.sessions_dir, chatgpt / 'sessions')

    def test_explicit_missing_path_does_not_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            explicit = Path(directory) / 'missing-chatgpt'
            chatgpt = Path(directory) / '.codex-chatgpt'
            (chatgpt / 'sessions').mkdir(parents=True)
            with patch.dict(os.environ, {'CODEX_HOME': str(chatgpt)}), \
                    patch.object(app, 'chatgpt_codex_home', return_value=chatgpt):
                reader = app.CodexTelemetryReader(str(explicit))
                result = reader.read()
            self.assertEqual(reader.sessions_dir, explicit / 'sessions')
            self.assertFalse(reader.sessions_dir.exists())
            self.assertEqual(result.error, 'Codex session telemetry is not available yet')

    def test_legacy_behavior_without_split_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory) / 'legacy-codex'
            (legacy / 'sessions').mkdir(parents=True)
            absent_chatgpt = Path(directory) / 'absent-chatgpt'
            with patch.dict(os.environ, {'CODEX_HOME': str(legacy)}), \
                    patch.object(app, 'chatgpt_codex_home', return_value=absent_chatgpt):
                reader = app.CodexTelemetryReader()
            self.assertEqual(reader.sessions_dir, legacy / 'sessions')

            with patch.dict(os.environ), patch.object(
                app, 'chatgpt_codex_home', return_value=absent_chatgpt
            ):
                os.environ.pop('CODEX_HOME', None)
                reader = app.CodexTelemetryReader()
            self.assertEqual(reader.sessions_dir, Path.home() / '.codex' / 'sessions')

    def test_codex_home_setting_defaults_empty_and_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory)
            config_file = config_dir / 'settings.json'
            config_file.write_text(json.dumps({'display_mode': 'remaining'}), encoding='utf-8')
            with patch.object(app, 'CONFIG_DIR', config_dir), patch.object(app, 'CONFIG_FILE', config_file):
                settings = app.AppSettings.load()
                self.assertEqual(settings.codex_home, '')
                settings.codex_home = r'C:\Users\Gev\.codex-chatgpt'
                settings.save()
                persisted = json.loads(config_file.read_text(encoding='utf-8'))
                self.assertEqual(persisted['codex_home'], r'C:\Users\Gev\.codex-chatgpt')
                self.assertEqual(app.AppSettings.load().codex_home, r'C:\Users\Gev\.codex-chatgpt')


class HistoryIsolationTests(unittest.TestCase):
    def test_default_codex_home_keeps_legacy_history_file(self):
        config_dir = Path('sentinel-config')
        legacy_file = config_dir / 'usage_history.json'
        with patch.object(app, 'HISTORY_FILE', legacy_file):
            path = app.history_path_for_codex_home(Path.home() / '.codex')
        self.assertEqual(path, legacy_file)

    def test_chatgpt_home_gets_deterministic_hashed_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / 'config'
            chatgpt = root / '.codex-chatgpt'
            (chatgpt / 'sessions').mkdir(parents=True)
            with patch.object(app, 'CONFIG_DIR', config_dir), \
                    patch.object(app, 'HISTORY_FILE', config_dir / 'usage_history.json'):
                first = app.history_path_for_codex_home(chatgpt)
                second = app.history_path_for_codex_home(chatgpt)
                default = app.history_path_for_codex_home(Path.home() / '.codex')
            self.assertEqual(first, second)
            self.assertEqual(first.parent, config_dir)
            self.assertEqual(default, config_dir / 'usage_history.json')
            self.assertNotEqual(first, default)
            self.assertRegex(first.name, r'^usage_history-[0-9a-f]{16}\.json$')
            self.assertNotIn(chatgpt.name, first.name)

    def test_equivalent_normalized_home_paths_share_one_history_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / 'config'
            chatgpt = root / '.codex-chatgpt'
            (chatgpt / 'sessions').mkdir(parents=True)
            equivalent = chatgpt / 'sessions' / '..'
            with patch.object(app, 'CONFIG_DIR', config_dir):
                direct = app.history_path_for_codex_home(chatgpt)
                round_trip = app.history_path_for_codex_home(equivalent)
            self.assertEqual(direct, round_trip)
            self.assertRegex(direct.name, r'^usage_history-[0-9a-f]{16}\.json$')

    @unittest.skipUnless(os.name == 'nt', 'case-insensitive path comparison is Windows-specific')
    def test_windows_case_variants_share_one_history_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / 'config'
            chatgpt = root / '.codex-chatgpt'
            with patch.object(app, 'CONFIG_DIR', config_dir):
                direct = app.history_path_for_codex_home(chatgpt)
                variant = app.history_path_for_codex_home(Path(str(chatgpt).upper()))
            self.assertEqual(direct, variant)

    def test_two_sources_do_not_share_history_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / 'config'
            chatgpt = root / '.codex-chatgpt'
            with patch.object(app, 'CONFIG_DIR', config_dir), \
                    patch.object(app, 'HISTORY_FILE', config_dir / 'usage_history.json'):
                default_path = app.history_path_for_codex_home(Path.home() / '.codex')
                chatgpt_path = app.history_path_for_codex_home(chatgpt)
                default_history = app.UsageHistory(default_path)
                with patch.object(app.time, 'time', return_value=10_000.0):
                    default_history.record(app.UsageSnapshot(
                        used_percent=10.0, window_minutes=10080, resets_at=100_000.0, timestamp=9_999.0))
                reloaded_default = app.UsageHistory(default_path)
                reloaded_chatgpt = app.UsageHistory(chatgpt_path)
            self.assertEqual(len(reloaded_default.points), 1)
            self.assertEqual(reloaded_chatgpt.points, [])
            self.assertTrue(default_path.exists())
            self.assertFalse(chatgpt_path.exists())

            with patch.object(app.time, 'time', return_value=20_000.0):
                reloaded_chatgpt.record(app.UsageSnapshot(
                    used_percent=90.0, window_minutes=10080, resets_at=100_000.0, timestamp=19_999.0))
            default_payload = json.loads(default_path.read_text(encoding='utf-8'))
            chatgpt_payload = json.loads(chatgpt_path.read_text(encoding='utf-8'))
            self.assertEqual([point['used_percent'] for point in default_payload], [10.0])
            self.assertEqual([point['used_percent'] for point in chatgpt_payload], [90.0])


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
