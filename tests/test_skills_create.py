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
                always_consider=False, skill_cmd='create')
    base.update(overrides)
    return Namespace(**base)


class SkillCreateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skills-test-')
        self.addCleanup(self.temp.cleanup)
        self.skill_root = Path(self.temp.name)
        (self.skill_root / 'registry.json').write_text(json.dumps({"version": 1, "skills": {}}))
        patcher = patch('skills.skill_root', return_value=self.skill_root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def registry(self):
        return json.loads((self.skill_root / 'registry.json').read_text())

    def test_create_writes_folder_and_registers_skill(self):
        skills.cmd_skill_create(make_args())
        folder = self.skill_root / 'custom-skill'
        self.assertTrue((folder / 'skill.json').exists())
        self.assertIn('Do the thing carefully.', (folder / 'prompt.md').read_text())
        self.assertIn('A custom skill.', (folder / 'README.md').read_text())
        reg = self.registry()['skills']['custom-skill']
        self.assertTrue(reg['enabled'])
        self.assertEqual(reg['task_types'], ['feature'])
        self.assertEqual(reg['triggers'], ['foo', 'bar'])
        self.assertEqual(reg['stages'], ['one', 'two'])
        self.assertNotIn('always_consider', reg)

    def test_create_sets_always_consider_when_requested(self):
        skills.cmd_skill_create(make_args(always_consider=True))
        self.assertTrue(self.registry()['skills']['custom-skill']['always_consider'])

    def test_create_rejects_duplicate_name(self):
        skills.cmd_skill_create(make_args())
        with self.assertRaises(SystemExit):
            skills.cmd_skill_create(make_args())

    def test_create_rejects_invalid_name(self):
        with self.assertRaises(SystemExit):
            skills.cmd_skill_create(make_args(name='Not_Valid'))

    def test_create_rejects_unknown_task_type(self):
        with self.assertRaises(SystemExit):
            skills.cmd_skill_create(make_args(task_types='not-a-real-type'))

    def test_create_rejects_unknown_cost(self):
        with self.assertRaises(SystemExit):
            skills.cmd_skill_create(make_args(cost='extreme'))

    def test_created_skill_is_selectable_by_trigger(self):
        skills.cmd_skill_create(make_args(triggers='thingamajig'))
        selected = skills.select_skills(self.skill_root, 'fix the thingamajig now', 'standard')
        self.assertIn('custom-skill', selected)


if __name__ == '__main__':
    unittest.main()
