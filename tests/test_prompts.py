"""Direct unit coverage for ai_stack/prompts.py: the pure prompt-slot,
variant, override, history and deterministic-assignment helpers, plus a light
in-process drive of the cmd_prompt read paths.
Run only this file with:
    python3 -m unittest discover -s tests -p 'test_prompts.py' -v
"""
import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import prompts  # noqa: E402
import core  # noqa: E402
import validators  # noqa: E402


def _expected_variant(cache_key, slot, variants):
    """The exact formula prompts.assign_prompt_variants uses for an experiment slot."""
    return variants[int(core.shasum(cache_key + slot), 16) % len(variants)]


class PureHelperTests(unittest.TestCase):
    def test_prompt_slot(self):
        self.assertEqual(prompts.prompt_slot('review'), 'validator.review')

    def test_variant_text_a_is_the_bundled_instruction(self):
        self.assertEqual(prompts.variant_text('review', 'validator.review', 'a'),
                         validators.INSTRUCTIONS['review'])

    def test_variant_text_unknown_variant_exits(self):
        # No templates/prompts/<slot>/ tree ships, so any non-'a' variant is unknown.
        with self.assertRaises(SystemExit):
            prompts.variant_text('review', 'validator.review', 'zz')

    def test_variant_entry_shape_and_sha(self):
        entry = prompts.variant_entry('review', 'validator.review', 'a')
        self.assertEqual(entry['variant'], 'a')
        self.assertEqual(entry['sha'], core.shasum(validators.INSTRUCTIONS['review'])[:12])
        self.assertEqual(len(entry['sha']), 12)
        int(entry['sha'], 16)  # 12 hex digits

    def test_available_variants_defaults_to_a(self):
        variants = prompts.available_variants('validator.does-not-exist')
        self.assertEqual(variants, ['a'])
        self.assertEqual(variants[0], 'a')


class OverridesAndHistoryTests(unittest.TestCase):
    def test_prompt_overrides_missing_then_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            self.assertEqual(prompts.prompt_overrides(state), {'version': 1, 'slots': {}})
            data = {'version': 1, 'slots': {'validator.review': {'variant': 'a', 'sha': 'abc123abc123'}}}
            (state / 'prompt-overrides.json').write_text(json.dumps(data))
            self.assertEqual(prompts.prompt_overrides(state), data)

    def test_prompt_history_append_load_and_skips_malformed(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            self.assertEqual(prompts.load_prompt_history(state), [])
            prompts.append_prompt_history(state, {'action': 'promote', 'slot': 'validator.review'})
            prompts.append_prompt_history(state, {'action': 'reset', 'slot': 'validator.review'})
            rows = prompts.load_prompt_history(state)
            self.assertEqual([r['action'] for r in rows], ['promote', 'reset'])
            # A corrupt line and a non-dict line are skipped, not fatal.
            with (state / 'prompt-history.jsonl').open('a') as f:
                f.write('not json at all\n')
                f.write('123\n')
            self.assertEqual(len(prompts.load_prompt_history(state)), 2)

    def test_prompt_experiment_missing_then_inner_dict(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            self.assertIsNone(prompts.prompt_experiment(state))
            active = {'slot': 'validator.review', 'variants': ['a', 'b']}
            (state / 'prompt-experiments.json').write_text(json.dumps({'version': 1, 'active': active}))
            self.assertEqual(prompts.prompt_experiment(state), active)


class AssignPromptVariantsTests(unittest.TestCase):
    def test_no_overrides_no_experiment_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(prompts.assign_prompt_variants(Path(d), 'cache-key'), {})

    def test_active_experiment_assignment_is_deterministic_and_matches_formula(self):
        slot, variants = 'validator.review', ['a', 'b']
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            (state / 'prompt-experiments.json').write_text(json.dumps(
                {'version': 1, 'active': {'slot': slot, 'variants': variants}}))

            entry_a = prompts.variant_entry('review', slot, 'a')
            checked_a = checked_b = 0
            for i in range(60):
                key = f'cache-key-{i}'
                expected = _expected_variant(key, slot, variants)
                if expected == 'a':
                    first = prompts.assign_prompt_variants(state, key)
                    second = prompts.assign_prompt_variants(state, key)
                    self.assertEqual(first, second)               # deterministic
                    self.assertEqual(first, {slot: entry_a})      # matches the hash formula
                    checked_a += 1
                else:
                    # variant 'b' has no bundled template; a real experiment only ever
                    # lists variants that available_variants() vetted, so this path exits.
                    with self.assertRaises(SystemExit):
                        prompts.assign_prompt_variants(state, key)
                    checked_b += 1
            self.assertGreater(checked_a, 0)
            self.assertGreater(checked_b, 0)

    def test_promoted_override_wins_and_experiment_does_not_re_add_the_slot(self):
        slot = 'validator.review'
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            (state / 'prompt-overrides.json').write_text(json.dumps(
                {'version': 1, 'slots': {slot: {'variant': 'a'}}}))
            (state / 'prompt-experiments.json').write_text(json.dumps(
                {'version': 1, 'active': {'slot': slot, 'variants': ['a', 'b']}}))
            result = prompts.assign_prompt_variants(state, 'any-cache-key')
            self.assertEqual(result, {slot: prompts.variant_entry('review', slot, 'a')})
            self.assertEqual(len(result), 1)


class CmdPromptReadPathTests(unittest.TestCase):
    """Drive the read-only cmd_prompt branches in-process. repo_state()'s config
    root and git_root() are redirected so nothing touches the real environment;
    promote/reset/rollback are left to tests/test_workflow.py (they gate on
    require_human and mutate state)."""

    @contextlib.contextmanager
    def _sandbox(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / 'repo'
            repo.mkdir()
            with patch.object(prompts, 'git_root', lambda: repo), \
                 patch.object(core, 'CONFIG_ROOT', Path(d) / 'config'):
                yield

    def _run(self, **ns):
        buf = io.StringIO()
        with self._sandbox(), contextlib.redirect_stdout(buf):
            prompts.cmd_prompt(argparse.Namespace(**ns))
        return buf.getvalue()

    def test_list_shows_every_slot(self):
        out = self._run(prompt_cmd='list')
        self.assertIn('validator.review', out)
        self.assertIn('variants=a', out)

    def test_show_prints_the_bundled_variant_text(self):
        out = self._run(prompt_cmd='show', name='review', variant='a')
        self.assertIn(validators.INSTRUCTIONS['review'].strip(), out)

    def test_experiment_status_without_an_experiment(self):
        out = self._run(prompt_cmd='experiment', experiment_cmd='status')
        self.assertIn('No active experiment', out)


if __name__ == '__main__':
    unittest.main()
