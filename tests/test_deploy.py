"""Direct, in-process unit coverage for ai_stack/deploy.py."""
import argparse
import contextlib
import hashlib
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
import deploy  # noqa: E402


def _git(repo, *args):
    subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True)


def _sandbox(tc):
    """Isolated git repo + redirected external state, mirroring tests/test_gates.py::_sandbox."""
    tmp = tempfile.TemporaryDirectory(prefix='deploy-test-')
    tc.addCleanup(tmp.cleanup)
    home = Path(tmp.name)
    repo = home / 'repo'
    repo.mkdir()
    cfg = home / 'config' / 'ai-agent-stack'

    env = {k: v for k, v in os.environ.items() if k not in ('AI_GATE', 'AI_TASK_DIR')}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'), AI_TASK_ID='deploy-task')

    for args in (('init', '-q'), ('config', 'user.name', 'T'), ('config', 'user.email', 't@e.com')):
        _git(repo, *args)
    (repo / 'app.txt').write_text('initial\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'init')

    envp = mock.patch.dict(os.environ, env, clear=True)
    envp.start()
    tc.addCleanup(envp.stop)
    cfgp = mock.patch.object(core, 'CONFIG_ROOT', cfg)
    cfgp.start()
    tc.addCleanup(cfgp.stop)

    old_cwd = os.getcwd()
    os.chdir(repo)
    tc.addCleanup(os.chdir, old_cwd)

    root = core.git_root()
    state = core.repo_state(root)
    return root, state


class _GitSandbox(unittest.TestCase):
    """A throwaway git repo with HOME/XDG redirected and AI_GATE/AI_TASK_DIR stripped."""
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='deploy-detect-test-')
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.repo = self.home / 'repo'
        self.repo.mkdir()
        base_env = {k: v for k, v in os.environ.items() if k not in ('AI_GATE', 'AI_TASK_DIR')}
        self.env = dict(base_env, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / 'config'))
        for args in (('init', '-q'), ('config', 'user.name', 'Test'),
                     ('config', 'user.email', 'test@example.com')):
            self.git(*args)

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.repo, env=self.env, text=True).strip()

    def write(self, rel, text=''):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path


