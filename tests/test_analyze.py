"""`ai analyze`: does a tasks file's `(AC: N)` tags cover every acceptance criterion, and
does every tag point at a criterion that actually exists.
"""
import contextlib
import io
import json
import sys
import tempfile
import textwrap
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import analyze  # noqa: E402


def contract(acceptance='[]'):
    return textwrap.dedent(f'''\
        objective: "x"
        acceptance: {acceptance}
        must_not_change: []
        risk_notes: []
        ''')


class ParseTasksFileTests(unittest.TestCase):
    def test_reads_checked_and_unchecked_with_and_without_tags(self):
        text = textwrap.dedent('''\
            # Tasks
            - [ ] Add the column (AC: 1)
            - [x] Wire the endpoint (AC: 2, 3)
            - [ ] Cleanup, no criteria
            Some prose that is not a checklist item.
            ''')
        tasks = analyze.parse_tasks_file(text)
        self.assertEqual(len(tasks), 3)
        self.assertEqual(tasks[0], {'line': 2, 'text': 'Add the column', 'checked': False, 'ac_refs': [1]})
        self.assertEqual(tasks[1], {'line': 3, 'text': 'Wire the endpoint', 'checked': True, 'ac_refs': [2, 3]})
        self.assertEqual(tasks[2], {'line': 4, 'text': 'Cleanup, no criteria', 'checked': False, 'ac_refs': []})

    def test_duplicate_refs_in_one_tag_are_deduplicated_and_sorted(self):
        tasks = analyze.parse_tasks_file('- [ ] x (AC: 3, 1, 3)')
        self.assertEqual(tasks[0]['ac_refs'], [1, 3])

    def test_empty_file_yields_no_tasks(self):
        self.assertEqual(analyze.parse_tasks_file(''), [])


class AnalyzeCoverageTests(unittest.TestCase):
    def test_every_criterion_covered_is_ready(self):
        report = analyze.analyze_coverage(
            contract(acceptance='["one", "two"]'),
            [{'line': 1, 'text': 'a', 'checked': False, 'ac_refs': [1]},
             {'line': 2, 'text': 'b', 'checked': False, 'ac_refs': [2]}])
        self.assertEqual(report['status'], 'READY')
        self.assertEqual(report['blockers'], [])
        self.assertEqual(report['covered'], [1, 2])

    def test_uncovered_criterion_blocks(self):
        report = analyze.analyze_coverage(
            contract(acceptance='["one", "two"]'),
            [{'line': 1, 'text': 'a', 'checked': False, 'ac_refs': [1]}])
        self.assertEqual(report['status'], 'NEEDS_HUMAN')
        self.assertEqual(report['uncovered'], [{'index': 2, 'text': 'two'}])
        self.assertTrue(any('criterion 2' in b for b in report['blockers']))

    def test_out_of_range_ref_is_reported_and_blocks(self):
        report = analyze.analyze_coverage(
            contract(acceptance='["one"]'),
            [{'line': 5, 'text': 'a', 'checked': False, 'ac_refs': [1, 9]}])
        self.assertEqual(report['status'], 'NEEDS_HUMAN')
        self.assertEqual(report['invalid_refs'], [{'line': 5, 'text': 'a', 'refs': [9]}])

    def test_untagged_task_is_reported_but_does_not_block(self):
        report = analyze.analyze_coverage(
            contract(acceptance='["one"]'),
            [{'line': 1, 'text': 'covers it', 'checked': False, 'ac_refs': [1]},
             {'line': 2, 'text': 'groundwork', 'checked': False, 'ac_refs': []}])
        self.assertEqual(report['status'], 'READY')
        self.assertEqual(report['untagged'], [{'line': 2, 'text': 'groundwork'}])

    def test_no_acceptance_criteria_blocks(self):
        report = analyze.analyze_coverage(contract(acceptance='[]'), [{'line': 1, 'text': 'x', 'checked': False, 'ac_refs': []}])
        self.assertEqual(report['status'], 'NEEDS_HUMAN')
        self.assertTrue(any('no acceptance criteria' in b for b in report['blockers']))

    def test_no_tasks_at_all_blocks(self):
        report = analyze.analyze_coverage(contract(acceptance='["one"]'), [])
        self.assertEqual(report['status'], 'NEEDS_HUMAN')
        self.assertTrue(any('no checklist items' in b for b in report['blockers']))


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='analyze-cmd-')
        self.addCleanup(self.temp.cleanup)
        self.task = Path(self.temp.name)
        (self.task / 'contracts').mkdir(parents=True)
        (self.task / 'state').mkdir()
        for target in ('analyze.git_root', 'analyze.repo_state'):
            patcher = patch(target, return_value=self.task)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('analyze.task_state', return_value=self.task)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tasks_file = self.task / 'tasks.md'

    def write_contract(self, acceptance='[]'):
        (self.task / 'contracts' / 'current-pr.yml').write_text(contract(acceptance=acceptance))

    def report(self):
        return json.loads((self.task / 'state' / 'analyze.json').read_text())

    def run_analyze(self, tasks_file=None):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            analyze.cmd_analyze(Namespace(json=False, tasks_file=str(tasks_file or self.tasks_file)))
        return out.getvalue()

    def test_missing_contract_is_needs_human(self):
        with self.assertRaises(SystemExit) as ctx:
            self.run_analyze()
        self.assertIn('no PR contract', str(ctx.exception))

    def test_missing_tasks_file_is_a_clean_error(self):
        self.write_contract(acceptance='["one"]')
        with self.assertRaises(SystemExit) as ctx:
            self.run_analyze(self.task / 'does-not-exist.md')
        self.assertIn('not found', str(ctx.exception))

    def test_full_coverage_exits_zero_and_writes_report(self):
        self.write_contract(acceptance='["one", "two"]')
        self.tasks_file.write_text('- [ ] a (AC: 1)\n- [ ] b (AC: 2)\n')
        self.run_analyze()
        self.assertEqual(self.report()['status'], 'READY')

    def test_uncovered_criterion_exits_needs_human_but_still_writes_report(self):
        self.write_contract(acceptance='["one", "two"]')
        self.tasks_file.write_text('- [ ] a (AC: 1)\n')
        with self.assertRaises(SystemExit) as ctx:
            self.run_analyze()
        self.assertIn('NEEDS_HUMAN', str(ctx.exception))
        self.assertEqual(self.report()['status'], 'NEEDS_HUMAN')

    def test_the_contract_and_tasks_file_are_never_rewritten(self):
        self.write_contract(acceptance='["one"]')
        self.tasks_file.write_text('- [ ] a (AC: 1)\n')
        before_contract = (self.task / 'contracts' / 'current-pr.yml').read_text()
        before_tasks = self.tasks_file.read_text()
        self.run_analyze()
        self.assertEqual((self.task / 'contracts' / 'current-pr.yml').read_text(), before_contract)
        self.assertEqual(self.tasks_file.read_text(), before_tasks)

    def test_json_output_is_the_full_report(self):
        self.write_contract(acceptance='["one"]')
        self.tasks_file.write_text('- [ ] a (AC: 1)\n')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            analyze.cmd_analyze(Namespace(json=True, tasks_file=str(self.tasks_file)))
        self.assertEqual(json.loads(out.getvalue())['status'], 'READY')


if __name__ == '__main__':
    unittest.main()
