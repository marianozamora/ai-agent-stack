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
