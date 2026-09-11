"""The "curation is human-only" invariant, extended to every command that writes what a
gate/validator is told or who judges it: `ai rules add/remove`, `ai validators
set/install/remove/propose --apply`, `ai providers set`, `ai capabilities enable/disable`,
`ai skill enable/disable/create`.

Mirrors the existing lessons/prompt/metrics guard (`core.require_human`): a model running
inside a gate (`AI_GATE`) or a validator (`AI_TASK_DIR`) must not be able to change its own
future prompt context, or configure the validator/reviewer that will judge it, and have the
change survive even though the gate it ran from then fails on the fingerprint mismatch.
"""
import argparse
import ast
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import os

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import capabilities  # noqa: E402
import core  # noqa: E402
import gates  # noqa: E402
import providers  # noqa: E402
import repo  # noqa: E402
import skills  # noqa: E402


def _git(cwd, *args):
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True)


def _sandbox(tc):
    """Isolated git repo + redirected external state, matching tests/test_gates.py::_sandbox."""
    tmp = tempfile.TemporaryDirectory(prefix='human-only-guard-test-')
    tc.addCleanup(tmp.cleanup)
    home = Path(tmp.name)
    repo_dir = home / 'repo'
    repo_dir.mkdir()
    cfg = home / 'config' / 'ai-agent-stack'

    env = {k: v for k, v in os.environ.items() if k not in ('AI_GATE', 'AI_TASK_DIR')}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'), AI_TASK_ID='guard-task')

    for args in (('init', '-q'), ('config', 'user.name', 'T'), ('config', 'user.email', 't@e.com')):
        _git(repo_dir, *args)
    (repo_dir / 'app.txt').write_text('initial\n')
    _git(repo_dir, 'add', '.')
    _git(repo_dir, 'commit', '-qm', 'init')

    envp = mock.patch.dict(os.environ, env, clear=True)
    envp.start()
    tc.addCleanup(envp.stop)
    cfgp = mock.patch.object(core, 'CONFIG_ROOT', cfg)
    cfgp.start()
    tc.addCleanup(cfgp.stop)

    old_cwd = os.getcwd()
    os.chdir(repo_dir)
    tc.addCleanup(os.chdir, old_cwd)

    root = core.git_root()
    state = core.repo_state(root)
    return root, state


