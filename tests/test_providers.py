"""Direct unit coverage for ai_stack/providers.py: the Builder/Reviewer provider
objects, the configured()/builder()/reviewer() selection logic, and the
`ai providers` command surface.

`run_codex_json` and its "bogus executable raises" guard are already covered by
tests/test_validators.py, which imports it via validators.py's re-export; that
identity is pinned here too so the re-export can never silently drift.
Run only this file with:
    python3 -m unittest discover -s tests -p 'test_providers.py' -v
"""
import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import core  # noqa: E402
import providers  # noqa: E402
import validators  # noqa: E402


def _pass(**overrides):
    value = {'status': 'PASS', 'evidence': ['checked X'], 'findings': [], 'summary_markdown': ''}
    value.update(overrides)
    return value


def _checker(value):
    """A stand-in for validators.check_verdict that just requires PASS + evidence."""
    if value.get('status') != 'PASS' or not value.get('evidence'):
        raise ValueError('bad verdict')
    return value


class ReExportTests(unittest.TestCase):
    def test_validators_run_codex_json_is_the_providers_implementation(self):
        # validators.py re-exports this rather than duplicating it; pin the identity
        # so a future edit to either file can't quietly fork the two implementations.
        self.assertIs(validators.run_codex_json, providers.run_codex_json)


class ClaudeBuilderTests(unittest.TestCase):
    def test_available_reflects_which(self):
        builder = providers.ClaudeBuilder()
        with mock.patch.object(providers.shutil, 'which', return_value='/usr/bin/claude'):
            self.assertTrue(builder.available())
        with mock.patch.object(providers.shutil, 'which', return_value=None):
            self.assertFalse(builder.available())

    def test_launch_raises_with_the_original_message_when_missing(self):
        builder = providers.ClaudeBuilder()
        with mock.patch.object(providers.shutil, 'which', return_value=None):
            with self.assertRaises(SystemExit) as caught:
                builder.launch('prompt text', Path('/repo'), {})
        # Exact text of the pre-refactor message: `ai plan` still works when the
        # builder is missing, and existing guidance/docs quote this string.
        self.assertEqual(str(caught.exception), 'Claude CLI missing. Use ai plan to only prepare.')

    def test_launch_execs_the_resolved_path_with_the_prompt_and_env(self):
        builder = providers.ClaudeBuilder()
        env = {'AI_TASK_ID': 'x'}
        with mock.patch.object(providers.shutil, 'which', return_value='/usr/local/bin/claude'), \
                mock.patch.object(providers.sys.stdin, 'isatty', return_value=True), \
                mock.patch.object(providers.os, 'execvpe') as execvpe:
            builder.launch('the prompt', Path('/repo'), env)
        execvpe.assert_called_once_with('/usr/local/bin/claude', ['/usr/local/bin/claude', 'the prompt'], env)

    def test_launch_refuses_without_a_tty_instead_of_hanging(self):
        # Regression: execvpe replaces this process with an interactive Claude session.
        # Without a real terminal to talk to (a script, CI, a pipe), it used to hang
        # silently forever instead of failing - this must refuse before ever exec'ing.
        builder = providers.ClaudeBuilder()
        with mock.patch.object(providers.shutil, 'which', return_value='/usr/local/bin/claude'), \
                mock.patch.object(providers.sys.stdin, 'isatty', return_value=False), \
                mock.patch.object(providers.os, 'execvpe') as execvpe:
            with self.assertRaises(SystemExit) as caught:
                builder.launch('the prompt', Path('/repo'), {})
        execvpe.assert_not_called()
        self.assertIn('interactive terminal', str(caught.exception))
        self.assertIn('--plan-only', str(caught.exception))


