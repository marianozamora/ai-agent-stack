"""`ai loop`: unattended builder rounds and gates until PR_READY or a human decision."""
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
import loop  # noqa: E402
import providers  # noqa: E402
import tasks  # noqa: E402


class FakeBuilder:
    name = 'claude'

    def __init__(self, ok=True):
        self.ok = ok
        self.prompts = []

    def run_headless(self, prompt, root, env, model=None, effort=None, tools=None, timeout=None):
        self.prompts.append((prompt, tools))
        (Path(root) / 'app.py').write_text(f'value = {len(self.prompts)}\n')
        return {'ok': self.ok, 'result': 'done', 'cost_usd': 0.5, 'turns': 3, 'denied': ['make test']}


class LoopTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='loop-test-')
        self.addCleanup(temp.cleanup)
        home = Path(temp.name)
        self.repo = home / 'repo'
        self.repo.mkdir()
        for argv in [('init', '-q', '-b', 'main'), ('config', 'user.name', 'T'), ('config', 'user.email', 't@e.com')]:
            subprocess.check_call(['git', *argv], cwd=self.repo, stdout=subprocess.DEVNULL)
        (self.repo / 'app.py').write_text('value = 0\n')
        subprocess.check_call(['git', 'add', '.'], cwd=self.repo, stdout=subprocess.DEVNULL)
        subprocess.check_call(['git', 'commit', '-qm', 'init'], cwd=self.repo, stdout=subprocess.DEVNULL)
        previous = os.getcwd(); os.chdir(self.repo); self.addCleanup(os.chdir, previous)
        for name in ('AI_TASK_ID', 'AI_GATE', 'AI_TASK_DIR'):
            os.environ.pop(name, None)
        core.TASK_ID = None
        patcher = patch.object(core, 'CONFIG_ROOT', home / 'config'); patcher.start(); self.addCleanup(patcher.stop)
        self.state = core.repo_state(self.repo)
        with contextlib.redirect_stdout(io.StringIO()):
            tasks.cmd_start(argparse.Namespace(id='T-1', title='small change', ticket_file=None, base='main',
                                               resume=False, switch=False, no_baseline=True))
        self.task = core.task_state(self.state)

    def _pipeline(self, outcomes):
        """A cmd_pipeline stand-in replaying (status, exit) per call."""
        calls = iter(outcomes)

        def fake(args):
            status, exit_value = next(calls)
            core.save_json(self.task / 'state/pipeline-run.json', {'status': status})
            if exit_value is not None:
                raise SystemExit(exit_value)
        return fake

    def _loop(self, builder, outcomes, max_rounds=4):
        args = argparse.Namespace(profile='fast', max_rounds=max_rounds, note=None, allow=None, timeout=60)
        out = io.StringIO()
        with patch('providers.builder', return_value=builder), \
                patch('gates.cmd_pipeline', side_effect=self._pipeline(outcomes)), contextlib.redirect_stdout(out):
            try:
                loop.cmd_loop(args)
                return out.getvalue(), None
            except SystemExit as exc:
                return out.getvalue(), str(exc)

    def rows(self, event):
        return [json.loads(line) for line in (self.state / 'metrics.jsonl').read_text().splitlines()
                if json.loads(line).get('event') == event]

    def test_rounds_continue_on_a_failed_gate_and_stop_at_pr_ready(self):
        builder = FakeBuilder()
        out, stopped = self._loop(builder, [('FAILED', 1), ('PR_READY', None)])
        self.assertIsNone(stopped)
        self.assertIn('PR_READY after 2 round(s).', out)
        self.assertEqual(len(builder.prompts), 2)
        self.assertIn(loop.LOOP_SUFFIX, builder.prompts[0][0])
        self.assertEqual([r['round'] for r in self.rows('builder_round')], [1, 2])
        self.assertIn('denied: make test', out)

    def test_a_human_decision_stops_the_loop(self):
        builder = FakeBuilder()
        _, stopped = self._loop(builder, [('FAILED', 'NEEDS_HUMAN: review has returned FAIL 4 times')])
        self.assertIn('NEEDS_HUMAN: review has returned FAIL 4 times', stopped)
        self.assertEqual(len(builder.prompts), 1)

    def test_a_budget_stop_is_not_retried(self):
        _, stopped = self._loop(FakeBuilder(), [('BUDGET_EXCEEDED', 'BUDGET_EXCEEDED: crossed')])
        self.assertIn('NEEDS_HUMAN: pipeline stopped (BUDGET_EXCEEDED)', stopped)

    def test_it_stops_after_max_rounds(self):
        builder = FakeBuilder()
        _, stopped = self._loop(builder, [('FAILED', 1), ('FAILED', 1)], max_rounds=2)
        self.assertIn('not PR_READY after 2 round(s)', stopped)
        self.assertEqual(len(builder.prompts), 2)

    def test_a_failed_builder_session_stops_before_the_gates(self):
        _, stopped = self._loop(FakeBuilder(ok=False), [])
        self.assertIn('the builder session did not finish', stopped)

    def test_the_operator_note_and_extra_tools_reach_the_builder(self):
        builder = FakeBuilder()
        args = argparse.Namespace(profile='fast', max_rounds=1, note='drop PDF uploads', allow=['Bash(make:*)'], timeout=60)
        with patch('providers.builder', return_value=builder), \
                patch('gates.cmd_pipeline', side_effect=self._pipeline([('PR_READY', None)])), \
                contextlib.redirect_stdout(io.StringIO()):
            loop.cmd_loop(args)
        prompt, tools = builder.prompts[0]
        self.assertIn('Operator direction: drop PDF uploads', prompt)
        self.assertIn('Bash(make:*)', tools)

    def test_the_operator_note_persists_for_the_gates_until_cleared(self):
        import gates
        self._loop(FakeBuilder(), [('PR_READY', None)])
        self.assertEqual(gates.operator_note(self.task), '')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(loop.save_operator_note(self.task, 'fallback is out of scope'), 'fallback is out of scope')
            # A later run without --note keeps the decision; the gates read the same file.
            self.assertEqual(loop.save_operator_note(self.task, None), 'fallback is out of scope')
        self.assertEqual(gates.operator_note(self.task), 'fallback is out of scope')
        loop.save_operator_note(self.task, '')
        self.assertEqual(gates.operator_note(self.task), '')

    def test_the_operator_note_changes_the_gate_evidence(self):
        import gates
        self._loop(FakeBuilder(), [('PR_READY', None)])
        plan = gates.current_plan(self.state)
        before = gates.evidence_fingerprint(self.repo, self.state, plan)
        loop.save_operator_note(self.task, 'fallback is out of scope')
        self.assertNotEqual(before, gates.evidence_fingerprint(self.repo, self.state, plan))

    def test_the_contract_is_inlined_for_the_headless_builder(self):
        builder = FakeBuilder()
        self._loop(builder, [('PR_READY', None)])
        self.assertIn('PR contract (its file is outside the checkout):', builder.prompts[0][0])
        self.assertIn('objective:', builder.prompts[0][0])


