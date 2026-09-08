"""Security/injection category: pins the existing mitigations for the two
user-controlled-string-into-subprocess-argv surfaces in ai_stack (--base
passed to git, skill names used as path components). This project never uses
shell=True/os.system anywhere (subprocess always gets an argv list), so
classic shell metacharacter injection isn't reachable; the real residual
risks are git *option* injection via a ref-shaped flag value, and path
traversal via a name later joined onto a directory. Run only this file with:
    python3 -m unittest discover -s tests -p 'test_security_injection.py'
"""
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import skills  # noqa: E402


class GitBaseArgumentInjectionTests(unittest.TestCase):
    """resolve_base() (core.py) verifies `base` with `git rev-parse --verify`
    before anything consumes it - this is what stops a ref-shaped value like
    `--output=<path>` from being read as a git option by the later `git diff`/
    `git diff --numstat` calls. An unverifiable base is now rejected outright
    rather than quietly replaced by HEAD: the old fallback let the command
    succeed over an empty scope, which silently downgraded risk and gates."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sec-test-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.repo = self.home / 'repo'
        self.repo.mkdir()
        base_env = {k: v for k, v in os.environ.items() if k not in ('AI_GATE', 'AI_TASK_DIR')}
        self.env = dict(base_env, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / 'config'), AI_TASK_ID='sec')
        for args in [('init', '-q'), ('config', 'user.name', 'Test'), ('config', 'user.email', 'test@example.com')]:
            subprocess.check_output(['git', *args], cwd=self.repo, env=self.env, text=True)
        (self.repo / 'app.txt').write_text('initial\n')
        subprocess.check_output(['git', 'add', '.'], cwd=self.repo, env=self.env, text=True)
        subprocess.check_output(['git', 'commit', '-qm', 'initial'], cwd=self.repo, env=self.env, text=True)

    def ai(self, *args):
        result = subprocess.run([sys.executable, str(ROOT / 'ai_stack/cli.py'), *args], cwd=self.repo, env=self.env, text=True, capture_output=True)
        return result

    def test_output_flag_shaped_base_does_not_write_a_file(self):
        sentinel = self.home / 'pwned-sentinel.txt'
        self.assertFalse(sentinel.exists())
        result = self.ai('impact', f'--base=--output={sentinel}')
        self.assertFalse(sentinel.exists(), 'a ref-shaped --base value must never reach git as a literal option')
        self.assertNotEqual(result.returncode, 0, 'an unverifiable base must fail closed')
        self.assertIn('does not exist in this repository', result.stdout + result.stderr)

    def test_upload_pack_flag_shaped_base_is_rejected_as_a_ref(self):
        sentinel = self.home / 'pwned-sentinel-2.txt'
        result = self.ai('review', f'--base=--upload-pack=touch {sentinel}', '--no-launch')
        self.assertFalse(sentinel.exists())
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        # The rejection must name refs that actually exist, so the operator can recover.
        self.assertIn('Refs detected here:', result.stdout + result.stderr)


class SkillNamePathTraversalTests(unittest.TestCase):
    """cmd_skill_create() joins `name` onto skill_root() unescaped, so the
    name validation regex is the only thing standing between a crafted name
    and writing outside skills/ - this pins that it actually rejects."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skills-sec-test-')
        self.addCleanup(self.temp.cleanup)
        self.skill_root = Path(self.temp.name) / 'skills'
        self.skill_root.mkdir()
        (self.skill_root / 'registry.json').write_text('{"version": 1, "skills": {}}')
        self.outside = Path(self.temp.name) / 'outside-marker.json'
        patcher = patch('skills.skill_root', return_value=self.skill_root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_args(self, name):
        return Namespace(name=name, category='quality', cost='medium', priority=50,
                          task_types='', triggers='', stages='', prompt='x', description='',
                          always_consider=False, skill_cmd='create')

    def test_parent_traversal_name_is_rejected(self):
        with self.assertRaises(SystemExit):
            skills.cmd_skill_create(self.make_args(f'../{self.outside.stem}'))
        self.assertFalse(self.outside.exists())

    def test_absolute_path_name_is_rejected(self):
        with self.assertRaises(SystemExit):
            skills.cmd_skill_create(self.make_args(str(self.outside)))
        self.assertFalse(self.outside.exists())

    def test_embedded_slash_name_is_rejected(self):
        with self.assertRaises(SystemExit):
            skills.cmd_skill_create(self.make_args('nested/evil'))
        self.assertFalse((self.skill_root / 'nested').exists())


if __name__ == '__main__':
    unittest.main()
