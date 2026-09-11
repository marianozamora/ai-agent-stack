"""`ai close` lock reclamation, and in-process coverage of `ai current` / `ai tasks`.

tasks.py:284-330 (cmd_current, cmd_tasks) ran at 0% even with the subprocess suite,
because tests/test_workflow.py never inspects their output in detail. This file
drives them directly against a sandbox repo, the same pattern as tests/test_tasks.py.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
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


class _TaskSandbox(unittest.TestCase):
    """A throwaway git repo with HOME/XDG redirected, mirroring tests/test_tasks.py."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tasks-status-test-')
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

    def directory(self, identity):
        return tasks.task_dir(self.state, self.repo, identity)


class CloseOrphanedLockTests(_TaskSandbox):
    """`ai close` recovers a stale pipeline.lock the same way `task_lock` does."""

    def _write_lock(self, identity, **fields):
        payload = {'pid': 999999, 'host': socket.gethostname(), 'command': 'ai pipeline',
                  'started_at': time.time(), **fields}
        (self.directory(identity) / tasks.LOCK_NAME).write_text(json.dumps(payload))
        return payload

    def test_a_stale_lock_is_reclaimed_and_the_task_closes(self):
        self.start('PROJ-1')
        payload = self._write_lock('PROJ-1')
        lock = self.directory('PROJ-1') / tasks.LOCK_NAME
        with patch.object(tasks, 'process_alive', return_value=False):
            tasks.cmd_close(namespace(id='PROJ-1'))
        self.assertFalse(lock.exists())
        meta = tasks.read_task(self.state, self.repo, 'PROJ-1')
        self.assertEqual(meta['status'], 'closed')

    def test_reclaiming_prints_the_same_message_as_task_lock(self):
        import io, contextlib
        self.start('PROJ-1')
        self._write_lock('PROJ-1')
        buf = io.StringIO()
        with patch.object(tasks, 'process_alive', return_value=False):
            with contextlib.redirect_stdout(buf):
                tasks.cmd_close(namespace(id='PROJ-1'))
        self.assertIn('Reclaiming a stale lock from pid 999999 (no such process).', buf.getvalue())

    def test_a_live_lock_still_refuses_and_the_task_stays_open(self):
        self.start('PROJ-1')
        self._write_lock('PROJ-1')
        with patch.object(tasks, 'process_alive', return_value=True):
            with self.assertRaises(SystemExit) as caught:
                tasks.cmd_close(namespace(id='PROJ-1'))
        self.assertIn('still running', str(caught.exception))
        meta = tasks.read_task(self.state, self.repo, 'PROJ-1')
        self.assertEqual(meta['status'], 'active')
        self.assertTrue((self.directory('PROJ-1') / tasks.LOCK_NAME).exists())

    def test_a_foreign_host_lock_refuses_without_checking_liveness(self):
        self.start('PROJ-1')
        self._write_lock('PROJ-1', host='some-other-machine')
        # process_alive is never consulted for a foreign host -- lock_state()
        # returns 'foreign' before it would be called.
        with patch.object(tasks, 'process_alive', side_effect=AssertionError('must not be called')):
            with self.assertRaises(SystemExit) as caught:
                tasks.cmd_close(namespace(id='PROJ-1'))
        self.assertIn('still running', str(caught.exception))
        meta = tasks.read_task(self.state, self.repo, 'PROJ-1')
        self.assertEqual(meta['status'], 'active')

    def test_an_already_closed_task_prints_and_writes_nothing(self):
        self.start('PROJ-1')
        tasks.cmd_close(namespace(id='PROJ-1'))
        path = tasks.task_dir(self.state, self.repo, 'PROJ-1') / 'task.json'
        before = path.read_bytes()
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tasks.cmd_close(namespace(id='PROJ-1'))
        self.assertIn('already closed', buf.getvalue())
        self.assertEqual(path.read_bytes(), before)