class DetectDeployTests(_GitSandbox):
    def test_every_bucket_is_classified_from_tracked_files_only(self):
        self.write('Dockerfile', 'FROM scratch\n')
        self.write('sub/Dockerfile', 'FROM scratch\n')
        self.write('docker-compose.yml', 'services: {}\n')
        self.write('.github/workflows/ci.yml', 'name: ci\n')
        self.write('.github/workflows/deploy.yaml', 'name: deploy\n')
        self.write('Procfile', 'web: run\n')
        self.write('fly.toml', 'app = "x"\n')
        self.write('vercel.json', '{}\n')
        self.write('infra/main.tf', 'resource "null_resource" "x" {}\n')
        self.write('k8s/deploy.yaml', 'kind: Deployment\n')
        self.write('scripts/deploy.sh', '#!/bin/sh\necho go\n')
        self.write('deploy_prod.py', 'print("go")\n')
        self.write('Makefile', 'deploy:\n\techo shipping\n')
        self.write('README.md', '# App\n\n## Deploy\n\nrun the script\n')
        self.write('docs/guide.md', '# Guide\n\n### Deployment\n\nsteps\n')
        self.write('src/app.py', 'x = 1\n')  # noise, no bucket
        self.git('add', '.')
        self.write('untracked/Dockerfile', 'FROM scratch\n')  # never `git add`ed

        found = deploy.detect_deploy(self.repo)
        self.assertCountEqual(found['docker'], ['Dockerfile', 'sub/Dockerfile', 'docker-compose.yml'])
        self.assertEqual(found['ci_cd'], ['.github/workflows/ci.yml', '.github/workflows/deploy.yaml'])
        self.assertCountEqual(found['paas'], ['Procfile', 'fly.toml', 'vercel.json'])
        self.assertEqual(found['infra'], ['infra/main.tf', 'k8s/deploy.yaml'])
        self.assertCountEqual(found['scripts'], ['Makefile', 'deploy_prod.py', 'scripts/deploy.sh'])
        self.assertCountEqual(found['docs'], ['README.md', 'docs/guide.md'])
        for bucket in found.values():
            self.assertNotIn('untracked/Dockerfile', bucket)

    def test_makefile_without_a_deploy_target_is_not_a_deploy_script(self):
        self.write('Makefile', 'build:\n\techo build\n')
        self.git('add', '.')
        self.assertEqual(deploy.detect_deploy(self.repo)['scripts'], [])

    def test_readme_without_a_deploy_heading_is_not_docs(self):
        self.write('README.md', '# Project\n\nJust a description, nothing deployable.\n')
        self.git('add', '.')
        self.assertEqual(deploy.detect_deploy(self.repo)['docs'], [])

    def test_untracked_dockerfile_is_ignored(self):
        self.write('Dockerfile', 'FROM scratch\n')  # created but not staged
        self.assertEqual(deploy.detect_deploy(self.repo)['docker'], [])

    def test_empty_repo_yields_all_empty_buckets(self):
        self.write('README.md', '# Project\n\nnothing deployable here\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'initial')
        self.assertEqual(
            deploy.detect_deploy(self.repo),
            {'docker': [], 'ci_cd': [], 'paas': [], 'infra': [], 'scripts': [], 'docs': []})


class CmdDeployDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _sandbox(self)

    def test_cmd_deploy_prints_without_error_default(self):
        (self.root / 'Dockerfile').write_text('FROM scratch\n')
        _git(self.root, 'add', '.')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            deploy.cmd_deploy(argparse.Namespace())
        out = buf.getvalue()
        self.assertIn('Deployment detection', out)
        self.assertIn('Dockerfile', out)

    def test_cmd_deploy_detect_subcommand_matches_default(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            deploy.cmd_deploy(argparse.Namespace(deploy_cmd='detect'))
        self.assertIn('Deployment detection', buf.getvalue())


class ValidateDeployConfigTests(unittest.TestCase):
    def base(self):
        return {'version': 1, 'targets': {'dev': {'command': ['echo', 'hi'], 'timeout': 60, 'evidence': 'ok'}}}

    def test_valid_config_is_returned(self):
        config = self.base()
        self.assertEqual(deploy.validate_deploy_config(config), config)

    def test_rejects_wrong_version(self):
        config = self.base(); config['version'] = 2
        with self.assertRaises(ValueError):
            deploy.validate_deploy_config(config)

    def test_rejects_invalid_target_name(self):
        config = {'version': 1, 'targets': {'Dev-1!': {'command': ['x'], 'timeout': 1, 'evidence': 'ok'}}}
        with self.assertRaises(ValueError):
            deploy.validate_deploy_config(config)

    def test_rejects_empty_command(self):
        config = self.base(); config['targets']['dev']['command'] = []
        with self.assertRaises(ValueError):
            deploy.validate_deploy_config(config)

    def test_rejects_command_with_non_strings(self):
        config = self.base(); config['targets']['dev']['command'] = ['echo', 1]
        with self.assertRaises(ValueError):
            deploy.validate_deploy_config(config)

    def test_rejects_zero_timeout(self):
        config = self.base(); config['targets']['dev']['timeout'] = 0
        with self.assertRaises(ValueError):
            deploy.validate_deploy_config(config)

    def test_rejects_negative_timeout(self):
        config = self.base(); config['targets']['dev']['timeout'] = -5
        with self.assertRaises(ValueError):
            deploy.validate_deploy_config(config)

    def test_rejects_non_int_timeout(self):
        config = self.base(); config['targets']['dev']['timeout'] = '60'
        with self.assertRaises(ValueError):
            deploy.validate_deploy_config(config)

    def test_rejects_empty_evidence(self):
        config = self.base(); config['targets']['dev']['evidence'] = '   '
        with self.assertRaises(ValueError):
            deploy.validate_deploy_config(config)

    def test_rejects_missing_evidence(self):
        config = self.base(); del config['targets']['dev']['evidence']
        with self.assertRaises(ValueError):
            deploy.validate_deploy_config(config)


class DeployConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='deploy-config-')
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)

    def test_missing_file_returns_default(self):
        self.assertEqual(deploy.deploy_config(self.state), {'version': 1, 'targets': {}})

    def test_corrupt_file_raises_systemexit(self):
        (self.state / 'deploy.json').write_text('{ not json')
        with self.assertRaises(SystemExit) as ctx:
            deploy.deploy_config(self.state)
        self.assertIn('Invalid deploy configuration', str(ctx.exception))


