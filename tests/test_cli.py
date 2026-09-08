import argparse
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
# ai_stack/ modules use bare same-directory imports; put it on sys.path first.
sys.path.insert(0, str(ROOT / 'ai_stack'))
import cli  # noqa: E402
import core  # noqa: E402

VERSION = (ROOT / 'VERSION').read_text().strip()


class ParserWiringTests(unittest.TestCase):
    def setUp(self):
        self.p = cli.parser()

    def test_parser_returns_argument_parser(self):
        self.assertIsInstance(self.p, argparse.ArgumentParser)

    def test_representative_subcommands_bind_a_callable_func(self):
        cases = [
            ['init'],
            ['plan', 'some task', '--profile', 'strict'],
            ['run', 'x'],
            ['ready'],
            ['gate', 'checks', '--', 'true'],
            ['pipeline', '--dry-run'],
            ['impact', '--base', 'main'],
            ['review', '--no-launch'],
            ['doctor'],
            ['status'],
            ['metrics', '--json'],
            ['skill', 'list'],
            ['rules', 'add', 'r'],
            ['path'],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                ns = self.p.parse_args(argv)
                self.assertTrue(hasattr(ns, 'func'))
                self.assertTrue(callable(ns.func))

    def test_bad_gate_choice_errors(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.p.parse_args(['gate', 'not-a-real-gate'])

    def test_version_action_prints_version_file_contents(self):
        out = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(out):
            self.p.parse_args(['--version'])
        self.assertEqual(out.getvalue().strip(), VERSION)

    def test_required_sub_subcommands_error_when_missing(self):
        for argv in (['validators'], ['ticket']):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                    self.p.parse_args(argv)

    def test_no_subcommand_parses_but_binds_no_func(self):
        ns = self.p.parse_args([])
        self.assertIsNone(ns.cmd)
        self.assertFalse(hasattr(ns, 'func'))


class MainNoCommandTests(unittest.TestCase):
    def test_main_with_no_command_prints_help_and_returns(self):
        out = io.StringIO()
        with patch.object(sys, 'argv', ['ai']), contextlib.redirect_stdout(out):
            result = cli.main()
        self.assertIsNone(result)
        self.assertIn('usage', out.getvalue())


class CmdPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cli-test-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.repo = self.home / 'repo'
        self.repo.mkdir()
        env = {k: v for k, v in os.environ.items() if k not in ('AI_GATE', 'AI_TASK_DIR', 'AI_TASK_ID')}
        for args in (('init', '-q'), ('config', 'user.name', 'Test'), ('config', 'user.email', 't@e.com')):
            subprocess.check_call(['git', *args], cwd=self.repo, env=env)
        # git_root()/task_state() read the process cwd; move into the sandbox and restore after.
        old = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, old)
        # In-process repo_state()/task_state() write under core.CONFIG_ROOT (fixed at import).
        self.env_patch = patch.dict(os.environ, {'HOME': str(self.home),
                                                 'XDG_CONFIG_HOME': str(self.home / 'config')},
                                    clear=False)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        for var in ('AI_GATE', 'AI_TASK_DIR', 'AI_TASK_ID'):
            os.environ.pop(var, None)
        self.cfg_patch = patch.object(core, 'CONFIG_ROOT', self.home / 'config' / 'ai-agent-stack')
        self.cfg_patch.start()
        self.addCleanup(self.cfg_patch.stop)

    def test_cmd_path_prints_an_existing_task_state_dir(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.cmd_path(argparse.Namespace())
        printed = Path(out.getvalue().strip())
        self.assertTrue(printed.is_dir())


if __name__ == '__main__':
    unittest.main()
