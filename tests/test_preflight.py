"""`ai preflight`: environment checks that run before anything spends tokens."""
import argparse
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
sys.path.insert(0, str(ROOT / 'tests'))
import core  # noqa: E402
import gates  # noqa: E402
import lifecycle  # noqa: E402
import preflight  # noqa: E402
from test_gates import _sandbox  # noqa: E402


def _set(command, timeout=60):
    with contextlib.redirect_stdout(io.StringIO()):
        preflight.cmd_preflight(argparse.Namespace(action='set', command=['--', *command], timeout=timeout))


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _sandbox(self)

    def test_nothing_configured_passes(self):
        self.assertIsNone(preflight.preflight_failure(self.root, self.state))

    def test_a_failing_check_names_the_command_and_its_output(self):
        _set(['sh', '-c', 'echo "Cannot connect to the Docker daemon" >&2; exit 1'])
        failure = preflight.preflight_failure(self.root, self.state)
        self.assertTrue(failure.startswith('NEEDS_HUMAN: environment not ready'))
        self.assertIn('exited 1', failure)
        self.assertIn('Cannot connect to the Docker daemon', failure)

    def test_a_passing_check_passes_and_set_replaces_a_duplicate(self):
        _set(['true']); _set(['true'])
        self.assertEqual(len(preflight.load_preflight(self.state)), 1)
        self.assertIsNone(preflight.preflight_failure(self.root, self.state))

    def test_a_missing_binary_or_a_timeout_fails_closed(self):
        _set(['definitely-not-a-binary-xyz'])
        self.assertIn('could not run', preflight.preflight_failure(self.root, self.state))
        with contextlib.redirect_stdout(io.StringIO()):
            preflight.cmd_preflight(argparse.Namespace(action='clear'))
        _set(['sleep', '5'], timeout=1)
        self.assertIn('timed out after 1s', preflight.preflight_failure(self.root, self.state))

    def test_configuring_is_human_only(self):
        with mock.patch.dict('os.environ', {'AI_GATE': 'review'}), self.assertRaises(SystemExit):
            _set(['true'])

    def test_probes_are_suggested_from_the_checks(self):
        validators = {'regression': {'command': ['sh', '-c', 'pnpm test:run && supabase test db']},
                      'checks': {'command': ['docker', 'compose', 'run', 'lint']}}
        self.assertEqual(preflight.suggestions(validators), [['supabase', 'status'], ['docker', 'info']])
        self.assertEqual(preflight.suggestions({'checks': {'command': ['pytest']}}), [])

    def test_the_pipeline_stops_before_any_gate(self):
        lifecycle.build_prompt(self.root, self.state, 'small change', 'standard', 'HEAD', None)
        contract = core.task_state(self.state) / 'contracts/current-pr.yml'
        contract.write_text(contract.read_text().replace('acceptance: []', 'acceptance: ["app.txt says hello"]'))
        _set(['false'])
        with mock.patch.object(gates, 'required_gates', return_value=[]), \
                mock.patch.object(gates, 'validator_config', return_value={'validators': {}}), \
                mock.patch.object(gates, 'base_behind_upstream', return_value=None), \
                mock.patch.object(gates, 'cmd_gate') as gate, self.assertRaises(SystemExit) as ctx:
            gates.cmd_pipeline(argparse.Namespace(dry_run=False, resume=True, allow_overrun=False, force_unlock=False))
        self.assertIn('environment not ready', str(ctx.exception))
        gate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
