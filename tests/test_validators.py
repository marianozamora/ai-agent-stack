"""Direct unit coverage for ai_stack/validators.py: the strict verdict schema
checker, the evidence-integrity check, and a light guard on run_codex_json.
Run only this file with:
    python3 -m unittest discover -s tests -p 'test_validators.py' -v
"""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import validators  # noqa: E402
import workflow  # noqa: E402


def _pass(**overrides):
    """A minimal valid PASS verdict, with fields overridable per-case."""
    value = {'status': 'PASS', 'evidence': ['checked X'], 'findings': [], 'summary_markdown': ''}
    value.update(overrides)
    return value


class InstructionsTests(unittest.TestCase):
    def test_instructions_is_str_to_str_and_stays_in_sync_with_order(self):
        """INSTRUCTIONS is a non-empty str->str dict; every key is a workflow gate."""
        self.assertIsInstance(validators.INSTRUCTIONS, dict)
        self.assertTrue(validators.INSTRUCTIONS)
        for key, text in validators.INSTRUCTIONS.items():
            self.assertIsInstance(key, str)
            self.assertIsInstance(text, str)
            self.assertTrue(text.strip())
            self.assertIn(key, workflow.ORDER)


class CheckVerdictTests(unittest.TestCase):
    def test_valid_pass_returns_the_same_dict(self):
        value = _pass(evidence=['checked X'])
        self.assertIs(validators.check_verdict(value, 'review'), value)

    def test_missing_field_rejected(self):
        value = {'status': 'PASS', 'evidence': ['x'], 'findings': []}
        with self.assertRaises(ValueError):
            validators.check_verdict(value, 'review')

    def test_extra_field_rejected(self):
        with self.assertRaises(ValueError):
            validators.check_verdict(_pass(extra='nope'), 'review')

    def test_bad_status_string_rejected(self):
        with self.assertRaises(ValueError):
            validators.check_verdict(_pass(status='MAYBE'), 'review')

    def test_non_dict_rejected(self):
        with self.assertRaises(ValueError):
            validators.check_verdict(['not', 'a', 'dict'], 'review')

    def test_evidence_not_a_list_rejected(self):
        with self.assertRaises(ValueError):
            validators.check_verdict(_pass(evidence='checked X'), 'review')

    def test_evidence_with_non_str_item_rejected(self):
        with self.assertRaises(ValueError):
            validators.check_verdict(_pass(evidence=[1]), 'review')

    def test_evidence_with_blank_item_rejected(self):
        with self.assertRaises(ValueError):
            validators.check_verdict(_pass(evidence=['   ']), 'review')

    def test_findings_not_a_list_rejected(self):
        value = _pass(status='FAIL', evidence=['x'], findings='oops')
        with self.assertRaises(ValueError):
            validators.check_verdict(value, 'review')

    def test_findings_with_non_str_item_rejected(self):
        value = _pass(status='FAIL', evidence=['x'], findings=[2])
        with self.assertRaises(ValueError):
            validators.check_verdict(value, 'review')

    def test_findings_with_blank_item_rejected(self):
        value = _pass(status='FAIL', evidence=['x'], findings=['   '])
        with self.assertRaises(ValueError):
            validators.check_verdict(value, 'review')

    def test_summary_markdown_not_a_str_rejected(self):
        with self.assertRaises(ValueError):
            validators.check_verdict(_pass(summary_markdown=123), 'review')

    def test_pass_with_empty_evidence_rejected(self):
        with self.assertRaises(ValueError):
            validators.check_verdict(_pass(evidence=[]), 'review')

    def test_pass_with_unresolved_findings_rejected(self):
        with self.assertRaises(ValueError):
            validators.check_verdict(_pass(evidence=['x'], findings=['still broken']), 'review')

    def test_summary_validator_pass_requires_a_draft(self):
        value = _pass(evidence=['x'], summary_markdown='   ')
        with self.assertRaises(ValueError):
            validators.check_verdict(value, 'summary')

    def test_summary_validator_pass_with_a_draft_is_ok(self):
        value = _pass(evidence=['x'], summary_markdown='# Title\n\nchanges')
        self.assertIs(validators.check_verdict(value, 'summary'), value)

    def test_fail_verdict_with_findings_and_no_evidence_is_allowed(self):
        value = _pass(status='FAIL', evidence=[], findings=['broken path in a.py:10'])
        self.assertIs(validators.check_verdict(value, 'review'), value)


class IntactRecordTests(unittest.TestCase):
    @staticmethod
    def _sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def test_non_dict_record_is_not_intact(self):
        self.assertFalse(validators.intact_record('not a dict', 'fp'))

    def test_fingerprint_mismatch_is_not_intact(self):
        self.assertFalse(validators.intact_record({'fingerprint': 'other'}, 'fp'))

    def test_missing_log_file_is_not_intact(self):
        record = {'fingerprint': 'fp', 'log': '/no/such/log/file', 'log_hash': 'abc'}
        self.assertFalse(validators.intact_record(record, 'fp'))

    def test_matching_log_hash_and_no_artifacts_is_intact(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'gate.log'
            log.write_text('validated fixture output\n')
            record = {'fingerprint': 'fp', 'log': str(log), 'log_hash': self._sha(log)}
            self.assertTrue(validators.intact_record(record, 'fp'))

    def test_tampered_log_file_is_not_intact(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'gate.log'
            log.write_text('original output\n')
            record = {'fingerprint': 'fp', 'log': str(log), 'log_hash': self._sha(log)}
            log.write_text('rewritten after hashing\n')
            self.assertFalse(validators.intact_record(record, 'fp'))

    def test_artifact_hash_mismatch_is_not_intact(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'gate.log'
            log.write_text('output\n')
            artifact = Path(d) / 'pr-summary.md'
            artifact.write_text('# summary\n')
            record = {
                'fingerprint': 'fp', 'log': str(log), 'log_hash': self._sha(log),
                'artifacts': [{'path': str(artifact), 'sha256': '0' * 64}],
            }
            self.assertFalse(validators.intact_record(record, 'fp'))

    def test_correct_artifact_hash_is_intact(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'gate.log'
            log.write_text('output\n')
            artifact = Path(d) / 'pr-summary.md'
            artifact.write_text('# summary\n')
            record = {
                'fingerprint': 'fp', 'log': str(log), 'log_hash': self._sha(log),
                'artifacts': [{'path': str(artifact), 'sha256': self._sha(artifact)}],
            }
            self.assertTrue(validators.intact_record(record, 'fp'))


class RunCodexJsonTests(unittest.TestCase):
    def test_bogus_executable_raises(self):
        """A missing codex binary surfaces as an OSError; codex is never really spawned here."""
        with tempfile.TemporaryDirectory() as d:
            review_dir = Path(d)
            with self.assertRaises((FileNotFoundError, OSError)):
                validators.run_codex_json(
                    str(review_dir / 'no-such-codex'), review_dir, review_dir,
                    'name', 'prompt', validators.SCHEMA,
                    lambda value: validators.check_verdict(value, 'review'))


if __name__ == '__main__':
    unittest.main()
