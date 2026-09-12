"""`ai checklist`: a deterministic, per-criterion completeness checklist from the PR
contract -- universal questions a human checks off, never a sufficiency judgement.
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
import checklist  # noqa: E402


def contract(acceptance='[]', must_not_change='[]'):
    return textwrap.dedent(f'''\
        objective: "x"
        acceptance: {acceptance}
        must_not_change: {must_not_change}
        risk_notes: []
        ''')


class GenerateChecklistTests(unittest.TestCase):
    def test_one_item_per_criterion_with_five_universal_checks_each(self):
        report = checklist.generate_checklist(contract(acceptance='["one", "two"]'))
        self.assertEqual(report['acceptance_count'], 2)
        self.assertEqual(len(report['items']), 2)
        self.assertEqual(report['items'][0]['index'], 1)
        self.assertEqual(report['items'][0]['criterion'], 'one')
        self.assertEqual(len(report['items'][0]['checks']), 5)
        self.assertTrue(all(c['checked'] is False for c in report['items'][0]['checks']))

    def test_empty_acceptance_yields_no_items(self):
        report = checklist.generate_checklist(contract(acceptance='[]'))
        self.assertEqual(report['items'], [])

    def test_must_not_change_count_is_reported(self):
        report = checklist.generate_checklist(contract(acceptance='["x"]', must_not_change='["a", "b"]'))
        self.assertEqual(report['must_not_change_count'], 2)


class RenderMarkdownTests(unittest.TestCase):
    def test_renders_a_heading_and_checkbox_per_item(self):
        report = checklist.generate_checklist(contract(acceptance='["Ships the button"]'))
        text = checklist.render_markdown(report)
        self.assertIn('## Criterion 1: Ships the button', text)
        self.assertIn('- [ ] ', text)

    def test_no_criteria_says_so(self):
        report = checklist.generate_checklist(contract(acceptance='[]'))
        self.assertIn('no acceptance criteria', checklist.render_markdown(report))


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='checklist-cmd-')
        self.addCleanup(self.temp.cleanup)
        self.task = Path(self.temp.name)
        (self.task / 'contracts').mkdir(parents=True)
        (self.task / 'state').mkdir()
        for target in ('checklist.git_root', 'checklist.repo_state'):
            patcher = patch(target, return_value=self.task)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('checklist.task_state', return_value=self.task)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_contract(self, **kwargs):
        (self.task / 'contracts' / 'current-pr.yml').write_text(contract(**kwargs))

    def run_checklist(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            checklist.cmd_checklist(Namespace(json=False))
        return out.getvalue()

    def test_missing_contract_is_needs_human(self):
        with self.assertRaises(SystemExit) as ctx:
            self.run_checklist()
        self.assertIn('no PR contract', str(ctx.exception))

    def test_writes_json_report_and_markdown_file(self):
        self.write_contract(acceptance='["Ships the button"]')
        self.run_checklist()
        report = json.loads((self.task / 'state' / 'checklist.json').read_text())
        self.assertEqual(report['acceptance_count'], 1)
        markdown = (self.task / 'state' / 'checklist.md').read_text()
        self.assertIn('Ships the button', markdown)

    def test_exits_zero_even_with_criteria(self):
        self.write_contract(acceptance='["a", "b"]')
        self.run_checklist()  # must not raise

    def test_the_contract_is_never_rewritten(self):
        self.write_contract(acceptance='["a"]')
        before = (self.task / 'contracts' / 'current-pr.yml').read_text()
        self.run_checklist()
        self.assertEqual((self.task / 'contracts' / 'current-pr.yml').read_text(), before)

    def test_json_output_matches_the_saved_report(self):
        self.write_contract(acceptance='["a"]')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            checklist.cmd_checklist(Namespace(json=True))
        payload = json.loads(out.getvalue())
        self.assertEqual(payload, json.loads((self.task / 'state' / 'checklist.json').read_text()))


if __name__ == '__main__':
    unittest.main()
