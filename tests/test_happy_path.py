"""`ai work` / `ai finish`, and the task-aware base default they rely on.

Both commands are compositions over cmd_planrun and cmd_pipeline, so what is tested
here is the wiring: which task they act on, which base they inherit, and how they
refuse when a precondition is missing.
"""
import argparse
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
                    switch=False, reason='', status=None, json=False, task='',
                    profile='standard', figma=None, no_figma=True, skill=None,
                    plan_only=True, no_resume=False)
    return argparse.Namespace(**{**defaults, **fields})


class HappyPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='happy-test-')
        self.addCleanup(self.temp.cleanup)
        home = Path(self.temp.name)
        self.repo = home / 'repo'
        self.repo.mkdir()
        for argv in [('init', '-q', '-b', 'main'), ('config', 'user.name', 'Test'),
                     ('config', 'user.email', 'test@example.com')]:
            subprocess.check_call(['git', *argv], cwd=self.repo,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (self.repo / 'app.py').write_text('value = 1\n')
        subprocess.check_call(['git', 'add', '.'], cwd=self.repo, stdout=subprocess.DEVNULL)
        subprocess.check_call(['git', 'commit', '-qm', 'initial'], cwd=self.repo,
                              stdout=subprocess.DEVNULL)
        subprocess.check_call(['git', 'branch', 'release'], cwd=self.repo)

        self.previous = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, self.previous)
        for name in ('AI_TASK_ID', 'AI_GATE', 'AI_TASK_DIR'):
            os.environ.pop(name, None)
        core.TASK_ID = None
        patcher = patch.object(core, 'CONFIG_ROOT', home / 'config')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state = core.repo_state(self.repo)

    # --- base precedence ---------------------------------------------------

    def test_the_active_tasks_base_outranks_the_repository_default(self):
        (self.state / 'repo.json').write_text('{"default_base": "main"}')
        tasks.cmd_start(namespace(id='PROJ-1', base='release'))
        self.assertEqual(core.resolve_base(self.repo, None, self.state), 'release')

    def test_an_explicit_base_still_outranks_the_task(self):
        tasks.cmd_start(namespace(id='PROJ-1', base='release'))
        self.assertEqual(core.resolve_base(self.repo, 'main', self.state), 'main')

    def test_a_tasks_base_that_disappeared_fails_closed_naming_the_task(self):
        tasks.cmd_start(namespace(id='PROJ-1', base='release'))
        subprocess.check_call(['git', 'branch', '-D', 'release'], cwd=self.repo,
                              stdout=subprocess.DEVNULL)
        with self.assertRaises(SystemExit) as caught:
            core.resolve_base(self.repo, None, self.state)
        self.assertIn('the active task', str(caught.exception))

    def test_the_repository_default_still_applies_with_no_active_task(self):
        (self.state / 'repo.json').write_text('{"default_base": "release"}')
        self.assertEqual(core.resolve_base(self.repo, None, self.state), 'release')

    # --- ai work -----------------------------------------------------------

    def test_work_without_an_active_task_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            tasks.cmd_work(namespace())
        self.assertIn('No active task', str(caught.exception))

    def test_work_uses_the_tasks_title_as_the_objective(self):
        tasks.cmd_start(namespace(id='PROJ-1', title='add email sign-in'))
        with patch('lifecycle.cmd_planrun') as planrun:
            tasks.cmd_work(namespace())
        built = planrun.call_args.args[0]
        self.assertEqual(built.task, 'add email sign-in')
        self.assertFalse(planrun.call_args.kwargs['launch'])

    def test_work_falls_back_to_the_task_id_when_there_is_no_title(self):
        tasks.cmd_start(namespace(id='PROJ-1'))
        with patch('lifecycle.cmd_planrun') as planrun:
            tasks.cmd_work(namespace())
        self.assertEqual(planrun.call_args.args[0].task, 'PROJ-1')

    def test_an_explicit_objective_overrides_the_title(self):
        tasks.cmd_start(namespace(id='PROJ-1', title='stored title'))
        with patch('lifecycle.cmd_planrun') as planrun:
            tasks.cmd_work(namespace(task='something else'))
        self.assertEqual(planrun.call_args.args[0].task, 'something else')

    def test_work_launches_the_builder_unless_plan_only(self):
        tasks.cmd_start(namespace(id='PROJ-1'))
        with patch('lifecycle.cmd_planrun') as planrun:
            tasks.cmd_work(namespace(plan_only=False))
        self.assertTrue(planrun.call_args.kwargs['launch'])

    def test_work_after_closing_reports_no_active_task(self):
        # Closing clears the pointer, so this is the message the operator actually sees.
        tasks.cmd_start(namespace(id='PROJ-1'))
        tasks.cmd_close(namespace(id='PROJ-1'))
        with self.assertRaises(SystemExit) as caught:
            tasks.cmd_work(namespace())
        self.assertIn('No active task', str(caught.exception))

    def test_work_is_refused_when_task_id_names_a_closed_task(self):
        # The pointer stays on PROJ-2, so `--task-id PROJ-1` is the path that reaches
        # the closed-task guard rather than the no-active-task check.
        tasks.cmd_start(namespace(id='PROJ-1'))
        tasks.cmd_start(namespace(id='PROJ-2', switch=True))
        tasks.cmd_close(namespace(id='PROJ-1'))
        core.TASK_ID = 'PROJ-1'
        self.addCleanup(setattr, core, 'TASK_ID', None)
        with self.assertRaises(SystemExit) as caught:
            tasks.cmd_work(namespace())
        self.assertIn('is closed', str(caught.exception))

    # --- ai finish ---------------------------------------------------------

    def test_finish_without_a_plan_is_refused(self):
        tasks.cmd_start(namespace(id='PROJ-1'))
        with self.assertRaises(SystemExit) as caught:
            tasks.cmd_finish(namespace())
        self.assertIn('no plan yet', str(caught.exception))

    def test_finish_names_the_command_that_configures_the_missing_gates(self):
        tasks.cmd_start(namespace(id='PROJ-1'))
        task = core.task_state(self.state)
        core.save_json(task / 'state/current-plan.json',
                       {'task': 'x', 'profile': 'standard', 'scope': {'base': 'main'},
                        'risk': {'risk': 'LOW', 'security': False}, 'caps': {}, 'figma': None})
        with self.assertRaises(SystemExit), patch('sys.stdout') as out:
            tasks.cmd_finish(namespace())
        printed = ' '.join(str(call) for call in out.write.call_args_list)
        self.assertIn('ai validators propose --apply', printed)
        self.assertIn('ai validators install', printed)

    def test_finish_runs_the_pipeline_with_resume_by_default(self):
        tasks.cmd_start(namespace(id='PROJ-1'))
        task = core.task_state(self.state)
        core.save_json(task / 'state/current-plan.json',
                       {'task': 'x', 'profile': 'standard', 'scope': {'base': 'main'},
                        'risk': {'risk': 'LOW', 'security': False}, 'caps': {}, 'figma': None})
        with patch('gates.validator_config') as config, patch('gates.cmd_pipeline') as pipeline, \
                patch('core.required_gates', return_value=['checks']):
            config.return_value = {'validators': {'checks': {}}}
            tasks.cmd_finish(namespace())
        self.assertTrue(pipeline.call_args.args[0].resume)
        self.assertFalse(pipeline.call_args.args[0].dry_run)


if __name__ == '__main__':
    unittest.main()
