"""`ai rules import` reads a conventions document the repo already has.

spec-kit's constitution is a file in the checkout that every prompt inflates. The stack
cannot adopt that shape -- framework state stays out of the repository -- but it can read
a document the project already keeps (CONTRIBUTING.md, docs/conventions.md, or a
spec-kit `.specify/memory/constitution.md`) and turn its bullets into rules, which then
live in external state like every other rule. Reading the checkout is fine; writing to it
is what the zero-footprint rule forbids.
"""
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
import repo  # noqa: E402


CONSTITUTION = textwrap.dedent('''\
    # Project Constitution

    Some prose that is not a rule at all, just an explanation of the sections below.

    ## I. Library-First
    - Every feature starts as a standalone library with its own tests.
    - No feature may depend on the CLI layer.

    ## II. Testing
    1. Tests are written before the implementation.
    2. Integration tests cover every contract change.

    ```bash
    # not a rule: a code fence
    - pytest -q
    ```

    - Short
    ''')


class ExtractRulesTests(unittest.TestCase):
    def setUp(self):
        self.items = repo.extract_rules(CONSTITUTION)

    def test_bullets_and_numbered_items_are_both_read(self):
        self.assertIn('Every feature starts as a standalone library with its own tests.', self.items)
        self.assertIn('Tests are written before the implementation.', self.items)

    def test_headings_and_prose_are_not_rules(self):
        self.assertFalse(any('Project Constitution' in i for i in self.items))
        self.assertFalse(any('Some prose' in i for i in self.items))

    def test_code_fences_are_stripped(self):
        self.assertFalse(any('pytest' in i for i in self.items))

    def test_too_short_lines_are_dropped(self):
        self.assertNotIn('Short', self.items)

    def test_duplicates_collapse(self):
        self.assertEqual(repo.extract_rules('- the same line here\n- the same line here\n'),
                         ['the same line here'])


class RulesImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rules-import-')
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.state = base / 'state'
        self.state.mkdir()
        self.source = base / 'CONTRIBUTING.md'
        self.source.write_text(CONSTITUTION)
        patcher = patch('repo.require_human')
        patcher.start()
        self.addCleanup(patcher.stop)

    def args(self, **overrides):
        base = dict(rules_cmd='import', file=str(self.source), scope='**', confirm=False)
        base.update(overrides)
        return Namespace(**base)

    def stored(self):
        path = self.state / 'rules.json'
        return json.loads(path.read_text()) if path.exists() else []

    def test_without_confirm_nothing_is_written(self):
        with self.assertRaises(SystemExit) as ctx:
            repo.cmd_rules_import(self.args(), self.state, [])
        self.assertIn('--confirm', str(ctx.exception))
        self.assertEqual(self.stored(), [])

    def test_confirm_writes_rules_with_their_source(self):
        repo.cmd_rules_import(self.args(confirm=True), self.state, [])
        rules = self.stored()
        self.assertTrue(rules)
        self.assertTrue(all(r['source'] == 'imported' for r in rules))
        self.assertTrue(all(r['source_file'] == str(self.source) for r in rules))
        self.assertTrue(all(r['scope'] == '**' for r in rules))

    def test_scope_is_carried_through(self):
        repo.cmd_rules_import(self.args(confirm=True, scope='src/**'), self.state, [])
        self.assertTrue(all(r['scope'] == 'src/**' for r in self.stored()))

    def test_existing_rules_are_kept_and_not_re_imported(self):
        existing = [{'rule': 'No feature may depend on the CLI layer.', 'scope': '**',
                     'source': 'user', 'confidence': 1.0, 'created_at': 0}]
        repo.cmd_rules_import(self.args(confirm=True), self.state, list(existing))
        rules = self.stored()
        self.assertEqual(rules[0], existing[0])
        matches = [r for r in rules if r['rule'] == existing[0]['rule']]
        self.assertEqual(len(matches), 1)

    def test_nothing_new_is_not_an_error(self):
        repo.cmd_rules_import(self.args(confirm=True), self.state, [])
        already = self.stored()
        repo.cmd_rules_import(self.args(confirm=True), self.state, list(already))
        self.assertEqual(len(self.stored()), len(already))

    def test_missing_file_fails_loudly(self):
        with self.assertRaises(SystemExit):
            repo.cmd_rules_import(self.args(file=str(self.state / 'nope.md')), self.state, [])

    def test_import_never_writes_to_the_source_document(self):
        before = self.source.read_text()
        repo.cmd_rules_import(self.args(confirm=True), self.state, [])
        self.assertEqual(self.source.read_text(), before)

    def test_import_is_human_only(self):
        """It curates what every later prompt is told, so a gate must not run it."""
        with patch('repo.require_human', side_effect=SystemExit('human-only')):
            with self.assertRaises(SystemExit):
                repo.cmd_rules_import(self.args(confirm=True), self.state, [])


if __name__ == '__main__':
    unittest.main()