class CodexErrorTests(unittest.TestCase):
    def test_the_nested_api_error_is_surfaced(self):
        events = '\n'.join([
            json.dumps({'type': 'thread.started'}),
            json.dumps({'type': 'error', 'message': json.dumps({'type': 'error', 'status': 400, 'error': {
                'message': "The 'gpt-x' model is not supported when using Codex with a ChatGPT account."}})}),
            json.dumps({'type': 'turn.failed', 'error': {'message': json.dumps({'error': {
                'message': "The 'gpt-x' model is not supported when using Codex with a ChatGPT account."}})}}),
        ])
        self.assertEqual(providers.codex_error(events),
                         "The 'gpt-x' model is not supported when using Codex with a ChatGPT account.")
        self.assertEqual(providers.codex_error('not json\n{}'), '')


class BuilderToolsTests(unittest.TestCase):
    def test_programs_from_the_checks_become_allowlist_entries(self):
        with tempfile.TemporaryDirectory() as state:
            validators = {
                'checks': {'command': ['sh', '-c', 'pnpm run lint && pnpm exec tsc --noEmit']},
                'regression': {'command': ['sh', '-c', 'pnpm test:run && if ls supabase/tests/*.sql >/dev/null 2>&1; '
                                                        'then supabase db reset --local && supabase test db; fi']}}
            tools = providers.builder_tools(Path(state), validators, ['rtk'])
        extra = tools[len(providers.BASE_BUILDER_TOOLS):]
        self.assertEqual(extra, ['Bash(pnpm:*)', 'Bash(supabase db:*)', 'Bash(supabase test:*)', 'Bash(rtk:*)'])
        self.assertNotIn('Bash(git push:*)', tools)


class RunHeadlessTests(unittest.TestCase):
    def test_argv_and_report_parsing(self):
        report = {'result': 'ok', 'total_cost_usd': 1.25, 'num_turns': 7, 'is_error': False,
                  'permission_denials': [{'tool_name': 'Bash', 'tool_input': {'command': 'make test'}}]}
        done = subprocess.CompletedProcess([], 0, stdout=json.dumps(report) + '\n', stderr='')
        with patch.object(providers.shutil, 'which', return_value='/bin/claude'), \
                patch.object(providers.subprocess, 'run', return_value=done) as run:
            result = providers.ClaudeBuilder().run_headless('p', Path('/repo'), {}, model='sonnet', effort='high',
                                                             tools=['Read'], timeout=5)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:3], ['/bin/claude', '-p', 'p'])
        self.assertIn('acceptEdits', argv)
        self.assertEqual(argv[-2:], ['--allowedTools', 'Read'])
        self.assertEqual((result['ok'], result['cost_usd'], result['turns'], result['denied']),
                         (True, 1.25, 7, ['make test']))


if __name__ == '__main__':
    unittest.main()
