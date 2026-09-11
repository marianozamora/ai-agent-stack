import argparse, contextlib, io, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import core  # noqa: E402
import tools  # noqa: E402


def _sandbox(tc):
    """Isolated git repo + redirected external state, mirroring tests/test_crg.py::_sandbox."""
    tmp = tempfile.TemporaryDirectory(prefix='tools-test-')
    tc.addCleanup(tmp.cleanup)
    home = Path(tmp.name)
    repo = home / 'repo'
    repo.mkdir()
    cfg = home / 'config' / 'ai-agent-stack'

    env = {k: v for k, v in os.environ.items() if k not in ('AI_GATE', 'AI_TASK_DIR')}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'), AI_TASK_ID='tools-task')

    for args in (('init', '-q'), ('config', 'user.name', 'T'), ('config', 'user.email', 't@e.com')):
        subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True)
    (repo / 'app.txt').write_text('initial\n')
    subprocess.run(['git', 'add', '.'], cwd=repo, check=True, capture_output=True)
    subprocess.run(['git', 'commit', '-qm', 'init'], cwd=repo, check=True, capture_output=True)

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


class CmdDocsLibraryErrorTests(unittest.TestCase):
    """C2: malformed/unreadable ctx7 `library` output must not fail silently."""

    def setUp(self):
        self.root, self.state = _sandbox(self)

    def _args(self, name='some-lib', query=''):
        return argparse.Namespace(docs_cmd='library', name=name, query=query)

    def test_malformed_output_prints_actionable_stderr_and_saves_no_mapping(self):
        with patch('tools.ctx7_cmd', return_value=['ctx7']), \
                patch('tools.run', return_value='not json at all'):
            buf_out, buf_err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                tools.cmd_docs(self._args(name='some-lib'))
        self.assertIn('Could not resolve a Context7 ID for some-lib', buf_err.getvalue())
        self.assertIn('ai docs query', buf_err.getvalue())
        self.assertEqual(core.load_json(self.state / 'context7-libraries.json', {}), {})

    def test_output_without_an_id_also_reports_and_saves_nothing(self):
        with patch('tools.ctx7_cmd', return_value=['ctx7']), \
                patch('tools.run', return_value='[{"name": "some-lib"}]'):
            buf_err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(buf_err):
                tools.cmd_docs(self._args(name='some-lib'))
        self.assertIn('Could not resolve a Context7 ID for some-lib', buf_err.getvalue())
        self.assertEqual(core.load_json(self.state / 'context7-libraries.json', {}), {})

    def test_well_formed_output_saves_the_mapping_without_stderr(self):
        with patch('tools.ctx7_cmd', return_value=['ctx7']), \
                patch('tools.run', return_value='[{"id": "/org/some-lib"}]'):
            buf_err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(buf_err):
                tools.cmd_docs(self._args(name='some-lib'))
        self.assertEqual(buf_err.getvalue(), '')
        mapping = core.load_json(self.state / 'context7-libraries.json', {})
        self.assertEqual(mapping['some-lib']['id'], '/org/some-lib')


if __name__ == '__main__':
    unittest.main()