class SetRemoveTests(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _sandbox(self)

    def _cmd(self, **ns):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            deploy.cmd_deploy(argparse.Namespace(**ns))
        return buf.getvalue()

    def test_set_saves_target(self):
        self._cmd(deploy_cmd='set', target='dev', evidence='service responds', timeout=60,
                   description='', command=['echo', 'hi'])
        config = json.loads((self.state / 'deploy.json').read_text())
        self.assertEqual(config['targets']['dev']['command'], ['echo', 'hi'])
        self.assertEqual(config['targets']['dev']['evidence'], 'service responds')

    def test_set_strips_leading_double_dash(self):
        self._cmd(deploy_cmd='set', target='dev', evidence='ok', timeout=60,
                   description='', command=['--', 'echo', 'hi'])
        config = json.loads((self.state / 'deploy.json').read_text())
        self.assertEqual(config['targets']['dev']['command'], ['echo', 'hi'])

    def test_remove_drops_target(self):
        self._cmd(deploy_cmd='set', target='dev', evidence='ok', timeout=60, description='', command=['echo'])
        self._cmd(deploy_cmd='remove', target='dev')
        config = json.loads((self.state / 'deploy.json').read_text())
        self.assertNotIn('dev', config['targets'])


class RunTests(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _sandbox(self)

    def _set(self, target, command, evidence='ok', timeout=30):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            deploy.cmd_deploy(argparse.Namespace(deploy_cmd='set', target=target, evidence=evidence,
                                                   timeout=timeout, description='', command=command))

    def _run(self, target='dev', execute=False):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            deploy.cmd_deploy(argparse.Namespace(deploy_cmd='run', target=target, execute=execute))
        return buf.getvalue()

    def _deploy_rows(self):
        path = self.state / 'metrics.jsonl'
        if not path.exists(): return []
        return [row for row in (json.loads(line) for line in path.read_text().splitlines()) if row.get('event') == 'deploy']

    def test_dry_run_does_not_execute(self):
        marker = self.root / 'MARKER'
        self._set('dev', ['python3', '-c', "open('MARKER','w').close()"])
        out = self._run('dev', execute=False)
        self.assertIn('Dry run: nothing executed.', out)
        self.assertFalse(marker.exists())

    def test_run_target_not_configured_raises_systemexit(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run('dev', execute=False)
        self.assertIn('ai deploy set', str(ctx.exception))

    def test_execute_success_records_metric_and_log(self):
        self._set('dev', ['true'])
        out = self._run('dev', execute=True)
        self.assertIn('DEPLOYED', out)
        record = json.loads((self.state / 'deploy' / 'dev.json').read_text())
        self.assertTrue(record['succeeded'])
        self.assertEqual(record['exit_code'], 0)
        self.assertEqual(record['evidence'], 'ok')
        log = Path(record['log'])
        self.assertTrue(log.is_file())
        self.assertEqual(record['log_hash'], hashlib.sha256(log.read_bytes()).hexdigest())
        rows = self._deploy_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['target'], 'dev')
        self.assertTrue(rows[0]['succeeded'])

    def test_execute_failure_raises_systemexit_and_records_failure(self):
        self._set('dev', ['sh', '-c', 'exit 3'])
        with self.assertRaises(SystemExit):
            self._run('dev', execute=True)
        record = json.loads((self.state / 'deploy' / 'dev.json').read_text())
        self.assertFalse(record['succeeded'])
        self.assertEqual(record['exit_code'], 3)
        self.assertIsNone(record['evidence'])

    def test_execute_inside_gate_environment_is_refused(self):
        self._set('dev', ['true'])
        with mock.patch.dict(os.environ, {'AI_GATE': 'checks'}):
            with self.assertRaises(SystemExit) as ctx:
                self._run('dev', execute=True)
        self.assertIn('human-only', str(ctx.exception))
        self.assertFalse((self.state / 'deploy' / 'dev.json').exists())


class CheckRunbookTests(unittest.TestCase):
    def base(self):
        return {'summary': 'Deploys via a systemd service.', 'prerequisites': ['SSH access'],
                'dev_steps': ['run deploy.sh'], 'verification': ['curl the health endpoint'],
                'rollback': ['redeploy the previous tag'], 'confidence_caveats': [],
                'suggested_dev_command': ['./deploy.sh', 'dev']}

    def test_accepts_valid_runbook_with_empty_suggested_command(self):
        value = self.base(); value['suggested_dev_command'] = []
        self.assertEqual(deploy.check_runbook(value), value)

    def test_rejects_empty_summary(self):
        value = self.base(); value['summary'] = '   '
        with self.assertRaises(ValueError):
            deploy.check_runbook(value)

    def test_rejects_list_with_empty_string(self):
        value = self.base(); value['dev_steps'] = ['']
        with self.assertRaises(ValueError):
            deploy.check_runbook(value)

    def test_rejects_extra_field(self):
        value = self.base(); value['extra'] = 'nope'
        with self.assertRaises(ValueError):
            deploy.check_runbook(value)

    def test_rejects_missing_field(self):
        value = self.base(); del value['rollback']
        with self.assertRaises(ValueError):
            deploy.check_runbook(value)


class RenderRunbookTests(unittest.TestCase):
    def base(self):
        return {'summary': 'Deploys via a systemd service.', 'prerequisites': ['SSH access'],
                'dev_steps': ['run deploy.sh'], 'verification': ['curl the health endpoint'],
                'rollback': ['redeploy the previous tag'], 'confidence_caveats': [],
                'suggested_dev_command': ['./deploy.sh', 'dev']}

    def test_contains_ai_deploy_set_line_when_command_present(self):
        text = deploy.render_runbook(self.base())
        # Flags-first: `target` is a plain positional immediately before a
        # REMAINDER `command` positional, so a flag placed after `target`
        # would be swallowed into `command` and never parsed (see cli.py's
        # `deploy set` parser, matching `validators set`'s shape).
        self.assertIn('ai deploy set --evidence "..." dev', text)
        self.assertIn('./deploy.sh dev', text)

    def test_contains_no_command_message_when_empty(self):
        value = self.base(); value['suggested_dev_command'] = []
        text = deploy.render_runbook(value)
        self.assertIn('No readable dev deploy command was found', text)
        self.assertNotIn('ai deploy set --evidence "..." dev', text)


class PlanCachingTests(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _sandbox(self)

    def _runbook_value(self):
        return {'summary': 'x', 'prerequisites': [], 'dev_steps': [], 'verification': [],
                'rollback': [], 'confidence_caveats': [], 'suggested_dev_command': []}

    def _plan(self, refresh=False):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            deploy.cmd_deploy(argparse.Namespace(deploy_cmd='plan', refresh=refresh, timeout=600))
        return buf.getvalue()

    def test_caches_by_commit_and_refresh_forces_regeneration(self):
        with mock.patch.object(deploy, 'shutil') as shutil_mock, \
                mock.patch.object(deploy, 'run_codex_json', return_value=self._runbook_value()) as codex_mock:
            shutil_mock.which.return_value = '/usr/bin/codex'
            self._plan()
            self.assertEqual(codex_mock.call_count, 1)
            out = self._plan()  # no --refresh: cached, no second call
            self.assertIn('up to date', out)
            self.assertEqual(codex_mock.call_count, 1)
            self._plan(refresh=True)
            self.assertEqual(codex_mock.call_count, 2)

    def test_missing_codex_raises_systemexit(self):
        with mock.patch.object(deploy, 'shutil') as shutil_mock:
            shutil_mock.which.return_value = None
            with self.assertRaises(SystemExit) as ctx:
                self._plan()
        self.assertIn('Codex CLI missing', str(ctx.exception))


class DeploySetParserTests(unittest.TestCase):
    """Regression coverage for the real argparse tree, not a hand-built Namespace.

    `target` is a plain positional immediately followed by a REMAINDER
    `command` positional, so `--evidence` must be parsed flags-first (before
    `target`), matching `ai validators set`'s documented shape. A flag placed
    after `target` is silently swallowed into `command` instead of erroring
    cleanly, which is exactly what shipped-but-broken README/help text would
    have led a user into.
    """
    def setUp(self):
        from cli import parser
        self.parser = parser()

    def test_flags_before_target_parses_correctly(self):
        args = self.parser.parse_args(
            ['deploy', 'set', '--evidence', 'E', 'dev', '--', 'sh', '-c', 'true'])
        self.assertEqual(args.target, 'dev')
        self.assertEqual(args.evidence, 'E')
        self.assertEqual(args.command, ['sh', '-c', 'true'])

    def test_flags_after_target_do_not_parse(self):
        # Documents the trap: --evidence after `target` is swallowed into the
        # REMAINDER `command` positional, so argparse reports it as missing.
        with self.assertRaises(SystemExit):
            self.parser.parse_args(
                ['deploy', 'set', 'dev', '--evidence', 'E', '--', 'sh', '-c', 'true'])


if __name__ == '__main__':
    unittest.main()
