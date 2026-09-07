import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

spec = importlib.util.spec_from_file_location('workflow', Path(__file__).resolve().parents[1] / 'ai_stack/workflow.py')
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


class HelpersTests(unittest.TestCase):
    def test_missing_invalid_and_partial_usage(self):
        self.assertEqual(workflow.usage_from_verdict({'usage': {
            'input_tokens': True, 'output_tokens': -1, 'cost_usd': float('nan')}}), {})
        self.assertEqual(workflow.usage_from_verdict({'usage': {'input_tokens': 1.5}}), {})
        rows = [{'event': 'gate', 'task_key': 'a', 'gate': 'checks', 'passed': False,
                 'usage': {'cost_usd': 0.02}},
                {'event': 'gate', 'task_key': 'b', 'gate': 'checks', 'passed': True, 'usage': {}}]
        report = workflow.summarize(rows)
        self.assertEqual(report['repeated_gate_attempts'], 0)
        self.assertEqual(report['usage']['cost_usd']['reported_attempts'], 1)
        self.assertIsNone(report['usage']['input_tokens']['reported_total'])

    def test_normalize_finding_folds_paths_lines_and_hex(self):
        a = workflow.normalize_finding('Leaked token 9f2c1a4b7d30ffab in src/auth.py:42')
        b = workflow.normalize_finding('Leaked token 00112233445566aa in src/auth.py:107')
        self.assertEqual(a, b)
        self.assertEqual(a, 'leaked token <hex> in src/auth.py:<n>')
        self.assertEqual(workflow.finding_signature('X'), workflow.finding_signature('x  '))
        self.assertEqual(len(workflow.finding_signature('anything')), 12)
        self.assertEqual(workflow.normalize_finding('x' * 500), ('x' * 500).lower()[:160])

    def test_detect_patterns_requires_distinct_tasks_and_tracks_resolution(self):
        rows = [
            {'event': 'gate', 'task_key': 't1', 'gate': 'cleanup', 'attempt': 1, 'passed': False,
             'profile': 'fast', 'risk': 'LOW', 'task_type': 'feature', 'ts': 1,
             'findings': [{'hash': 'abc123456789', 'text': 'leftover debug in src/app.py:<n>'}]},
            {'event': 'gate', 'task_key': 't1', 'gate': 'cleanup', 'attempt': 2, 'passed': True,
             'profile': 'fast', 'risk': 'LOW', 'task_type': 'feature', 'ts': 2, 'findings': []},
            {'event': 'gate', 'task_key': 't2', 'gate': 'cleanup', 'attempt': 1, 'passed': False,
             'profile': 'standard', 'risk': 'MEDIUM', 'task_type': 'bug', 'ts': 3,
             'findings': [{'hash': 'abc123456789', 'text': 'leftover debug in src/app.py:<n>'}]},
            {'event': 'gate', 'task_key': 't3', 'gate': 'cleanup', 'attempt': 1, 'passed': False,
             'profile': 'fast', 'risk': 'LOW', 'task_type': 'feature', 'ts': 4,
             'findings': [{'hash': 'zzz999', 'text': 'unrelated, only seen once per task'}]},
        ]
        patterns = workflow.detect_patterns(rows)
        self.assertEqual(len(patterns), 1)
        pattern = patterns[0]
        self.assertEqual(pattern['occurrences'], 2)
        self.assertEqual(pattern['distinct_tasks'], 2)
        self.assertEqual(pattern['resolved_next_attempt'], 1)
        self.assertEqual(pattern['by_profile'], {'fast': 1, 'standard': 1})
        self.assertEqual(pattern['scope_hint'], 'src/**')

    def test_outcome_stats_marks_low_evidence_below_min_n(self):
        thin = [
            {'event': 'gate', 'gate': 'checks', 'passed': True, 'attempt': 1, 'task_key': 't1', 'profile': 'fast'},
            {'event': 'gate', 'gate': 'checks', 'passed': True, 'attempt': 1, 'task_key': 't2', 'profile': 'fast'},
        ]
        stats = workflow.outcome_stats(thin, profile='fast', min_n=5)
        self.assertFalse(stats[0]['sufficient'])
        self.assertNotIn('pass_rate', stats[0])

        rich = [{'event': 'gate', 'gate': 'checks', 'passed': i != 0, 'attempt': 2 if i == 0 else 1,
                 'task_key': f't{i}', 'profile': 'fast',
                 'usage': {'input_tokens': 100, 'output_tokens': 10}} for i in range(6)]
        stats = workflow.outcome_stats(rich, profile='fast', min_n=5)
        row = stats[0]
        self.assertTrue(row['sufficient'])
        self.assertEqual(row['n'], 6)
        self.assertAlmostEqual(row['pass_rate'], 5 / 6, places=3)
        self.assertEqual(row['median_usage_tokens'], 110)

    def test_bucket_ts_uses_utc_day_and_iso_week(self):
        import datetime
        ts = datetime.datetime(2026, 1, 1, 23, 30, tzinfo=datetime.timezone.utc).timestamp()
        self.assertEqual(workflow.bucket_ts(ts, 'day'), '2026-01-01')
        self.assertEqual(workflow.bucket_ts(ts, 'week'), '2026-W01')
        with self.assertRaises(ValueError):
            workflow.bucket_ts(ts, 'month')

    def test_parse_window_relative_and_absolute(self):
        now = time.time()
        self.assertAlmostEqual(workflow.parse_window('1d'), now - 86400, delta=2)
        self.assertAlmostEqual(workflow.parse_window('2w'), now - 2 * 604800, delta=2)
        import datetime
        expected = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc).timestamp()
        self.assertEqual(workflow.parse_window('2026-01-01'), expected)
        with self.assertRaises(ValueError):
            workflow.parse_window('nonsense')

    def test_usage_report_groups_windows_and_never_zero_fills(self):
        rows = [
            {'event': 'gate', 'ts': 100, 'gate': 'checks', 'passed': True, 'task_id': 't1', 'task_key': 'k1',
             'usage': {'input_tokens': 100, 'output_tokens': 10}},
            {'event': 'gate', 'ts': 100, 'gate': 'checks', 'passed': False, 'task_id': 't1', 'task_key': 'k1',
             'usage': {}},
            {'event': 'gate', 'ts': 200000, 'gate': 'cleanup', 'passed': True, 'task_id': 't2', 'task_key': 'k2',
             'usage': {'input_tokens': 5, 'output_tokens': 1}},
            {'event': 'pipeline', 'ts': 200000, 'status': 'PR_READY', 'task_id': 't2', 'task_key': 'k2'},
        ]
        by_gate = workflow.usage_report(rows, group_by='gate')
        checks = next(r for r in by_gate if r['key'] == 'checks')
        self.assertEqual(checks['gate_attempts'], 2)
        self.assertEqual(checks['reported_attempts'], 1)
        self.assertEqual(checks['unreported_attempts'], 1)
        self.assertEqual(checks['input_tokens'], 100)

        by_task = workflow.usage_report(rows, group_by='task', since=1000)
        self.assertEqual(len(by_task), 1)
        self.assertEqual(by_task[0]['key'], 'k2')
        self.assertEqual(by_task[0]['task_id'], 't2')
        self.assertEqual(by_task[0]['pipeline_runs'], 1)

        top = workflow.usage_report(rows, group_by='gate', top=1)
        self.assertEqual(len(top), 1)
        self.assertEqual(top[0]['key'], 'checks')

    def test_timeout_kills_child_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'survived'
            child = f'import time; time.sleep(0.8); open({str(marker)!r}, "w").write("alive")'
            parent = f'import subprocess, sys, time; subprocess.Popen([sys.executable, "-c", {child!r}]); time.sleep(10)'
            with (root / 'log').open('w') as output:
                code = workflow.execute([sys.executable, '-c', parent], root, dict(os.environ), output, 0.3)
            self.assertEqual(code, 124)
            time.sleep(0.8)
            self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
