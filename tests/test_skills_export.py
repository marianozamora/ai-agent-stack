"""`ai skill export` / `scripts/export_skills.py`: writing the skill library as standalone,
native-format `SKILL.md` files a project can use without this stack's CLI, routing, or gates.
"""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import skills  # noqa: E402


def write_skill(root: Path, name: str, meta: dict | None = None, extra_files: dict | None = None):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'prompt.md').write_text(f'{name} body.\n')
    (folder / 'README.md').write_text(f'# {name}\n')
    (folder / 'skill.json').write_text(json.dumps({"name": name, **(meta or {})}))
    for filename, content in (extra_files or {}).items():
        (folder / filename).write_text(content)
    registry = json.loads((root / 'registry.json').read_text()) if (root / 'registry.json').exists() \
        else {"version": 1, "skills": {}}
    registry['skills'][name] = meta or {"enabled": True, "category": "quality", "cost": "low",
                                        "priority": 50, "task_types": ["feature"], "triggers": []}
    (root / 'registry.json').write_text(json.dumps(registry))


class ExportUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skills-export-')
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.stack_root = base / 'stack-skills'
        self.out_dir = base / 'out'
        self.stack_root.mkdir()
        (self.stack_root / 'registry.json').write_text(json.dumps({"version": 1, "skills": {}}))
        patcher = patch('skills.skill_root', return_value=self.stack_root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_exports_every_enabled_selectable_skill_by_default(self):
        write_skill(self.stack_root, 'alpha')
        write_skill(self.stack_root, 'beta', {"enabled": False, "category": "quality", "cost": "low",
                                              "priority": 50, "task_types": [], "triggers": []})
        write_skill(self.stack_root, 'hidden', {"enabled": True, "selectable": False, "category": "quality",
                                                 "cost": "low", "priority": 50, "task_types": [], "triggers": []})
        exported = skills.export_skills(None, self.out_dir)
        self.assertEqual(set(exported), {'alpha'})
        self.assertTrue((self.out_dir / 'alpha' / 'SKILL.md').is_file())
        self.assertFalse((self.out_dir / 'beta').exists())
        self.assertFalse((self.out_dir / 'hidden').exists())

    def test_exported_file_has_valid_frontmatter_and_the_prompt_body(self):
        write_skill(self.stack_root, 'alpha', {"enabled": True, "category": "quality", "cost": "low",
                                               "priority": 50, "task_types": ["feature"],
                                               "triggers": ["do the thing"]})
        skills.export_skills(None, self.out_dir)
        text = (self.out_dir / 'alpha' / 'SKILL.md').read_text()
        self.assertTrue(text.startswith('---\nname: alpha\n'))
        head, _, body = text.partition('---\n')[2].partition('---\n')
        self.assertIn('description:', head)
        self.assertIn('do the thing', head)
        self.assertIn('alpha body.', body)

    def test_companion_files_travel_with_the_skill(self):
        write_skill(self.stack_root, 'has-script', extra_files={'helper.sh': '#!/bin/sh\necho hi\n'})
        skills.export_skills(None, self.out_dir)
        self.assertTrue((self.out_dir / 'has-script' / 'helper.sh').is_file())
        self.assertEqual((self.out_dir / 'has-script' / 'helper.sh').read_text(), '#!/bin/sh\necho hi\n')

    def test_internal_files_are_not_leaked_into_the_export(self):
        write_skill(self.stack_root, 'alpha')
        skills.export_skills(None, self.out_dir)
        exported_files = {p.name for p in (self.out_dir / 'alpha').iterdir()}
        self.assertEqual(exported_files, {'SKILL.md'})

    def test_explicit_names_export_only_those(self):
        write_skill(self.stack_root, 'alpha')
        write_skill(self.stack_root, 'beta')
        exported = skills.export_skills(None, self.out_dir, ['beta'])
        self.assertEqual(exported, ['beta'])
        self.assertFalse((self.out_dir / 'alpha').exists())

    def test_unknown_explicit_name_raises(self):
        write_skill(self.stack_root, 'alpha')
        with self.assertRaises(SystemExit):
            skills.export_skills(None, self.out_dir, ['not-a-real-skill'])

    def test_non_selectable_skill_is_rejected_even_when_named_explicitly(self):
        write_skill(self.stack_root, 'hidden', {"enabled": True, "selectable": False, "category": "quality",
                                                 "cost": "low", "priority": 50, "task_types": [], "triggers": []})
        with self.assertRaises(SystemExit):
            skills.export_skills(None, self.out_dir, ['hidden'])

    def test_skill_without_a_prompt_file_is_skipped_not_crashed(self):
        folder = self.stack_root / 'no-prompt'
        folder.mkdir()
        registry = json.loads((self.stack_root / 'registry.json').read_text())
        registry['skills']['no-prompt'] = {"enabled": True, "category": "quality", "cost": "low",
                                           "priority": 50, "task_types": [], "triggers": []}
        (self.stack_root / 'registry.json').write_text(json.dumps(registry))
        exported = skills.export_skills(None, self.out_dir)
        self.assertNotIn('no-prompt', exported)

    def test_export_writes_only_under_out_dir(self):
        write_skill(self.stack_root, 'alpha')
        before = (self.stack_root / 'registry.json').read_text()
        skills.export_skills(None, self.out_dir)
        self.assertEqual((self.stack_root / 'registry.json').read_text(), before)
        self.assertFalse((self.stack_root / 'alpha' / 'SKILL.md').exists())


class BundledLibraryExportTests(unittest.TestCase):
    """Against the real, shipped skills/ directory -- every bundled skill must export cleanly."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skills-export-real-')
        self.addCleanup(self.temp.cleanup)
        self.out_dir = Path(self.temp.name) / 'out'

    def test_every_bundled_selectable_skill_exports_with_valid_frontmatter(self):
        registry = skills.skill_registry().get('skills', {})
        selectable = [n for n, m in registry.items() if m.get('selectable', True)]
        exported = skills.export_skills(None, self.out_dir)
        self.assertEqual(set(exported), set(selectable))
        for name in exported:
            text = (self.out_dir / name / 'SKILL.md').read_text()
            self.assertTrue(text.startswith(f'---\nname: {name}\n'))
            self.assertIn('description: "', text)

    def test_companion_scripts_survive_export(self):
        skills.export_skills(None, self.out_dir)
        self.assertTrue((self.out_dir / 'wizard' / 'template.sh').is_file())
        self.assertTrue((self.out_dir / 'git-guardrails-claude-code' / 'block-dangerous-git.sh').is_file())

    def test_tool_routing_is_excluded_by_default(self):
        skills.export_skills(None, self.out_dir)
        self.assertFalse((self.out_dir / 'tool-routing').exists())


class StandaloneScriptTests(unittest.TestCase):
    """The zero-install scripts/export_skills.py entry point."""

    def test_runs_without_the_package_installed_and_needs_no_repository(self):
        with tempfile.TemporaryDirectory(prefix='skills-export-script-') as tmp:
            out_dir = Path(tmp) / 'out'
            result = subprocess.run(
                [sys.executable, str(ROOT / 'scripts/export_skills.py'), '--out', str(out_dir),
                 '--skill', 'tdd'],
                cwd=tmp, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((out_dir / 'tdd' / 'SKILL.md').is_file())


class CliExportTests(unittest.TestCase):
    def test_cli_export_works_outside_a_git_repository(self):
        import cli
        import os
        with tempfile.TemporaryDirectory(prefix='skills-export-cli-') as tmp:
            non_repo = Path(tmp) / 'not-a-repo'; non_repo.mkdir()
            out_dir = Path(tmp) / 'out'
            previous = os.getcwd()
            os.chdir(non_repo)
            self.addCleanup(os.chdir, previous)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                 patch.object(sys, 'argv', ['ai', 'skill', 'export', '--out', str(out_dir), '--skill', 'tdd']):
                try:
                    cli.main()
                except SystemExit as exc:
                    self.assertIn(exc.code, (0, None))
            self.assertTrue((out_dir / 'tdd' / 'SKILL.md').is_file())


if __name__ == '__main__':
    unittest.main()
