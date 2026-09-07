import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installer', ROOT / 'ai_stack/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='stack-test-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.repo = self.home / 'repo'
        self.repo.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / 'config'), AI_TASK_ID='one')
        for args in [('init', '-q'), ('config', 'user.name', 'Test'), ('config', 'user.email', 'test@example.com')]:
            self.git(*args)
        (self.repo / 'app.txt').write_text('initial\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'initial')

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.repo, env=self.env, text=True).strip()

    def ai(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(ROOT / 'ai_stack/cli.py'), *args], cwd=self.repo, env=self.env, text=True, capture_output=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def plan(self, *args):
        self.ai('plan', 'small change', '--profile', 'fast', '--base', 'HEAD', *args)

    def pass_gates(self):
        for gate in ('checks', 'regression', 'cleanup', 'provenance', 'ponytail', 'summary', 'contract'):
            self.ai('gate', gate, '--', sys.executable, '-c', 'import json; print(json.dumps({"status":"PASS","evidence":["verified fixture"]}))')

    def test_readiness_requires_fresh_evidence(self):
        self.plan()
        self.assertIn('NEEDS_HUMAN', self.ai('ready', ok=False))
        self.pass_gates()
        self.assertIn('PR_READY', self.ai('ready'))
        (self.repo / 'app.txt').write_text('changed\n')
        self.assertIn('NEEDS_HUMAN', self.ai('ready', ok=False))

    def test_failure_and_modified_gate_are_not_passes(self):
        self.plan()
        self.pass_gates()
        self.ai('gate', 'checks', '--', sys.executable, '-c', 'raise SystemExit(2)', ok=False)
        self.assertIn('FAILED', self.ai('ready', ok=False))
        output = self.ai('gate', 'checks', '--', sys.executable, '-c', 'open("app.txt","w").write("changed")', ok=False)
        self.assertIn('changed during gate', output)

    def test_untracked_changes_invalidate_evidence(self):
        self.plan()
        self.pass_gates()
        (self.repo / 'new.txt').write_text('untracked')
        self.assertIn('NEEDS_HUMAN', self.ai('ready', ok=False))

    def test_missing_or_tampered_log_invalidates_gate(self):
        self.plan()
        self.pass_gates()
        state = Path(self.ai('path').strip())
        record = json.loads((state / 'gates/checks.json').read_text())
        Path(record['log']).write_text('changed evidence')
        self.assertIn('NEEDS_HUMAN', self.ai('ready', ok=False))

    def test_rules_invalidate_evidence(self):
        self.plan()
        self.pass_gates()
        self.ai('rules', 'add', 'New acceptance rule')
        self.assertIn('NEEDS_HUMAN', self.ai('ready', ok=False))

    def test_task_and_worktree_isolation(self):
        self.plan('--task-id', 'one')
        one = Path(self.ai('path', '--task-id', 'one').strip())
        before = (one / 'state/current-plan.json').read_text()
        self.plan('--task-id', 'two')
        two = Path(self.ai('path', '--task-id', 'two').strip())
        self.assertNotEqual(one, two)
        self.assertEqual(before, (one / 'state/current-plan.json').read_text())
        self.git('remote', 'add', 'origin', 'https://example.com/repo.git')
        first = self.ai('path').strip()
        other = self.home / 'worktree'
        self.git('worktree', 'add', '-qb', 'other', str(other))
        self.repo = other
        self.assertNotEqual(first, self.ai('path').strip())

    def test_oversized_context_does_not_replace_plan(self):
        self.plan()
        state = Path(self.ai('path').strip())
        before = (state / 'state/current-plan.json').read_text()
        output = self.ai('plan', 'x' * 40000, '--profile', 'fast', '--base', 'HEAD', ok=False)
        self.assertIn('exceeds budget', output)
        self.assertEqual(before, (state / 'state/current-plan.json').read_text())

    def test_oversized_handoff_is_rejected(self):
        self.plan()
        state = Path(self.ai('path').strip())
        self.assertIn('exceeds budget', self.ai('handoff', '--evidence', 'x' * 10000, ok=False))
        self.assertEqual(list((state / 'handoffs').iterdir()), [])

    def test_risk_adds_security_and_review(self):
        self.plan()
        self.pass_gates()
        (self.repo / 'auth').mkdir()
        (self.repo / 'auth/token.py').write_text('token = 1')
        output = self.ai('ready', ok=False)
        self.assertIn('review', output)
        self.assertIn('security', output)

    def test_success_exit_without_review_verdict_is_rejected(self):
        self.plan()
        self.ai('gate', 'review', '--', sys.executable, '-c', 'print("review failed")', ok=False)

    def test_gate_timeout(self):
        self.plan()
        self.ai('gate', '--timeout', '1', 'checks', '--', sys.executable, '-c', 'import time; time.sleep(10)', ok=False)
        record = json.loads((Path(self.ai('path').strip()) / 'gates/checks.json').read_text())
        self.assertEqual(record['exit_code'], 124)

    def configure_pipeline(self, failing=None):
        for name in ('cleanup','checks','regression','contract','provenance','ponytail','summary'):
            script = 'import json; print(json.dumps({"status":"PASS","evidence":["fixture validated"],"usage":{"input_tokens":12,"output_tokens":3,"cost_usd":0.01}}))'
            if name == failing:
                script = 'raise SystemExit(2)'
            self.ai('validators','set',name,'--',sys.executable,'-c',script)

    def test_pipeline_preflight_success_resume_and_metrics(self):
        self.plan()
        self.assertIn('configure validators',self.ai('pipeline',ok=False))
        self.configure_pipeline()
        preview=json.loads(self.ai('pipeline','--dry-run'))
        self.assertEqual(preview['order'][0],'cleanup')
        self.assertEqual(json.loads(self.ai('metrics','--json'))['gate_attempts'],0)
        self.assertIn('PR_READY',self.ai('pipeline'))
        report=json.loads(self.ai('metrics','--json'))
        self.assertEqual(report['gate_passes'],7)
        self.assertEqual(report['usage']['input_tokens']['reported_total'],84)
        self.assertEqual(report['pipeline_successes'],1)
        self.assertIn('fresh evidence reused',self.ai('pipeline','--resume'))
        self.assertEqual(json.loads(self.ai('metrics','--json'))['gate_attempts'],7)
        self.assertEqual(json.loads(self.ai('metrics','--task-id','two','--json'))['gate_attempts'],0)
        self.assertEqual(json.loads(self.ai('metrics','--all-tasks','--json'))['gate_attempts'],7)
        self.assertEqual(self.git('status','--porcelain'),'')

    def test_pipeline_stops_on_failure_and_config_changes_invalidate(self):
        self.plan()
        self.configure_pipeline(failing='checks')
        self.ai('pipeline',ok=False)
        report=json.loads(self.ai('metrics','--json'))
        self.assertEqual(report['gate_attempts'],2)
        self.assertEqual(report['gate_failures'],1)
        self.assertEqual(report['pipeline_successes'],0)
        self.configure_pipeline()
        self.assertIn('PR_READY',self.ai('pipeline','--resume'))
        self.ai('validators','remove','checks')
        self.assertIn('NEEDS_HUMAN',self.ai('ready',ok=False))

    def test_exit_code_adapter_and_json_checks_are_explicit(self):
        self.plan()
        self.configure_pipeline()
        self.ai('validators','set','checks','--',sys.executable,'-c','print("not a verdict")')
        self.ai('pipeline',ok=False)
        self.ai('validators','set','--adapter','exit-code','checks','--',sys.executable,'-c','pass',ok=False)
        self.ai('validators','set','--adapter','exit-code','--evidence','Project tests succeeded',
                'checks','--',sys.executable,'-c','pass')
        self.assertIn('PR_READY',self.ai('pipeline'))
        self.ai('validators','set','--timeout','0','checks','--','true',ok=False)

    def test_pipeline_requires_security_and_rejects_mutation(self):
        self.plan()
        self.configure_pipeline()
        self.ai('validators','set','cleanup','--',sys.executable,'-c',
                'open("app.txt","w").write("changed"); print(\'{"status":"PASS","evidence":["fixture"]}\')')
        self.assertIn('changed during gate',self.ai('pipeline',ok=False))
        self.assertEqual(json.loads(self.ai('metrics','--json'))['gate_attempts'],1)
        (self.repo/'auth').mkdir()
        (self.repo/'auth/token.py').write_text('token = 1')
        output=self.ai('pipeline',ok=False)
        self.assertIn('security',output)
        self.assertIn('review',output)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='stack-install-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.destination = self.root / 'data/ai-agent-stack'
        self.bin = self.root / 'bin'

    def test_upgrade_and_reinstall_from_installed_source(self):
        first = installer.install(ROOT, self.destination, self.bin)
        second = installer.install(self.destination, self.destination, self.bin)
        self.assertTrue(first.exists())
        self.assertNotEqual(first, second)
        self.assertEqual(self.destination.resolve(), second.resolve())
        self.assertFalse((second / '.git').exists())
        self.assertFalse((second / 'tests').exists())

    def test_validation_failure_preserves_installation(self):
        first = installer.install(ROOT, self.destination, self.bin)
        with patch.object(installer.subprocess, 'check_output', return_value='wrong'):
            with self.assertRaises(RuntimeError):
                installer.install(ROOT, self.destination, self.bin)
        self.assertEqual(self.destination.resolve(), first.resolve())

    def test_activation_failure_restores_legacy_directory(self):
        self.destination.mkdir(parents=True)
        (self.destination / 'marker').write_text('old')
        real_replace = os.replace
        def fail_command_link(source, dest):
            if Path(dest) == self.bin / 'ai':
                raise OSError('simulated link failure')
            return real_replace(source, dest)
        with patch.object(installer.os, 'replace', side_effect=fail_command_link):
            with self.assertRaises(OSError):
                installer.install(ROOT, self.destination, self.bin)
        self.assertEqual((self.destination / 'marker').read_text(), 'old')

    def test_activation_failure_restores_previous_symlink(self):
        first = installer.install(ROOT, self.destination, self.bin)
        real_replace = os.replace
        def fail_command_link(source, dest):
            if Path(dest) == self.bin / 'ai':
                raise OSError('simulated link failure')
            return real_replace(source, dest)
        with patch.object(installer.os, 'replace', side_effect=fail_command_link):
            with self.assertRaises(OSError):
                installer.install(ROOT, self.destination, self.bin)
        self.assertEqual(self.destination.resolve(), first.resolve())


if __name__ == '__main__':
    unittest.main()
