import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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


if __name__ == '__main__':
    unittest.main()