class CurrentCommandTests(_TaskSandbox):
    def _run_current(self, as_json=False):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tasks.cmd_current(namespace(json=as_json))
        return buf.getvalue()

    def test_no_active_task_text_and_json(self):
        out = self._run_current()
        self.assertIn('No active task', out)
        self.assertIn('ai start', out)
        payload = json.loads(self._run_current(as_json=True))
        self.assertIsNone(payload['active'])
        self.assertIn('hint', payload)

    def test_active_task_with_plan_gates_readiness_and_budget_overrun(self):
        self.start('PROJ-1', title='widget')
        directory = self.directory('PROJ-1')
        (directory / 'state').mkdir(parents=True, exist_ok=True)
        (directory / 'gates').mkdir(parents=True, exist_ok=True)
        (directory / 'state/current-plan.json').write_text(json.dumps(
            {'profile': 'fast', 'risk': {'risk': 'LOW'}, 'task_type': 'feature',
             'scope': {'base': 'HEAD'}}))
        (directory / 'state/readiness.json').write_text(json.dumps({'status': 'ready'}))
        (directory / 'gates/checks.json').write_text(json.dumps({'passed': True}))
        (directory / 'gates/regression.json').write_text(json.dumps({'passed': False}))
        (directory / 'state/pipeline-run.json').write_text(json.dumps(
            {'status': 'BUDGET_EXCEEDED', 'overrun_gate': 'cleanup', 'remaining': ['summary', 'contract']}))

        out = self._run_current()
        self.assertIn('(crossed at cleanup)', out)
        self.assertIn('not run:', out)
        self.assertIn('checks=PASS', out)
        self.assertIn('regression=FAIL', out)

        payload = json.loads(self._run_current(as_json=True))
        for key in ('id', 'status', 'title', 'base', 'profile', 'risk', 'task_type', 'directory',
                    'contract', 'readiness', 'gates', 'last_run', 'overrun_gate', 'running'):
            self.assertIn(key, payload)
        self.assertEqual(payload['id'], 'PROJ-1')
        self.assertEqual(payload['title'], 'widget')
        self.assertEqual(payload['readiness'], 'ready')
        self.assertEqual(payload['last_run'], 'BUDGET_EXCEEDED')
        self.assertEqual(payload['overrun_gate'], 'cleanup')
        self.assertEqual(payload['gates'], {'checks': True, 'regression': False})
        self.assertFalse(payload['running'])

    def test_running_reflects_a_live_lock_and_not_a_stale_one(self):
        self.start('PROJ-1')
        lock = self.directory('PROJ-1') / tasks.LOCK_NAME
        lock.write_text(json.dumps({'pid': 999999, 'host': socket.gethostname(),
                                    'command': 'ai pipeline', 'started_at': time.time()}))
        with patch.object(tasks, 'process_alive', return_value=True):
            out = self._run_current()
            self.assertIn('RUNNING:', out)
            self.assertTrue(json.loads(self._run_current(as_json=True))['running'])
        with patch.object(tasks, 'process_alive', return_value=False):
            out = self._run_current()
            self.assertNotIn('RUNNING:', out)
            self.assertFalse(json.loads(self._run_current(as_json=True))['running'])


class TasksCommandTests(_TaskSandbox):
    def _run_tasks(self, **fields):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tasks.cmd_tasks(namespace(**fields))
        return buf.getvalue()

    def test_empty_listing(self):
        self.assertIn('No tasks recorded', self._run_tasks())
        self.assertEqual(json.loads(self._run_tasks(json=True)), [])

    def test_status_filter_json_and_active_marker(self):
        self.start('PROJ-1', title='first')
        self.start('PROJ-2', title='second', switch=True)
        rows = json.loads(self._run_tasks(json=True))
        self.assertEqual({r['id'] for r in rows}, {'PROJ-1', 'PROJ-2'})
        paused = json.loads(self._run_tasks(json=True, status='paused'))
        self.assertEqual([r['id'] for r in paused], ['PROJ-1'])

        text = self._run_tasks()
        lines = {line.strip() for line in text.splitlines()}
        self.assertTrue(any(line.startswith('* ') and 'PROJ-2' in line for line in lines))
        self.assertTrue(any(line.startswith('paused') and 'PROJ-1' in line for line in lines))


class ProcessAliveRealTests(unittest.TestCase):
    """process_alive() against real pids -- no mocking of os.kill."""

    def test_the_current_process_is_alive(self):
        self.assertTrue(tasks.process_alive(os.getpid()))

    def test_an_exited_process_is_not_alive(self):
        proc = subprocess.Popen(['true'])
        proc.wait()
        self.assertFalse(tasks.process_alive(proc.pid))


if __name__ == '__main__':
    unittest.main()
