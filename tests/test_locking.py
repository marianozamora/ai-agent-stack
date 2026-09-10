"""Per-task pipeline locking and the explicit budget-overrun status."""
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import gates  # noqa: E402
import tasks  # noqa: E402
import workflow  # noqa: E402


class LockStateTests(unittest.TestCase):
    def test_our_own_live_lock_is_recognised_as_ours(self):
        held = {'pid': os.getpid(), 'host': socket.gethostname()}
        self.assertEqual(tasks.lock_state(held), 'ours')

    def test_a_dead_pid_on_this_host_is_stale(self):
        held = {'pid': 999999, 'host': socket.gethostname()}
        with patch.object(tasks, 'process_alive', return_value=False):
            self.assertEqual(tasks.lock_state(held), 'stale')

    def test_a_live_pid_on_this_host_is_held(self):
        held = {'pid': 999999, 'host': socket.gethostname()}
        with patch.object(tasks, 'process_alive', return_value=True):
            self.assertEqual(tasks.lock_state(held), 'held')

    def test_another_host_is_never_assumed_dead(self):
        # A pid from another machine says nothing about a process here; assuming it
        # dead would let a shared external state directory silently double-run.
        held = {'pid': os.getpid(), 'host': 'some-other-machine'}
        self.assertEqual(tasks.lock_state(held), 'foreign')

    def test_a_corrupt_lock_file_is_stale_not_a_crash(self):
        self.assertEqual(tasks.lock_state({}), 'stale')
        self.assertEqual(tasks.lock_state({'pid': 'not-a-pid'}), 'stale')


class TaskLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lock-test-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / tasks.LOCK_NAME

    def test_acquire_and_release(self):
        with tasks.task_lock(self.directory, 'ai pipeline') as acquired:
            self.assertTrue(acquired)
            self.assertTrue(self.path.exists())
            self.assertEqual(json.loads(self.path.read_text())['pid'], os.getpid())
        self.assertFalse(self.path.exists())

    def test_reentrant_acquisition_does_not_release_the_outer_lock(self):
        # `ai pipeline` holds the lock and calls `ai gate` in-process; the inner
        # exit must not drop the outer lock and let a rival in mid-run.
        with tasks.task_lock(self.directory, 'ai pipeline'):
            with tasks.task_lock(self.directory, 'ai gate checks') as inner:
                self.assertFalse(inner)
            self.assertTrue(self.path.exists())
        self.assertFalse(self.path.exists())

    def test_a_held_lock_refuses_with_the_owning_command(self):
        self.path.write_text(json.dumps({'pid': 4242, 'host': socket.gethostname(),
                                         'command': 'ai pipeline', 'started_at': 0}))
        with patch.object(tasks, 'process_alive', return_value=True):
            with self.assertRaises(SystemExit) as caught:
                with tasks.task_lock(self.directory, 'ai gate checks'):
                    pass
        message = str(caught.exception)
        self.assertIn('ai pipeline is already running', message)
        self.assertIn('4242', message)
        self.assertTrue(self.path.exists(), 'a refused acquisition must not delete the lock')

    def test_a_stale_lock_is_reclaimed(self):
        self.path.write_text(json.dumps({'pid': 999999, 'host': socket.gethostname(),
                                         'command': 'ai pipeline', 'started_at': 0}))
        with patch.object(tasks, 'process_alive', return_value=False):
            with tasks.task_lock(self.directory, 'ai pipeline') as acquired:
                self.assertTrue(acquired)
                self.assertEqual(json.loads(self.path.read_text())['pid'], os.getpid())

    def test_a_foreign_lock_needs_force(self):
        held = json.dumps({'pid': 4242, 'host': 'some-other-machine',
                           'command': 'ai pipeline', 'started_at': 0})
        self.path.write_text(held)
        with self.assertRaises(SystemExit):
            with tasks.task_lock(self.directory, 'ai pipeline'):
                pass
        self.path.write_text(held)
        with tasks.task_lock(self.directory, 'ai pipeline', force=True) as acquired:
            self.assertTrue(acquired)

    def test_the_lock_is_released_when_the_body_raises(self):
        with self.assertRaises(RuntimeError):
            with tasks.task_lock(self.directory, 'ai pipeline'):
                raise RuntimeError('gate blew up')
        self.assertFalse(self.path.exists())


class BudgetStatusTests(unittest.TestCase):
    def test_the_message_names_the_crossing_gate_and_what_never_ran(self):
        required = ['cleanup', 'checks', 'contract', 'ponytail', 'summary']
        message = gates.budget_message(
            {'gate': 'contract', 'tokens': 60000, 'dimension': 'tokens', 'spent': 60000, 'budget': 40000},
            required, 'ponytail')
        self.assertIn('crossed at:  contract (60000 >= 40000', message)
        self.assertIn('not run:     ponytail, summary', message)
        self.assertIn('--allow-overrun', message)
        self.assertNotIn('cleanup', message.split('not run:')[1])

    def test_the_message_reports_a_cost_overrun_in_dollars(self):
        message = gates.budget_message(
            {'gate': 'review', 'tokens': 9000, 'dimension': 'cost_usd', 'spent': 1.7321, 'budget': 1.5},
            ['review', 'summary'], 'summary')
        self.assertIn('crossed at:  review ($1.7321 >= $1.50 reported cost)', message)
        self.assertIn('not run:     summary', message)

    def test_an_explicit_budget_status_is_counted(self):
        rows = [{'event': 'pipeline', 'status': 'BUDGET_EXCEEDED',
                 'usage': {'input_tokens': 1, 'output_tokens': 1}, 'usage_budget': 40000}]
        self.assertEqual(workflow.summarize(rows)['pipeline_budget_exceeded'], 1)

    def test_historical_rows_without_the_status_are_still_counted(self):
        # metrics.jsonl is an append-only audit source, so rows written before
        # BUDGET_EXCEEDED existed must keep being reported as overruns.
        rows = [{'event': 'pipeline', 'status': 'FAILED',
                 'usage': {'input_tokens': 30000, 'output_tokens': 20000}, 'usage_budget': 40000}]
        self.assertEqual(workflow.summarize(rows)['pipeline_budget_exceeded'], 1)

    def test_a_run_that_finished_with_allow_overrun_is_not_an_overrun(self):
        rows = [{'event': 'pipeline', 'status': 'PR_READY',
                 'usage': {'input_tokens': 90000, 'output_tokens': 20000}, 'usage_budget': 40000}]
        self.assertEqual(workflow.summarize(rows)['pipeline_budget_exceeded'], 0)

    def test_an_ordinary_failure_under_budget_is_not_an_overrun(self):
        rows = [{'event': 'pipeline', 'status': 'FAILED',
                 'usage': {'input_tokens': 10, 'output_tokens': 10}, 'usage_budget': 40000}]
        self.assertEqual(workflow.summarize(rows)['pipeline_budget_exceeded'], 0)


if __name__ == '__main__':
    unittest.main()