class CodexReviewerTests(unittest.TestCase):
    def test_available_reflects_which(self):
        reviewer = providers.CodexReviewer()
        with mock.patch.object(providers.shutil, 'which', return_value='/usr/bin/codex'):
            self.assertTrue(reviewer.available())
        with mock.patch.object(providers.shutil, 'which', return_value=None):
            self.assertFalse(reviewer.available())

    def test_verdict_raises_when_codex_is_missing(self):
        reviewer = providers.CodexReviewer()
        with mock.patch.object(providers.shutil, 'which', return_value=None):
            with self.assertRaises(ValueError) as caught:
                reviewer.verdict(Path('/repo'), Path('/repo/review'), 'cleanup', 'prompt',
                                 validators.SCHEMA, _checker, None)
        self.assertIn('Codex CLI missing', str(caught.exception))

    def test_verdict_delegates_to_run_codex_json_with_the_resolved_path(self):
        reviewer = providers.CodexReviewer()
        with mock.patch.object(providers.shutil, 'which', return_value='/usr/bin/codex'), \
                mock.patch.object(providers, 'run_codex_json', return_value=_pass()) as run:
            result = reviewer.verdict(Path('/repo'), Path('/repo/review'), 'cleanup', 'prompt',
                                      validators.SCHEMA, _checker, 300)
        run.assert_called_once_with('/usr/bin/codex', Path('/repo'), Path('/repo/review'),
                                    'cleanup', 'prompt', validators.SCHEMA, _checker, 300)
        self.assertEqual(result, _pass())

    def test_probe_binary_matches_executable(self):
        self.assertEqual(providers.CodexReviewer.probe_binary, providers.CodexReviewer.executable)


class CommandReviewerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cmdreviewer-')
        self.addCleanup(self.tmp.cleanup)
        self.review_dir = Path(self.tmp.name)

    def _script(self, body):
        path = self.review_dir / 'script.sh'
        path.write_text('#!/bin/bash\n' + body)
        path.chmod(0o755)
        return [str(path)]

    def test_available_false_for_empty_or_missing_command(self):
        self.assertFalse(providers.CommandReviewer([]).available())
        self.assertFalse(providers.CommandReviewer(['/no/such/binary-xyz']).available())

    def test_probe_binary_is_the_commands_first_token(self):
        reviewer = providers.CommandReviewer(['/bin/echo', 'hi'])
        self.assertEqual(reviewer.probe_binary, '/bin/echo')
        self.assertIsNone(providers.CommandReviewer([]).probe_binary)

    def test_successful_verdict_reads_the_last_stdout_line(self):
        command = self._script('echo "noise"\necho \'{"status":"PASS","evidence":["ok"],'
                               '"findings":[],"summary_markdown":""}\'\n')
        reviewer = providers.CommandReviewer(command)
        result = reviewer.verdict(Path('.'), self.review_dir, 'cleanup', 'the prompt',
                                  validators.SCHEMA, _checker, 10)
        self.assertEqual(result['status'], 'PASS')

    def test_prompt_is_passed_on_stdin(self):
        command = self._script('python3 -c "import sys,json; text=sys.stdin.read(); '
                               'print(json.dumps({\'status\':\'PASS\',\'evidence\':[text.strip()],'
                               '\'findings\':[],\'summary_markdown\':\'\'}))"')
        reviewer = providers.CommandReviewer(command)
        result = reviewer.verdict(Path('.'), self.review_dir, 'cleanup', 'unique-prompt-marker',
                                  validators.SCHEMA, _checker, 10)
        self.assertEqual(result['evidence'], ['unique-prompt-marker'])

    def test_nonzero_exit_raises_with_diagnostics_path(self):
        command = self._script('echo "boom" 1>&2\nexit 3\n')
        reviewer = providers.CommandReviewer(command)
        with self.assertRaises(ValueError) as caught:
            reviewer.verdict(Path('.'), self.review_dir, 'cleanup', 'prompt',
                             validators.SCHEMA, _checker, 10)
        message = str(caught.exception)
        self.assertIn('exited 3', message)
        diagnostics = self.review_dir / 'cleanup-events.jsonl'
        self.assertTrue(diagnostics.is_file())
        self.assertIn('boom', diagnostics.read_text())

    def test_no_output_raises(self):
        command = self._script('true\n')
        reviewer = providers.CommandReviewer(command)
        with self.assertRaises(ValueError) as caught:
            reviewer.verdict(Path('.'), self.review_dir, 'cleanup', 'prompt',
                             validators.SCHEMA, _checker, 10)
        self.assertIn('no output', str(caught.exception))

    def test_malformed_json_raises_with_diagnostics(self):
        command = self._script('echo "not json"\n')
        reviewer = providers.CommandReviewer(command)
        with self.assertRaises(ValueError) as caught:
            reviewer.verdict(Path('.'), self.review_dir, 'cleanup', 'prompt',
                             validators.SCHEMA, _checker, 10)
        self.assertIn('diagnostics', str(caught.exception))

    def test_checker_rejection_raises_with_diagnostics(self):
        command = self._script('echo \'{"status":"FAIL","evidence":[],"findings":[],'
                               '"summary_markdown":""}\'\n')
        reviewer = providers.CommandReviewer(command)
        with self.assertRaises(ValueError) as caught:
            reviewer.verdict(Path('.'), self.review_dir, 'cleanup', 'prompt',
                             validators.SCHEMA, _checker, 10)
        self.assertIn('bad verdict', str(caught.exception))
        self.assertIn('diagnostics', str(caught.exception))

    def test_timeout_raises(self):
        command = self._script('sleep 5\n')
        reviewer = providers.CommandReviewer(command)
        with self.assertRaises(ValueError) as caught:
            reviewer.verdict(Path('.'), self.review_dir, 'cleanup', 'prompt',
                             validators.SCHEMA, _checker, 0.2)
        self.assertIn('timed out', str(caught.exception))

    def test_missing_binary_raises_before_spawning(self):
        reviewer = providers.CommandReviewer(['/no/such/binary-xyz'])
        with self.assertRaises(ValueError) as caught:
            reviewer.verdict(Path('.'), self.review_dir, 'cleanup', 'prompt',
                             validators.SCHEMA, _checker, 10)
        self.assertIn('not executable', str(caught.exception))


