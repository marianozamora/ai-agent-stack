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
# ai_stack/ modules use bare same-directory imports; putting ai_stack/ on
# sys.path makes `import clarify` work, transitively pulling core/workflow.
sys.path.insert(0, str(ROOT / 'ai_stack'))
import clarify  # noqa: E402


def contract(acceptance='[]', objective='"Add rate limiting to the login endpoint"',
             must_not_change='[]'):
    return textwrap.dedent(f'''\
        objective: {objective}
        acceptance: {acceptance}
        must_not_change: {must_not_change}
        risk_notes: []
        design:
          enabled: false
        ''')


class ListFieldTests(unittest.TestCase):
    def test_reads_flow_style_written_by_ensure_contract(self):
        text = contract(acceptance='["429 after 6 attempts", "counter resets after 60s"]')
        self.assertEqual(clarify._list_field(text, 'acceptance'),
                         ['429 after 6 attempts', 'counter resets after 60s'])

    def test_reads_block_style_written_by_a_human(self):
        text = textwrap.dedent('''\
            objective: "x"
            acceptance:
              - 429 after 6 attempts
              - "counter resets after 60s"
            must_not_change: []
            ''')
        self.assertEqual(clarify._list_field(text, 'acceptance'),
                         ['429 after 6 attempts', 'counter resets after 60s'])

    def test_block_list_stops_at_the_next_field(self):
        text = textwrap.dedent('''\
            acceptance:
              - only this one
            must_not_change:
              - the public API
            ''')
        self.assertEqual(clarify._list_field(text, 'acceptance'), ['only this one'])
        self.assertEqual(clarify._list_field(text, 'must_not_change'), ['the public API'])

    def test_missing_and_malformed_fields_are_empty_not_fatal(self):
        self.assertEqual(clarify._list_field('objective: "x"\n', 'acceptance'), [])
        self.assertEqual(clarify._list_field('acceptance: [not, json]\n', 'acceptance'), [])


