import argparse, contextlib, io, sys, unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import tools  # noqa: E402


class Ctx7CmdTests(unittest.TestCase):
    def test_missing_binary_returns_none(self):
        with patch('tools.shutil.which', return_value=None):
            self.assertIsNone(tools.ctx7_cmd())

    def test_present_binary_returns_argv_list(self):
        with patch('tools.shutil.which', return_value='/usr/local/bin/ctx7'):
            self.assertEqual(tools.ctx7_cmd(), ['ctx7'])


class GraphEnvTests(unittest.TestCase):
    def test_graph_env_sets_output_dir_and_keeps_ambient(self):
        state = Path('/tmp/graph-state')
        env = tools.graph_env(state)
        self.assertEqual(env['GRAPHIFY_OUT'], str(state / 'graphify'))
        self.assertIn('PATH', env)  # ambient environment preserved


class CmdFigmaDoctorTests(unittest.TestCase):
    def test_doctor_reports_missing_clients_without_touching_git_state(self):
        # doctor branch only shells out via shutil.which; both faked absent
        buf = io.StringIO()
        with patch('tools.shutil.which', return_value=None), contextlib.redirect_stdout(buf):
            tools.cmd_figma(argparse.Namespace(figma_cmd='doctor'))
        out = buf.getvalue()
        self.assertIn('claude missing', out)
        self.assertIn('codex missing', out)
        self.assertIn('Recommended remote:', out)


if __name__ == '__main__':
    unittest.main()