class RunCodexJsonTests(unittest.TestCase):
    """Real-subprocess coverage of run_codex_json's own protocol (schema file, JSON
    events, usage extraction, diagnostics). The "bogus executable" OSError guard is
    covered in tests/test_validators.py via the re-export; this covers behavior."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='run-codex-json-')
        self.addCleanup(self.tmp.cleanup)
        self.review_dir = Path(self.tmp.name)

    def _fake_codex(self, body):
        path = self.review_dir / 'fake-codex'
        path.write_text('#!' + sys.executable + '\n' + body)
        path.chmod(0o755)
        return str(path)

    def test_success_extracts_verdict_and_summed_usage(self):
        codex = self._fake_codex('''import json, sys
args = sys.argv
assert args[1] == "exec" and args[args.index("-s") + 1] == "read-only"
assert "--output-schema" in args and "--ephemeral" in args
out = args[args.index("--output-last-message") + 1]
open(out, "w").write(json.dumps({"status": "PASS", "evidence": ["ok"], "findings": [], "summary_markdown": ""}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 1}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 2, "output_tokens": 1}}))
''')
        result = providers.run_codex_json(codex, Path('.'), self.review_dir, 'cleanup', 'the prompt',
                                          validators.SCHEMA, _checker)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['usage'], {'input_tokens': 5, 'output_tokens': 2})
        self.assertTrue((self.review_dir / 'cleanup-events.jsonl').is_file())

    def test_nonzero_exit_raises(self):
        codex = self._fake_codex('import sys; sys.exit(2)\n')
        with self.assertRaises(ValueError) as caught:
            providers.run_codex_json(codex, Path('.'), self.review_dir, 'cleanup', 'p',
                                     validators.SCHEMA, _checker)
        self.assertIn('exited 2', str(caught.exception))

    def test_missing_output_file_raises(self):
        codex = self._fake_codex('pass\n')  # never writes --output-last-message
        with self.assertRaises(ValueError) as caught:
            providers.run_codex_json(codex, Path('.'), self.review_dir, 'cleanup', 'p',
                                     validators.SCHEMA, _checker)
        self.assertIn('Missing or oversized', str(caught.exception))

    def test_oversized_output_raises(self):
        codex = self._fake_codex('''import sys
out = sys.argv[sys.argv.index("--output-last-message") + 1]
open(out, "w").write("x" * 70000)
''')
        with self.assertRaises(ValueError) as caught:
            providers.run_codex_json(codex, Path('.'), self.review_dir, 'cleanup', 'p',
                                     validators.SCHEMA, _checker)
        self.assertIn('Missing or oversized', str(caught.exception))

    def test_checker_rejection_propagates(self):
        codex = self._fake_codex('''import json, sys
out = sys.argv[sys.argv.index("--output-last-message") + 1]
open(out, "w").write(json.dumps({"status": "FAIL", "evidence": [], "findings": [], "summary_markdown": ""}))
''')
        with self.assertRaises(ValueError) as caught:
            providers.run_codex_json(codex, Path('.'), self.review_dir, 'cleanup', 'p',
                                     validators.SCHEMA, _checker)
        self.assertIn('bad verdict', str(caught.exception))

    def test_timeout_raises_with_diagnostics(self):
        codex = self._fake_codex('import time; time.sleep(5)\n')
        with self.assertRaises(ValueError) as caught:
            providers.run_codex_json(codex, Path('.'), self.review_dir, 'cleanup', 'p',
                                     validators.SCHEMA, _checker, timeout=0.2)
        self.assertIn('timed out', str(caught.exception))

    def test_malformed_usage_events_are_ignored_not_fatal(self):
        codex = self._fake_codex('''import json, sys
out = sys.argv[sys.argv.index("--output-last-message") + 1]
open(out, "w").write(json.dumps({"status": "PASS", "evidence": ["ok"], "findings": [], "summary_markdown": ""}))
print("not json at all")
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": -5}}))
''')
        result = providers.run_codex_json(codex, Path('.'), self.review_dir, 'cleanup', 'p',
                                          validators.SCHEMA, _checker)
        self.assertNotIn('usage', result)  # negative tokens rejected, no crash either


class ConfiguredTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='providers-configured-')
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        env_cleanup = mock.patch.dict(os.environ, {}, clear=False)
        env_cleanup.start()
        self.addCleanup(env_cleanup.stop)
        os.environ.pop('AI_BUILDER', None)
        os.environ.pop('AI_REVIEWER', None)

    def test_defaults_with_no_repo_json(self):
        settings = providers.configured(self.state)
        self.assertEqual(settings, {'builder': 'claude', 'reviewer': 'codex', 'reviewer_command': None})

    def test_stored_settings_override_defaults(self):
        (self.state / 'repo.json').write_text(json.dumps({'providers': {'builder': 'claude', 'reviewer': 'command',
                                                          'reviewer_command': ['/bin/echo']}}))
        settings = providers.configured(self.state)
        self.assertEqual(settings['reviewer'], 'command')
        self.assertEqual(settings['reviewer_command'], ['/bin/echo'])

    def test_env_overrides_stored(self):
        (self.state / 'repo.json').write_text(json.dumps({'providers': {'reviewer': 'command'}}))
        with mock.patch.dict(os.environ, {'AI_REVIEWER': 'codex', 'AI_BUILDER': 'claude'}):
            settings = providers.configured(self.state)
        self.assertEqual(settings['reviewer'], 'codex')

    def test_malformed_providers_value_is_tolerated(self):
        (self.state / 'repo.json').write_text(json.dumps({'providers': 'not-a-dict'}))
        settings = providers.configured(self.state)
        self.assertEqual(settings['builder'], 'claude')
        self.assertEqual(settings['reviewer'], 'codex')


class FactoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='providers-factory-')
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)

    def test_builder_default_is_claude(self):
        self.assertIsInstance(providers.builder(self.state), providers.ClaudeBuilder)

    def test_builder_unknown_choice_raises_systemexit(self):
        (self.state / 'repo.json').write_text(json.dumps({'providers': {'builder': 'gpt5'}}))
        with self.assertRaises(SystemExit) as caught:
            providers.builder(self.state)
        self.assertIn('Unknown builder', str(caught.exception))

    def test_reviewer_default_is_codex(self):
        self.assertIsInstance(providers.reviewer(self.state), providers.CodexReviewer)

    def test_reviewer_command_needs_a_configured_command(self):
        (self.state / 'repo.json').write_text(json.dumps({'providers': {'reviewer': 'command'}}))
        with self.assertRaises(ValueError) as caught:
            providers.reviewer(self.state)
        self.assertIn('no reviewer_command is configured', str(caught.exception))

    def test_reviewer_command_with_configured_command(self):
        (self.state / 'repo.json').write_text(json.dumps(
            {'providers': {'reviewer': 'command', 'reviewer_command': ['/bin/echo', 'hi']}}))
        instance = providers.reviewer(self.state)
        self.assertIsInstance(instance, providers.CommandReviewer)
        self.assertEqual(instance.command, ['/bin/echo', 'hi'])

    def test_reviewer_unknown_choice_raises_valueerror(self):
        (self.state / 'repo.json').write_text(json.dumps({'providers': {'reviewer': 'claude-review'}}))
        with self.assertRaises(ValueError) as caught:
            providers.reviewer(self.state)
        self.assertIn('Unknown reviewer', str(caught.exception))


class CmdProvidersTests(unittest.TestCase):
    """cmd_providers(args) via a real git repo + redirected external state,
    matching the pattern in tests/test_repo.py::RepoSandboxTests."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='providers-cmd-')
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        self.repo = home / 'repo'
        self.repo.mkdir()
        self.cfg = home / 'config' / 'ai-agent-stack'

        env = {k: v for k, v in os.environ.items() if k not in ('AI_GATE', 'AI_TASK_DIR', 'AI_BUILDER', 'AI_REVIEWER')}
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'), AI_TASK_ID='providers-task')

        for args in (('init', '-q'), ('config', 'user.name', 'T'), ('config', 'user.email', 't@e.com')):
            subprocess.run(['git', *args], cwd=self.repo, check=True, capture_output=True)
        (self.repo / 'main.py').write_text('print("hi")\n')
        subprocess.run(['git', 'add', '.'], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-qm', 'init'], cwd=self.repo, check=True, capture_output=True)

        envp = mock.patch.dict(os.environ, env, clear=True)
        envp.start()
        self.addCleanup(envp.stop)
        cfgp = mock.patch.object(core, 'CONFIG_ROOT', self.cfg)
        cfgp.start()
        self.addCleanup(cfgp.stop)

        old_cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, old_cwd)

    def state(self):
        return core.repo_state(core.git_root())

    def _run(self, args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            providers.cmd_providers(args)
        return buf.getvalue()

    def _ns(self, providers_cmd, **fields):
        defaults = dict(builder=None, reviewer=None, reviewer_command=[], json=False)
        return argparse.Namespace(providers_cmd=providers_cmd, **{**defaults, **fields})

    def test_show_prints_defaults(self):
        out = self._run(self._ns(None))
        self.assertIn('Builder:  claude', out)
        self.assertIn('Reviewer: codex', out)

    def test_show_json_matches_configured(self):
        out = self._run(self._ns('show', json=True))
        payload = json.loads(out)
        self.assertEqual(payload, providers.configured(self.state()))

    def test_set_builder_persists_and_merges_with_existing_settings(self):
        self._run(self._ns('set', reviewer='command', reviewer_command=['--', '/bin/echo']))
        self._run(self._ns('set', builder='claude'))
        meta = core.load_json(self.state() / 'repo.json', {})
        self.assertEqual(meta['providers']['builder'], 'claude')
        # The earlier `set --reviewer command` call must survive an unrelated later set.
        self.assertEqual(meta['providers']['reviewer'], 'command')
        self.assertEqual(meta['providers']['reviewer_command'], ['/bin/echo'])

    def test_set_unknown_builder_raises_systemexit(self):
        # Direct call bypasses argparse's own `choices=` guard, exercising cmd_providers'
        # own validation (needed for any caller that builds a Namespace by hand).
        with self.assertRaises(SystemExit) as caught:
            self._run(self._ns('set', builder='gpt5'))
        self.assertIn('Unknown builder', str(caught.exception))

    def test_set_unknown_reviewer_raises_systemexit(self):
        with self.assertRaises(SystemExit) as caught:
            self._run(self._ns('set', reviewer='claude-review'))
        self.assertIn('Unknown reviewer', str(caught.exception))

    def test_set_reviewer_command_without_executable_raises(self):
        with self.assertRaises(SystemExit) as caught:
            self._run(self._ns('set', reviewer='command', reviewer_command=['--']))
        self.assertIn('requires an executable', str(caught.exception))

    def test_doctor_reports_codex_read_only_guarantee(self):
        with mock.patch.object(providers.shutil, 'which', return_value='/usr/bin/x'):
            out = self._run(self._ns('doctor'))
        self.assertIn('guaranteed by the provider', out)

    def test_doctor_flags_command_reviewer_as_not_read_only(self):
        self._run(self._ns('set', reviewer='command', reviewer_command=['--', '/bin/echo']))
        with mock.patch.object(providers.shutil, 'which', return_value='/usr/bin/x'):
            out = self._run(self._ns('doctor'))
        self.assertIn('NOT guaranteed', out)

    def test_doctor_reports_missing_binaries(self):
        with mock.patch.object(providers.shutil, 'which', return_value=None):
            out = self._run(self._ns('doctor'))
        self.assertIn('builder claude: MISSING', out)
        self.assertIn('reviewer codex: MISSING', out)

    def test_doctor_exits_nonzero_when_reviewer_cannot_be_constructed(self):
        self._run(self._ns('set', reviewer='command'))  # no reviewer_command configured
        with self.assertRaises(SystemExit) as caught:
            self._run(self._ns('doctor'))
        self.assertEqual(caught.exception.code, 1)


if __name__ == '__main__':
    unittest.main()