class AnalyzeContractTests(unittest.TestCase):
    def test_concrete_criteria_are_ready(self):
        report = clarify.analyze_contract(
            contract(acceptance='["Returns 429 after the 6th attempt in 60s"]',
                     must_not_change='["the /login response shape"]'), {})
        self.assertEqual(report['status'], 'READY')
        self.assertEqual(report['blockers'], [])

    def test_empty_acceptance_blocks(self):
        report = clarify.analyze_contract(contract(), {})
        self.assertEqual(report['status'], 'NEEDS_HUMAN')
        self.assertTrue(any('no acceptance criteria' in b for b in report['blockers']))

    def test_placeholder_objective_blocks(self):
        report = clarify.analyze_contract(
            contract(objective='"Implement the current working task."',
                     acceptance='["Returns 429 after the 6th attempt in 60s"]'), {})
        self.assertTrue(any('no specific objective' in b for b in report['blockers']))

    def test_unobservable_criterion_blocks(self):
        report = clarify.analyze_contract(
            contract(acceptance='["Rate limiting works correctly"]'), {})
        self.assertEqual(report['status'], 'NEEDS_HUMAN')
        self.assertTrue(any("'works correctly'" in b for b in report['blockers']))

    def test_too_short_criterion_blocks(self):
        report = clarify.analyze_contract(contract(acceptance='["done"]'), {})
        self.assertTrue(any('too short' in b for b in report['blockers']))

    def test_soft_term_asks_instead_of_blocking(self):
        """'improve' in a criterion that names its observable outcome is not a defect.

        A blocking lint here would stop real work on a real wording, so the soft tier
        only raises a question -- the asymmetry is the whole point of two tiers.
        """
        report = clarify.analyze_contract(
            contract(acceptance='["Improve the parse error to include the file path and line"]',
                     must_not_change='["the CLI exit codes"]'), {})
        self.assertEqual(report['status'], 'READY')
        self.assertTrue(any("'improve'" in q for q in report['questions']))

    def test_spec_kit_clarification_marker_blocks(self):
        report = clarify.analyze_contract(
            contract(acceptance='["Returns 429 after the 6th attempt in 60s"]'),
            {'clarifications_needed': ['which limit applies to authenticated users?']})
        self.assertEqual(report['status'], 'NEEDS_HUMAN')
        self.assertTrue(any('which limit applies' in b for b in report['blockers']))

    def test_empty_must_not_change_is_a_question_not_a_blocker(self):
        report = clarify.analyze_contract(
            contract(acceptance='["Returns 429 after the 6th attempt in 60s"]'), {})
        self.assertEqual(report['status'], 'READY')
        self.assertTrue(any('must_not_change is empty' in q for q in report['questions']))

    def test_mentioned_blockers_are_advisory_questions_only(self):
        report = clarify.analyze_contract(
            contract(acceptance='["Returns 429 after the 6th attempt in 60s"]',
                     must_not_change='["the /login response shape"]'),
            {'blockers_mentioned': ['PLAT-88 redis rollout']})
        self.assertEqual(report['status'], 'READY')
        self.assertTrue(any('PLAT-88' in q for q in report['questions']))


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='clarify-cmd-')
        self.addCleanup(self.temp.cleanup)
        self.task = Path(self.temp.name)
        (self.task / 'contracts').mkdir(parents=True)
        (self.task / 'state').mkdir()
        for target in ('clarify.git_root', 'clarify.repo_state'):
            patcher = patch(target, return_value=self.task)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('clarify.task_state', return_value=self.task)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, **kwargs):
        (self.task / 'contracts' / 'current-pr.yml').write_text(contract(**kwargs))

    def report(self):
        return json.loads((self.task / 'state' / 'clarify.json').read_text())

    def run_clarify(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            clarify.cmd_clarify(Namespace(json=False))
        return out.getvalue()

    def test_missing_contract_is_needs_human(self):
        with self.assertRaises(SystemExit) as ctx:
            self.run_clarify()
        self.assertIn('no PR contract', str(ctx.exception))

    def test_ready_contract_writes_a_report_and_exits_zero(self):
        self.write(acceptance='["Returns 429 after the 6th attempt in 60s"]',
                   must_not_change='["the /login response shape"]')
        self.run_clarify()
        self.assertEqual(self.report()['status'], 'READY')

    def test_blocking_contract_exits_needs_human_but_still_writes_the_report(self):
        self.write(acceptance='["Rate limiting works correctly"]')
        with self.assertRaises(SystemExit) as ctx:
            self.run_clarify()
        self.assertIn('NEEDS_HUMAN', str(ctx.exception))
        self.assertEqual(self.report()['status'], 'NEEDS_HUMAN')

    def test_the_contract_is_never_rewritten(self):
        self.write(acceptance='["Rate limiting works correctly"]')
        before = (self.task / 'contracts' / 'current-pr.yml').read_text()
        with self.assertRaises(SystemExit):
            self.run_clarify()
        self.assertEqual((self.task / 'contracts' / 'current-pr.yml').read_text(), before)

    def test_the_report_lands_outside_the_fingerprinted_contracts_directory(self):
        """Producing a reading of the contract must not invalidate in-flight evidence."""
        self.write(acceptance='["Returns 429 after the 6th attempt in 60s"]')
        self.run_clarify()
        self.assertEqual(list((self.task / 'contracts').iterdir()),
                         [self.task / 'contracts' / 'current-pr.yml'])
        self.assertTrue((self.task / 'state' / 'clarify.json').exists())

    def test_ticket_clarification_markers_reach_the_command(self):
        self.write(acceptance='["Returns 429 after the 6th attempt in 60s"]')
        (self.task / 'state' / 'ticket.json').write_text(
            json.dumps({'clarifications_needed': ['does this apply to SSO logins?']}))
        with self.assertRaises(SystemExit):
            self.run_clarify()
        self.assertTrue(any('SSO logins' in b for b in self.report()['blockers']))


if __name__ == '__main__':
    unittest.main()
