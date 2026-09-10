"""Skills resolve in a cascade: this repository's own skills shadow the bundled ones.

Before this, skill_root() was the single stack-wide directory, so a repository could not
add a skill of its own without editing the framework -- and a team with one repo-specific
convention had to either fork the stack or push that convention on every other repo.
"""
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import skills  # noqa: E402


def make_args(**overrides):
    base = dict(name='custom-skill', category='quality', cost='medium', priority=50,
                task_types='feature', triggers='foo,bar', stages='one,two',
                prompt='Do the thing carefully.', description='A custom skill.',
                always_consider=False, skill_cmd='create', repo=False)
    base.update(overrides)
    return Namespace(**base)


def write_skill(root: Path, name: str, prompt: str, meta: dict | None = None):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'prompt.md').write_text(prompt)
    (folder / 'README.md').write_text(f'# {name}\n')
    registry = json.loads((root / 'registry.json').read_text()) if (root / 'registry.json').exists() \
        else {"version": 1, "skills": {}}
    registry['skills'][name] = meta or {"enabled": True, "category": "quality", "cost": "low",
                                        "priority": 50, "task_types": ["feature"], "triggers": []}
    (root / 'registry.json').write_text(json.dumps(registry))


class CascadeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skills-scope-')
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

    def test_repo_skills_are_listed_alongside_bundled_ones(self):
        write_skill(self.stack_root, 'tdd', 'bundled tdd body')
        write_skill(self.repo_root(), 'house-style', 'repo-only body')
        registry = skills.skill_registry(self.state)['skills']
        self.assertEqual(registry['tdd']['origin'], 'stack')
        self.assertEqual(registry['house-style']['origin'], 'repo')

    def test_repo_skill_shadows_a_bundled_skill_of_the_same_name(self):
        write_skill(self.stack_root, 'tdd', 'bundled tdd body')
        write_skill(self.repo_root(), 'tdd', 'repo tdd body')
        self.assertEqual(skills.skill_registry(self.state)['skills']['tdd']['origin'], 'repo')
        self.assertIn('repo tdd body', skills.load_skill_context(self.state, ['tdd'], 10000))

    def test_shadowing_replaces_the_definition_rather_than_merging_fields(self):
        write_skill(self.stack_root, 'tdd', 'bundled', meta={"enabled": True, "category": "impl",
                                                             "cost": "low", "stages": ["red", "green"]})
        write_skill(self.repo_root(), 'tdd', 'repo', meta={"enabled": True, "category": "impl",
                                                           "cost": "high"})
        entry = skills.skill_registry(self.state)['skills']['tdd']
        self.assertEqual(entry['cost'], 'high')
        self.assertNotIn('stages', entry)

    def test_no_state_yields_bundled_skills_only(self):
        write_skill(self.stack_root, 'tdd', 'bundled tdd body')
        write_skill(self.repo_root(), 'house-style', 'repo-only body')
        self.assertNotIn('house-style', skills.skill_registry(None)['skills'])
        self.assertEqual(skills.load_skill_context(None, ['house-style'], 10000), '(none)')

    def test_repo_skill_participates_in_selection(self):
        write_skill(self.repo_root(), 'house-style', 'repo-only body',
                    meta={"enabled": True, "category": "quality", "cost": "low", "priority": 90,
                          "task_types": ["feature"], "triggers": ["thingamajig"]})
        selected = skills.select_skills(self.state, 'fix the thingamajig now', 'standard')
        self.assertIn('house-style', selected)

    def test_disable_override_applies_to_a_repo_skill(self):
        write_skill(self.repo_root(), 'house-style', 'repo-only body')
        (self.state / 'skill-overrides.json').write_text(json.dumps({"house-style": False}))
        self.assertFalse(skills.enabled_skills(self.state)['house-style']['enabled'])


class RepoScopedCreateTests(CascadeTests):
    def setUp(self):
        super().setUp()
        for target, value in (('skills.git_root', Path('/nowhere')), ('skills.repo_state', self.state)):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_create_repo_writes_into_repo_state_not_the_stack(self):
        skills.cmd_skill_create(make_args(repo=True))
        self.assertTrue((self.repo_root() / 'custom-skill' / 'prompt.md').exists())
        self.assertFalse((self.stack_root / 'custom-skill').exists())
        self.assertEqual(json.loads((self.stack_root / 'registry.json').read_text())['skills'], {})
        self.assertIn('custom-skill',
                      json.loads((self.repo_root() / 'registry.json').read_text())['skills'])

    def test_create_repo_may_deliberately_shadow_a_bundled_name(self):
        write_skill(self.stack_root, 'tdd', 'bundled tdd body')
        skills.cmd_skill_create(make_args(name='tdd', repo=True, prompt='repo tdd body'))
        self.assertIn('repo tdd body', skills.load_skill_context(self.state, ['tdd'], 10000))

    def test_create_repo_still_rejects_a_duplicate_repo_name(self):
        skills.cmd_skill_create(make_args(repo=True))
        with self.assertRaises(SystemExit):
            skills.cmd_skill_create(make_args(repo=True))

    def test_each_scope_owns_its_own_namespace(self):
        """A bundled skill is still worth creating for other repos when one repo shadows it.

        The collision check is per-registry, so the same name can exist in both scopes;
        which one wins here is then the cascade's business, not the create command's.
        """
        skills.cmd_skill_create(make_args(repo=True, prompt='repo body'))
        skills.cmd_skill_create(make_args(repo=False, prompt='bundled body'))
        self.assertIn('custom-skill', json.loads((self.stack_root / 'registry.json').read_text())['skills'])
        self.assertIn('repo body', skills.load_skill_context(self.state, ['custom-skill'], 10000))

    def test_create_preserves_registry_fields_it_does_not_own(self):
        (self.stack_root / 'registry.json').write_text(
            json.dumps({"version": 1, "policy": "metadata-only routing", "skills": {}}))
        skills.cmd_skill_create(make_args())
        written = json.loads((self.stack_root / 'registry.json').read_text())
        self.assertEqual(written['policy'], 'metadata-only routing')
        self.assertNotIn('origin', written['skills']['custom-skill'])


if __name__ == '__main__':
    unittest.main()
