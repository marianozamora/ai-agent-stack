"""`ai tasks-to-issues`: turn a tasks file (or the contract's acceptance criteria) into
GitHub issues. Dry run by default; `--execute` is `require_human()`-guarded and shells out
to `gh issue create`, both mocked here -- no test ever touches a real GitHub repository.
"""
import contextlib
import io
import sys
import tempfile
import textwrap
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import issues  # noqa: E402


def contract(acceptance='[]'):
    return textwrap.dedent(f'''\
        objective: "x"
        acceptance: {acceptance}
        must_not_change: []
        risk_notes: []
        ''')


class PlannedIssuesTests(unittest.TestCase):
    def test_from_tasks_only_unchecked_tasks_become_issues(self):
        tasks = [{'line': 1, 'text': 'do it', 'checked': False, 'ac_refs': [1]},
                 {'line': 2, 'text': 'already done', 'checked': True, 'ac_refs': [2]}]
        planned = issues.planned_issues(contract(acceptance='["a", "b"]'), tasks)
        self.assertEqual([p['title'] for p in planned], ['do it'])
        self.assertIn('AC 1', planned[0]['body'])

    def test_untagged_task_gets_a_body_saying_so(self):
        tasks = [{'line': 1, 'text': 'groundwork', 'checked': False, 'ac_refs': []}]
        planned = issues.planned_issues(contract(), tasks)
        self.assertIn('no acceptance criteria tagged', planned[0]['body'])

    def test_falls_back_to_one_issue_per_acceptance_criterion(self):
        planned = issues.planned_issues(contract(acceptance='["one", "two"]'), None)
        self.assertEqual([p['title'] for p in planned], ['AC 1: one', 'AC 2: two'])


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='issues-cmd-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task = self.root  # git_root() and task_state() both point here for simplicity
        (self.task / 'contracts').mkdir(parents=True)
        for target in ('issues.git_root', 'issues.repo_state'):
            patcher = patch(target, return_value=self.root)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('issues.task_state', return_value=self.task)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_contract(self, acceptance='[]'):
        (self.task / 'contracts' / 'current-pr.yml').write_text(contract(acceptance=acceptance))

    def test_missing_contract_is_needs_human(self):
        with self.assertRaises(SystemExit) as ctx:
            issues.cmd_tasks_to_issues(Namespace(tasks_file=None, execute=False, json=False))
        self.assertIn('no PR contract', str(ctx.exception))

    def test_dry_run_prints_without_calling_gh(self):
        self.write_contract(acceptance='["one"]')
        with patch('issues.run') as run_mock, contextlib.redirect_stdout(io.StringIO()) as out:
            issues.cmd_tasks_to_issues(Namespace(tasks_file=None, execute=False, json=False))
        run_mock.assert_not_called()
        self.assertIn('DRY RUN', out.getvalue())
        self.assertIn('AC 1: one', out.getvalue())

    def test_execute_is_refused_inside_a_gate(self):
        self.write_contract(acceptance='["one"]')
        with patch.dict('os.environ', {'AI_GATE': 'checks'}):
            with self.assertRaises(SystemExit) as ctx:
                issues.cmd_tasks_to_issues(Namespace(tasks_file=None, execute=True, json=False))
        self.assertIn('human-only', str(ctx.exception))

    def test_execute_without_gh_installed_is_a_clean_error(self):
        self.write_contract(acceptance='["one"]')
        with patch('issues.shutil.which', return_value=None):
            with self.assertRaises(SystemExit) as ctx:
                issues.cmd_tasks_to_issues(Namespace(tasks_file=None, execute=True, json=False))
        self.assertIn('gh', str(ctx.exception))

    def test_execute_calls_gh_issue_create_once_per_issue(self):
        self.write_contract(acceptance='["one", "two"]')
        with patch('issues.shutil.which', return_value='/usr/bin/gh'), \
             patch('issues.run', side_effect=['https://github.com/x/y/issues/1',
                                              'https://github.com/x/y/issues/2']) as run_mock, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            issues.cmd_tasks_to_issues(Namespace(tasks_file=None, execute=True, json=False))
        self.assertEqual(run_mock.call_count, 2)
        self.assertIn('https://github.com/x/y/issues/1', out.getvalue())
        self.assertIn('https://github.com/x/y/issues/2', out.getvalue())

    def test_nothing_to_file_prints_and_does_not_call_gh(self):
        self.write_contract(acceptance='[]')
        with patch('issues.run') as run_mock, contextlib.redirect_stdout(io.StringIO()) as out:
            issues.cmd_tasks_to_issues(Namespace(tasks_file=None, execute=True, json=False))
        run_mock.assert_not_called()
        self.assertIn('Nothing to file', out.getvalue())

    def test_tasks_file_not_found_is_a_clean_error(self):
        self.write_contract(acceptance='["one"]')
        with self.assertRaises(SystemExit) as ctx:
            issues.cmd_tasks_to_issues(Namespace(tasks_file=str(self.task / 'nope.md'), execute=False, json=False))
        self.assertIn('not found', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
