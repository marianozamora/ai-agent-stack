"""Explicit task lifecycle: start / switch / close / current / tasks.

The invariant under test is contract isolation: a new task must never inherit the
previous task's acceptance criteria, which is exactly what the old branch-derived
identity allowed when two tickets were worked on one branch.
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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import core  # noqa: E402
import tasks  # noqa: E402


def namespace(**fields):
    defaults = dict(id=None, title='', ticket_file=None, base=None, resume=False,
                    switch=False, reason='', status=None, json=False)
    return argparse.Namespace(**{**defaults, **fields})


class TaskLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tasks-test-')
        self.addCleanup(self.temp.cleanup)
        home = Path(self.temp.name)
        self.repo = home / 'repo'
        self.repo.mkdir()
        for argv in [('init', '-q', '-b', 'master'), ('config', 'user.name', 'Test'),
                     ('config', 'user.email', 'test@example.com')]:
            subprocess.check_call(['git', *argv], cwd=self.repo,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (self.repo / 'app.py').write_text('value = 1\n')
        subprocess.check_call(['git', 'add', '.'], cwd=self.repo, stdout=subprocess.DEVNULL)
        subprocess.check_call(['git', 'commit', '-qm', 'initial'], cwd=self.repo,
                              stdout=subprocess.DEVNULL)

        self.previous = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, self.previous)
        for name in ('AI_TASK_ID', 'AI_GATE', 'AI_TASK_DIR'):
            patcher = patch.dict(os.environ, {}, clear=False)
            patcher.start()
            self.addCleanup(patcher.stop)
            os.environ.pop(name, None)
        core.TASK_ID = None
        patcher = patch.object(core, 'CONFIG_ROOT', home / 'config')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state = core.repo_state(self.repo)

    def start(self, identity, **fields):
        tasks.cmd_start(namespace(id=identity, **fields))

    def contract(self, identity):
        return tasks.contract_path(self.state, self.repo, identity)

    def set_acceptance(self, identity, text):
        path = self.contract(identity)
        path.write_text(path.read_text().replace('acceptance: []', f'acceptance: ["{text}"]'))

    def test_ticket_file_snapshot_carries_clarification_markers(self):
        """`ai start --ticket-file` is the primary path a spec reaches the contract by.

        Its ticket.json used to be spelled out by hand alongside two other write sites, so
        `clarifications_needed` reached only the two that were remembered -- and an
        unresolved `[NEEDS CLARIFICATION]` marker never got to `ai clarify` at all. All
        three now go through workflow.ticket_snapshot().
        """
        spec = self.repo / 'spec.md'
        spec.write_text('### Acceptance Scenarios\n'
                        '1. Given a cart, When checkout, Then an order exists.\n'
                        '2. System MUST [NEEDS CLARIFICATION: which currencies?]\n')
        self.start('PROJ-9', ticket_file=str(spec))
        snapshot = json.loads((tasks.task_dir(self.state, self.repo, 'PROJ-9')
                               / 'state' / 'ticket.json').read_text())
        self.assertEqual(snapshot['clarifications_needed'], ['which currencies?'])
        self.assertTrue(any('an order exists' in i for i in snapshot['acceptance_items']))

    def test_a_spanish_ticket_fills_every_contract_field(self):
        # The first campaign ticket: Spanish headings detected nothing, and
        # must_not_change/risk_notes had to be copied into the contract by hand.
        ticket = self.repo / 'ticket.md'
        ticket.write_text('# Notificar la firma\n\n## Objetivo\n\nUn servicio avisa a los\ndemás participantes.\n\n'
                          '## Criterios de aceptación\n\n1. Existe `NotifyPromiseSigned`.\n2. El firmante no recibe aviso.\n\n'
                          '## No debe cambiar\n\n- backend/payments/**\n- frontend/**\n\n'
                          '## Notas de riesgo\n\n- accept mueve dinero.\n')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.start('PROJ-7', title='Notificar la firma', ticket_file=str(ticket))
        contract = self.contract('PROJ-7').read_text()
        self.assertIn('objective: "Un servicio avisa a los dem\\u00e1s participantes."', contract)
        self.assertIn('El firmante no recibe aviso.', contract)
        self.assertIn('must_not_change: ["backend/payments/**", "frontend/**"]', contract)
        self.assertIn('risk_notes: ["accept mueve dinero."]', contract)
        self.assertIn('2 acceptance item(s), 2 must_not_change, 1 risk note(s)', out.getvalue())

    def test_a_ticket_never_overwrites_a_human_list_on_resume(self):
        self.start('PROJ-8', title='t')
        path = self.contract('PROJ-8')
        path.write_text(path.read_text().replace('must_not_change: []', 'must_not_change: ["mine"]'))
        ticket = self.repo / 'ticket.md'
        ticket.write_text('## Must not change\n\n- theirs\n\n## Objective\n\nOther.\n')
        self.start('PROJ-8', resume=True, ticket_file=str(ticket))
        text = path.read_text()
        self.assertIn('must_not_change: ["mine"]', text)
        self.assertNotIn('Other.', text)

    def _configure_checks(self, command):
        core.save_json(self.state / 'validators.json', {'version': 1, 'validators': {
            'checks': {'adapter': 'exit-code', 'command': command, 'evidence': 'ok', 'timeout': 30}}})

    def test_start_runs_a_baseline_and_says_when_the_base_is_already_red(self):
        self._configure_checks(['sh', '-c', 'echo lint error; exit 1'])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.start('PROJ-3')
        self.assertIn('baseline: checks FAIL before any change (exit 1)', out.getvalue())
        baseline = json.loads((tasks.task_dir(self.state, self.repo, 'PROJ-3') / 'state' / 'baseline.json').read_text())
        self.assertFalse(baseline['passed'])
        self.assertIn('lint error', Path(baseline['log']).read_text())

    def test_a_green_baseline_is_reported_and_can_be_skipped(self):
        self._configure_checks(['true'])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.start('PROJ-4')
        self.assertIn('baseline: checks PASS', out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.start('PROJ-5', switch=True, no_baseline=True)
        self.assertNotIn('baseline:', out.getvalue())

    def test_start_warns_when_the_base_is_already_a_large_review(self):
        base = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=self.repo, capture_output=True, text=True).stdout.strip()
        (self.repo / 'big.py').write_text(''.join(f'x{i} = {i}\n' for i in range(tasks.BASE_DRIFT_LINES + 1)))
        subprocess.check_call(['git', 'add', '.'], cwd=self.repo, stdout=subprocess.DEVNULL)
        subprocess.check_call(['git', 'commit', '-qm', 'big'], cwd=self.repo, stdout=subprocess.DEVNULL)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.start('PROJ-6', base=base)
        self.assertIn('changed line(s) away from this checkout', out.getvalue())

    def test_a_new_task_never_inherits_the_previous_contract(self):
        self.start('PROJ-1', title='first')
        self.set_acceptance('PROJ-1', 'first criterion')
        self.start('PROJ-2', switch=True)
        self.assertIn('acceptance: []', self.contract('PROJ-2').read_text())
        self.assertIn('first criterion', self.contract('PROJ-1').read_text())

    def test_starting_over_an_active_task_is_refused_without_switch(self):
        self.start('PROJ-1')
        with self.assertRaises(SystemExit) as caught:
            self.start('PROJ-2')
        self.assertIn('still active', str(caught.exception))

    def test_restarting_the_same_task_is_refused_without_resume(self):
        self.start('PROJ-1')
        with self.assertRaises(SystemExit) as caught:
            self.start('PROJ-1')
        self.assertIn('already exists', str(caught.exception))

    def test_switch_preserves_each_contract(self):
        self.start('PROJ-1')
        self.set_acceptance('PROJ-1', 'first criterion')
        self.start('PROJ-2', switch=True)
        self.set_acceptance('PROJ-2', 'second criterion')
        tasks.cmd_switch(namespace(id='PROJ-1'))
        self.assertIn('first criterion', self.contract('PROJ-1').read_text())
        self.assertIn('second criterion', self.contract('PROJ-2').read_text())
        self.assertEqual(core.active_task_id(self.state, self.repo), 'PROJ-1')

    def test_switch_pauses_the_previous_task(self):
        self.start('PROJ-1')
        self.start('PROJ-2', switch=True)
        self.assertEqual(tasks.read_task(self.state, self.repo, 'PROJ-1')['status'], 'paused')
        self.assertEqual(tasks.read_task(self.state, self.repo, 'PROJ-2')['status'], 'active')

    def test_close_clears_the_pointer_and_blocks_further_evidence(self):
        self.start('PROJ-1')
        tasks.cmd_close(namespace(id=None, reason='shipped'))
        self.assertEqual(core.active_task_id(self.state, self.repo), '')
        meta = tasks.read_task(self.state, self.repo, 'PROJ-1')
        self.assertEqual(meta['status'], 'closed')
        self.assertEqual(meta['close_reason'], 'shipped')

    def test_require_open_task_refuses_a_closed_task(self):
        self.start('PROJ-1')
        tasks.cmd_close(namespace(id=None))
        core.TASK_ID = 'PROJ-1'
        self.addCleanup(setattr, core, 'TASK_ID', None)
        with self.assertRaises(SystemExit) as caught:
            tasks.require_open_task(self.state)
        self.assertIn('is closed', str(caught.exception))

    def test_require_open_task_allows_an_active_task(self):
        self.start('PROJ-1')
        directory, meta = tasks.require_open_task(self.state)
        self.assertEqual(meta['id'], 'PROJ-1')
        self.assertTrue(directory.is_dir())

    def test_switching_to_a_closed_task_is_refused(self):
        self.start('PROJ-1')
        tasks.cmd_close(namespace(id=None))
        with self.assertRaises(SystemExit) as caught:
            tasks.cmd_switch(namespace(id='PROJ-1'))
        self.assertIn('closed', str(caught.exception))

    def test_resume_reopens_without_rewriting_the_objective_or_title(self):
        self.start('PROJ-1', title='first ticket')
        self.set_acceptance('PROJ-1', 'first criterion')
        path = self.contract('PROJ-1')
        path.write_text(path.read_text().replace('objective: "first ticket"',
                                                 'objective: "a human edited this"'))
        tasks.cmd_close(namespace(id=None))
        self.start('PROJ-1', resume=True, switch=True)
        self.assertIn('a human edited this', path.read_text())
        self.assertIn('first criterion', path.read_text())
        self.assertEqual(tasks.read_task(self.state, self.repo, 'PROJ-1')['title'], 'first ticket')
        self.assertEqual(tasks.read_task(self.state, self.repo, 'PROJ-1')['status'], 'active')

    def test_identity_precedence_is_ai_task_id_then_active_task(self):
        self.start('PROJ-1')
        self.assertEqual(core.task_identity(self.state, self.repo), 'PROJ-1')
        # AI_TASK_ID (set by `ai gate`) still wins, so a gate stays pinned to the task
        # that launched it even if the pointer moves mid-run.
        with patch.dict(os.environ, {'AI_TASK_ID': 'GATE-TASK'}):
            self.assertEqual(core.task_identity(self.state, self.repo), 'GATE-TASK')

    def test_no_active_task_is_refused_with_no_branch_fallback(self):
        # The deprecated branch-derived identity is gone: a command that needs a task
        # directory now stops instead of silently reusing another ticket's contract.
        with self.assertRaises(SystemExit) as caught:
            core.task_identity(self.state, self.repo)
        self.assertIn('no active task', str(caught.exception))
        self.assertIn('ai start', str(caught.exception))

    def test_task_json_keeps_lifecycle_fields_across_task_state_calls(self):
        self.start('PROJ-1', title='keeps this')
        core.task_state(self.state)  # the call every other command makes
        meta = tasks.read_task(self.state, self.repo, 'PROJ-1')
        self.assertEqual(meta['title'], 'keeps this')
        self.assertEqual(meta['status'], 'active')

    def test_tasks_listing_is_scoped_to_this_checkout(self):
        self.start('PROJ-1')
        self.start('PROJ-2', switch=True)
        listed = {m['id'] for m in tasks.all_tasks(self.state, self.repo)}
        self.assertEqual(listed, {'PROJ-1', 'PROJ-2'})

    def test_invalid_identity_is_rejected(self):
        for bad in ('', '../escape', 'has space', '-leading-dash'):
            with self.assertRaises(SystemExit):
                tasks.check_identity(bad)


if __name__ == '__main__':
    unittest.main()