def _run_silently(fn, args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(argparse.Namespace(**args))
    return buf.getvalue()


def _snapshot(path):
    """None if the path is absent, 'dir' if it's a directory (e.g. a skill folder that must
    stay uncreated), else its exact bytes -- covers "must stay unwritten" and "must stay
    unchanged" with one comparison."""
    if not path.exists(): return None
    if path.is_dir(): return 'dir'
    return path.read_bytes()


class HumanOnlyGuardTableTests(unittest.TestCase):
    """Every mutating action of the six handlers rejects under AI_GATE and AI_TASK_DIR,
    without writing anything -- and the read-only actions of the same handlers keep working
    under AI_GATE (negative control)."""

    def setUp(self):
        self.root, self.state = _sandbox(self)

    def _rows(self):
        """(label, fn, args, target path, setup) for every action the plan requires guarded."""
        rows = []

        def setup_existing_rule():
            core.save_json(self.state / 'rules.json', [{"rule": "existing", "scope": "**",
                "source": "user", "confidence": 1.0, "created_at": 0}])

        rows.append(('rules add', repo.cmd_rules,
                     dict(rules_cmd='add', rule='New rule', scope='**'),
                     self.state / 'rules.json', None))
        rows.append(('rules remove', repo.cmd_rules,
                     dict(rules_cmd='remove', index=1),
                     self.state / 'rules.json', setup_existing_rule))
        rows.append(('validators set', gates.cmd_validators,
                     dict(action='set', name='checks', adapter='json', evidence=None,
                          timeout=600, command=[sys.executable, '-c', 'pass']),
                     self.state / 'validators.json', None))
        rows.append(('validators install', gates.cmd_validators,
                     dict(action='install'),
                     self.state / 'validators.json', None))
        rows.append(('validators remove', gates.cmd_validators,
                     dict(action='remove', name='checks'),
                     self.state / 'validators.json', None))
        rows.append(('validators propose --apply', gates.cmd_validators,
                     dict(action='propose', apply=True, json=False),
                     self.state / 'validators.json', None))
        rows.append(('providers set', providers.cmd_providers,
                     dict(providers_cmd='set', builder='claude', reviewer=None,
                          reviewer_command=[], json=False),
                     self.state / 'repo.json', None))
        rows.append(('capabilities enable', capabilities.cmd_capabilities,
                     dict(capabilities_cmd='enable', name='crg'),
                     self.state / 'capability-overrides.json', None))
        rows.append(('capabilities disable', capabilities.cmd_capabilities,
                     dict(capabilities_cmd='disable', name='crg'),
                     self.state / 'capability-overrides.json', None))
        rows.append(('skill enable', skills.cmd_skill,
                     dict(skill_cmd='enable', name='diagnosing-bugs'),
                     self.state / 'skill-overrides.json', None))
        rows.append(('skill disable', skills.cmd_skill,
                     dict(skill_cmd='disable', name='diagnosing-bugs'),
                     self.state / 'skill-overrides.json', None))
        rows.append(('skill create', skills.cmd_skill,
                     dict(skill_cmd='create', name='guard-test-skill', cost='low',
                          category='testing', priority=50, task_types='', triggers='',
                          stages='', always_consider=False, repo=True),
                     self.state / 'skills' / 'guard-test-skill', None))
        return rows

    def test_every_mutating_action_rejects_under_ai_gate(self):
        for label, fn, ns, target, setup in self._rows():
            with self.subTest(label=label, env='AI_GATE'):
                if setup: setup()
                before = _snapshot(target)
                with mock.patch.dict(os.environ, {'AI_GATE': 'checks'}):
                    with self.assertRaises(SystemExit) as ctx:
                        _run_silently(fn, ns)
                    self.assertIn('human-only', str(ctx.exception))
                self.assertEqual(_snapshot(target), before, f'{label} wrote to {target} under AI_GATE')

    def test_every_mutating_action_rejects_under_ai_task_dir(self):
        for label, fn, ns, target, setup in self._rows():
            with self.subTest(label=label, env='AI_TASK_DIR'):
                if setup: setup()
                before = _snapshot(target)
                with mock.patch.dict(os.environ, {'AI_TASK_DIR': str(self.state)}):
                    with self.assertRaises(SystemExit) as ctx:
                        _run_silently(fn, ns)
                    self.assertIn('human-only', str(ctx.exception))
                self.assertEqual(_snapshot(target), before, f'{label} wrote to {target} under AI_TASK_DIR')

    def test_read_only_actions_still_work_under_ai_gate(self):
        read_only_rows = [
            ('rules list', repo.cmd_rules, dict(rules_cmd='list')),
            ('validators show', gates.cmd_validators, dict(action='show')),
            ('validators propose (no --apply)', gates.cmd_validators,
             dict(action='propose', apply=False, json=False)),
            ('providers show', providers.cmd_providers,
             dict(providers_cmd='show', builder=None, reviewer=None, reviewer_command=[], json=False)),
            ('providers doctor', providers.cmd_providers,
             dict(providers_cmd='doctor', builder=None, reviewer=None, reviewer_command=[], json=False)),
            ('capabilities list', capabilities.cmd_capabilities, dict(capabilities_cmd='list')),
            ('skill list', skills.cmd_skill, dict(skill_cmd='list', task=None, profile='standard')),
        ]
        with mock.patch.dict(os.environ, {'AI_GATE': 'checks'}):
            for label, fn, ns in read_only_rows:
                with self.subTest(label=label):
                    _run_silently(fn, ns)  # must not raise


class RegressionGuardAstTests(unittest.TestCase):
    """Every `cmd_*` in ai_stack/*.py whose body writes (`save_json`, `.write_text(`,
    `.unlink(`) must call `require_human`, or be named here with a one-line reason it isn't
    curation. A new mutating command with neither forces this test to fail, so someone has to
    decide -- the same guarantee the plan asked for."""

    # name -> why it is not curation of what a gate/validator reads or who judges it.
    ALLOWLIST = {
        'cmd_start': 'creates/opens a task workspace, not curated prompt context',
        'cmd_planrun': 'runs the plan/gate/build loop itself, does not curate context',
        'cmd_ticket': 'writes the task ticket the human is drafting, not gate input',
        'cmd_handoff': 'writes a handoff note for a human, not gate/validator input',
        'cmd_pipeline': 'orchestrates gates, does not curate what they are told',
        'cmd_ready': 'writes readiness state derived from gate results, not an input to them',
        'cmd_clarify': 'records clarifying Q&A for the current task, not curated rules',
        'cmd_benchmark': 'writes benchmark run output, not gate/validator input',
        'cmd_profile': 'writes the deep repo profile cache, a detection artifact not curation',
        'cmd_init': 'first-time repo scaffolding, runs before any task/gate exists',
        'cmd_docs': 'writes doc-tool caches (Context7 IDs, docs cache), not gate input',
    }

    def test_every_mutating_cmd_is_guarded_or_allowlisted(self):
        ai_stack = ROOT / 'ai_stack'
        offenders = []
        for path in sorted(ai_stack.glob('*.py')):
            src = path.read_text()
            tree = ast.parse(src, filename=str(path))
            lines = src.splitlines()
            for node in ast.walk(tree):
                if not (isinstance(node, ast.FunctionDef) and node.name.startswith('cmd_')):
                    continue
                body = '\n'.join(lines[node.lineno - 1:node.end_lineno])
                writes = ('save_json(' in body) or ('.write_text(' in body) or ('.unlink(' in body)
                if not writes:
                    continue
                guarded = 'require_human(' in body
                if not guarded and node.name not in self.ALLOWLIST:
                    offenders.append(f'{path.name}:{node.name}')
        self.assertEqual(offenders, [],
            'New mutating cmd_* without require_human() or an ALLOWLIST entry: ' + ', '.join(offenders))

    def test_allowlist_entries_are_all_actually_unguarded_mutators(self):
        # Catches a stale allowlist entry: a name in ALLOWLIST that no longer exists, or that
        # no longer writes anything, would otherwise silently stop meaning what its comment says.
        ai_stack = ROOT / 'ai_stack'
        found = set()
        for path in sorted(ai_stack.glob('*.py')):
            src = path.read_text()
            tree = ast.parse(src, filename=str(path))
            lines = src.splitlines()
            for node in ast.walk(tree):
                if not (isinstance(node, ast.FunctionDef) and node.name.startswith('cmd_')):
                    continue
                body = '\n'.join(lines[node.lineno - 1:node.end_lineno])
                writes = ('save_json(' in body) or ('.write_text(' in body) or ('.unlink(' in body)
                if writes and node.name in self.ALLOWLIST:
                    found.add(node.name)
        missing = set(self.ALLOWLIST) - found
        self.assertEqual(missing, set(), 'Stale allowlist entries (no longer a mutating cmd_*): ' + ', '.join(sorted(missing)))


if __name__ == '__main__':
    unittest.main()
