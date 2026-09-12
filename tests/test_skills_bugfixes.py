"""Regression tests for five confirmed defects in the skills engine (docs/spec-skills-testing.md Task 1).

B1: a non-boolean skill-overrides.json entry (e.g. the JSON string "false") was interpreted
    as truthy by bool(), silently enabling a skill a human meant to disable.
B2: a corrupt repo-scoped registry.json degraded silently to "no repo skills", identical to
    a repo that simply never defined any -- the corruption was invisible.
B3: an unvalidated skill name in a repo registry (e.g. "../../evil") was selectable and
    reached resolve_skill_file()'s root/name/filename join unchecked.
B4: `ai skill create` (stack-scoped) wrote into STACK_ROOT/skills, which in an installed
    release is a disposable release directory the next upgrade replaces wholesale.
B5: an explicit --skill list longer than the profile's cap was silently truncated, unlike
    an explicitly disabled skill, which is reported.
"""
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
import skills  # noqa: E402


def write_skill(root: Path, name: str, meta: dict | None = None):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'prompt.md').write_text(f'{name} body')
    (folder / 'README.md').write_text(f'# {name}\n')
    registry = json.loads((root / 'registry.json').read_text()) if (root / 'registry.json').exists() \
        else {"version": 1, "skills": {}}
    registry['skills'][name] = meta or {"enabled": True, "category": "quality", "cost": "low",
                                        "priority": 50, "task_types": ["feature"], "triggers": []}
    (root / 'registry.json').write_text(json.dumps(registry))


class BugfixTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skills-bugfix-')
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.stack_root = base / 'stack-skills'
        self.state = base / 'repo-state'
        self.stack_root.mkdir()
        (self.state / 'skills').mkdir(parents=True)
        (self.stack_root / 'registry.json').write_text(json.dumps({"version": 1, "skills": {}}))
        patcher = patch('skills.skill_root', return_value=self.stack_root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def repo_root(self):
        return skills.repo_skill_root(self.state)


class B1NonBooleanOverrideTests(BugfixTestCase):
    def test_string_false_does_not_get_coerced_to_true_by_bool(self):
        # The bug: bool("false") is True in Python, so a hand-written override meant to
        # disable the skill was silently interpreted as enabling it. A non-bool override
        # must be ignored and reported, not coerced -- proven here on a skill whose
        # registry default is False, where the old code's bool("false")==True would flip
        # the effective value away from that default.
        write_skill(self.stack_root, 'tdd', {"enabled": False, "category": "implementation",
                                              "cost": "low", "priority": 50, "task_types": ["feature"], "triggers": []})
        (self.state / 'skill-overrides.json').write_text(json.dumps({"tdd": "false"}))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            enabled = skills.enabled_skills(self.state)
        self.assertFalse(enabled['tdd']['enabled'], 'a non-bool override must fall back to the registry default (False), not bool("false")==True')
        self.assertIn('tdd', err.getvalue())

    def test_zero_and_null_are_ignored_the_same_way(self):
        write_skill(self.stack_root, 'tdd')
        for bad in (0, None, 'true', [], {}):
            (self.state / 'skill-overrides.json').write_text(json.dumps({"tdd": bad}))
            with contextlib.redirect_stderr(io.StringIO()):
                enabled = skills.enabled_skills(self.state)
            self.assertTrue(enabled['tdd']['enabled'], f'non-bool override {bad!r} must fall back to the registry default')

    def test_real_booleans_are_still_honored(self):
        write_skill(self.stack_root, 'tdd')
        (self.state / 'skill-overrides.json').write_text(json.dumps({"tdd": False}))
        with contextlib.redirect_stderr(io.StringIO()):
            enabled = skills.enabled_skills(self.state)
        self.assertFalse(enabled['tdd']['enabled'])
        (self.state / 'skill-overrides.json').write_text(json.dumps({"tdd": True}))
        with contextlib.redirect_stderr(io.StringIO()):
            enabled = skills.enabled_skills(self.state)
        self.assertTrue(enabled['tdd']['enabled'])

    def test_override_for_an_unknown_skill_is_ignored_without_crashing(self):
        write_skill(self.stack_root, 'tdd')
        (self.state / 'skill-overrides.json').write_text(json.dumps({"nonexistent": True}))
        with contextlib.redirect_stderr(io.StringIO()):
            enabled = skills.enabled_skills(self.state)
        self.assertIn('tdd', enabled)
        self.assertNotIn('nonexistent', enabled)


class B2CorruptRepoRegistryTests(BugfixTestCase):
    def test_invalid_json_is_reported_and_bundled_skills_survive(self):
        write_skill(self.stack_root, 'tdd')
        (self.repo_root() / 'registry.json').write_text('{not json')
        with contextlib.redirect_stderr(io.StringIO()) as err:
            reg = skills.skill_registry(self.state)['skills']
        self.assertIn('tdd', reg)
        self.assertIn('Invalid repo skill registry', err.getvalue())

    def test_skills_value_not_a_dict_is_reported(self):
        write_skill(self.stack_root, 'tdd')
        (self.repo_root() / 'registry.json').write_text(json.dumps({"version": 1, "skills": []}))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            reg = skills.skill_registry(self.state)['skills']
        self.assertIn('tdd', reg)
        self.assertIn('Invalid repo skill registry', err.getvalue())

    def test_top_level_not_an_object_is_reported(self):
        write_skill(self.stack_root, 'tdd')
        (self.repo_root() / 'registry.json').write_text(json.dumps([]))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            reg = skills.skill_registry(self.state)['skills']
        self.assertIn('tdd', reg)
        self.assertIn('Invalid repo skill registry', err.getvalue())

    def test_a_corrupt_bundled_registry_still_fails_closed_to_no_skills(self):
        (self.stack_root / 'registry.json').write_text('{not json')
        with contextlib.redirect_stderr(io.StringIO()) as err:
            reg = skills.skill_registry(self.state)['skills']
        self.assertEqual(reg, {})
        self.assertIn('Invalid stack skill registry', err.getvalue())


class B3PathTraversalTests(BugfixTestCase):
    def test_traversal_name_is_dropped_from_the_registry(self):
        write_skill(self.stack_root, 'tdd')
        (self.repo_root() / 'registry.json').write_text(json.dumps(
            {"version": 1, "skills": {"../../evil": {"category": "x", "task_types": ["feature"], "priority": 999}}}))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            reg = skills.skill_registry(self.state)['skills']
        self.assertNotIn('../../evil', reg)
        self.assertIn('tdd', reg)
        self.assertIn('invalid repo skill entry', err.getvalue().lower())

    def test_traversal_name_is_never_selected(self):
        write_skill(self.stack_root, 'tdd')
        (self.repo_root() / 'registry.json').write_text(json.dumps(
            {"version": 1, "skills": {"../../evil": {"category": "x", "task_types": ["feature"], "priority": 999}}}))
        with contextlib.redirect_stderr(io.StringIO()):
            selected = skills.select_skills(self.state, 'add feature', 'standard')
        self.assertNotIn('../../evil', selected)

    def test_uppercase_name_is_also_rejected(self):
        write_skill(self.stack_root, 'tdd')
        (self.repo_root() / 'registry.json').write_text(json.dumps(
            {"version": 1, "skills": {"Evil": {"category": "x", "task_types": ["feature"]}}}))
        with contextlib.redirect_stderr(io.StringIO()):
            reg = skills.skill_registry(self.state)['skills']
        self.assertNotIn('Evil', reg)

    def test_resolve_skill_file_refuses_a_traversal_name_directly(self):
        (self.stack_root / '..' ).mkdir(exist_ok=True)  # no-op guard; real check is the regex
        self.assertIsNone(skills.resolve_skill_file(self.state, '../../evil', 'prompt.md'))
        self.assertIsNone(skills.resolve_skill_file(self.state, 'a/b', 'prompt.md'))


class B4StackScopedCreateInReleaseTests(BugfixTestCase):
    def test_create_without_repo_is_refused_inside_an_installed_release(self):
        from argparse import Namespace
        release_root = Path(self.temp.name) / 'ai-agent-stack-releases' / 'release-abc123' / 'skills'
        release_root.mkdir(parents=True)
        (release_root / 'registry.json').write_text(json.dumps({"version": 1, "skills": {}}))
        with patch('skills.skill_root', return_value=release_root):
            args = Namespace(name='global-x', category='quality', cost='medium', priority=50,
                              task_types='feature', triggers='', stages='', prompt='body',
                              description='', always_consider=False, skill_cmd='create', repo=False)
            with patch('skills.require_human'):
                with self.assertRaises(SystemExit) as ctx:
                    skills.cmd_skill_create(args)
        self.assertIn('installed release', str(ctx.exception))
        self.assertFalse((release_root / 'global-x').exists())
        registry = json.loads((release_root / 'registry.json').read_text())
        self.assertNotIn('global-x', registry.get('skills', {}))

    def test_create_with_repo_still_works_inside_an_installed_release(self):
        from argparse import Namespace
        release_root = Path(self.temp.name) / 'ai-agent-stack-releases' / 'release-abc123' / 'skills'
        release_root.mkdir(parents=True)
        (release_root / 'registry.json').write_text(json.dumps({"version": 1, "skills": {}}))
        with patch('skills.skill_root', return_value=release_root), \
             patch('skills.git_root', return_value=Path(self.temp.name)), \
             patch('skills.repo_state', return_value=self.state):
            args = Namespace(name='repo-x', category='quality', cost='medium', priority=50,
                              task_types='feature', triggers='', stages='', prompt='body',
                              description='', always_consider=False, skill_cmd='create', repo=True)
            with patch('skills.require_human'):
                skills.cmd_skill_create(args)
        self.assertTrue((self.repo_root() / 'repo-x').exists())

    def test_create_without_repo_still_works_from_a_source_checkout(self):
        from argparse import Namespace
        args = Namespace(name='global-y', category='quality', cost='medium', priority=50,
                          task_types='feature', triggers='', stages='', prompt='body',
                          description='', always_consider=False, skill_cmd='create', repo=False)
        with patch('skills.require_human'):
            skills.cmd_skill_create(args)
        self.assertTrue((self.stack_root / 'global-y').exists())


class B5ExplicitSkillCapTests(BugfixTestCase):
    def setUp(self):
        super().setUp()
        write_skill(self.stack_root, 'research', {"enabled": True, "category": "evidence", "cost": "low",
                                                   "priority": 50, "task_types": [], "triggers": []})
        write_skill(self.stack_root, 'tdd', {"enabled": True, "category": "implementation", "cost": "low",
                                              "priority": 50, "task_types": [], "triggers": []})

    def test_overflow_is_reported_and_named(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            selected = skills.select_skills(self.state, 'x', 'fast', explicit=['research', 'tdd'])
        self.assertEqual(selected, ['research'])
        self.assertIn('tdd', err.getvalue())
        self.assertIn('fast', err.getvalue())

    def test_within_cap_is_silent(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            selected = skills.select_skills(self.state, 'x', 'standard', explicit=['research', 'tdd'])
        self.assertEqual(selected, ['research', 'tdd'])
        self.assertEqual(err.getvalue(), '')

    def test_duplicates_are_deduplicated_preserving_order_without_a_cap_warning(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            selected = skills.select_skills(self.state, 'x', 'standard', explicit=['tdd', 'tdd'])
        self.assertEqual(selected, ['tdd'])
        self.assertEqual(err.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
