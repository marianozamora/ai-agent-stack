import argparse, contextlib, io, json, os, subprocess, sys, tempfile, unittest
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

    def test_doctor_reports_ready_when_both_clients_list_figma(self):
        with patch('tools.shutil.which', return_value='/usr/bin/x'), \
                patch('tools.run', return_value='some-figma-mcp entry'):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                tools.cmd_figma(argparse.Namespace(figma_cmd='doctor'))
        out = buf.getvalue()
        self.assertIn('Claude Figma MCP: ready', out)
        self.assertIn('Codex Figma MCP:  ready', out)


class CmdFigmaSetupTests(unittest.TestCase):
    def test_claude_only_skips_codex_even_if_present(self):
        calls = []
        with patch('tools.shutil.which', return_value='/usr/bin/x'), \
                patch('tools.subprocess.run', side_effect=lambda *a, **k: calls.append(a[0])):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                tools.cmd_figma(argparse.Namespace(figma_cmd='setup', claude_only=True, codex_only=False))
        self.assertEqual(len(calls), 1)
        self.assertIn('claude', calls[0])
        self.assertIn('OAuth', buf.getvalue())

    def test_codex_only_skips_claude_even_if_present(self):
        calls = []
        with patch('tools.shutil.which', return_value='/usr/bin/x'), \
                patch('tools.subprocess.run', side_effect=lambda *a, **k: calls.append(a[0])):
            tools.cmd_figma(argparse.Namespace(figma_cmd='setup', claude_only=False, codex_only=True))
        self.assertEqual(len(calls), 1)
        self.assertIn('codex', calls[0])

    def test_neither_client_available_raises(self):
        with patch('tools.shutil.which', return_value=None):
            with self.assertRaises(SystemExit) as caught:
                tools.cmd_figma(argparse.Namespace(figma_cmd='setup', claude_only=False, codex_only=False))
        self.assertIn('Neither Claude nor Codex', str(caught.exception))


class CmdDocsDoctorTests(unittest.TestCase):
    def test_ready_prints_version_and_whoami(self):
        def fake_run(cmd, check=True, **k):
            if cmd[-1] == '--version': return '1.2.3'
            if cmd[-1] == 'whoami': return 'me@example.com'
            return ''
        with patch('tools.shutil.which', return_value='/usr/local/bin/ctx7'), \
                patch('tools.run', side_effect=fake_run):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                tools.cmd_docs(argparse.Namespace(docs_cmd='doctor'))
        out = buf.getvalue()
        self.assertIn('Context7: ready', out)
        self.assertIn('1.2.3', out)
        self.assertIn('me@example.com', out)

    def test_missing_reports_install_hint_without_calling_run(self):
        with patch('tools.shutil.which', return_value=None), \
                patch('tools.run') as run_mock:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                tools.cmd_docs(argparse.Namespace(docs_cmd='doctor'))
        run_mock.assert_not_called()
        self.assertIn('missing (install: npm install -g ctx7)', buf.getvalue())


class CmdDocsSetupTests(unittest.TestCase):
    def setUp(self):
        # cmd_docs() calls git_root()+repo_state() unconditionally before branching on
        # docs_cmd, so a bare `patch('tools.subprocess.run', ...)` below -- which patches
        # the process-wide subprocess module, not just tools.py's reference to it -- would
        # also intercept core.run()'s `git rev-parse`/`git remote get-url` calls whenever
        # this repo's cwd/root isn't already cached from an earlier test. _sandbox() gives
        # each test its own repo and warms both caches before any patch is applied.
        self.root, self.state = _sandbox(self)

    def test_missing_ctx7_raises(self):
        with patch('tools.shutil.which', return_value=None):
            with self.assertRaises(SystemExit) as caught:
                tools.cmd_docs(argparse.Namespace(docs_cmd='setup', mcp=False, universal=False))
        self.assertIn('ctx7 CLI missing', str(caught.exception))

    def test_mcp_and_claude_only_argv(self):
        captured = {}
        def fake_run(cmd, **k):
            captured['cmd'] = cmd
            return argparse.Namespace(returncode=0)
        with patch('tools.shutil.which', return_value='/usr/local/bin/ctx7'), \
                patch('tools.subprocess.run', side_effect=fake_run):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                tools.cmd_docs(argparse.Namespace(docs_cmd='setup', mcp=True, universal=False))
        self.assertEqual(captured['cmd'], ['ctx7', 'setup', '--mcp', '--claude', '--yes'])
        self.assertIn('Codex alternative', buf.getvalue())

    def test_universal_and_cli_argv(self):
        captured = {}
        def fake_run(cmd, **k):
            captured['cmd'] = cmd
            return argparse.Namespace(returncode=0)
        with patch('tools.shutil.which', return_value='/usr/local/bin/ctx7'), \
                patch('tools.subprocess.run', side_effect=fake_run):
            tools.cmd_docs(argparse.Namespace(docs_cmd='setup', mcp=False, universal=True))
        self.assertEqual(captured['cmd'], ['ctx7', 'setup', '--cli', '--yes'])

    def test_nonzero_returncode_propagates(self):
        with patch('tools.shutil.which', return_value='/usr/local/bin/ctx7'), \
                patch('tools.subprocess.run', return_value=argparse.Namespace(returncode=7)):
            with self.assertRaises(SystemExit) as caught:
                tools.cmd_docs(argparse.Namespace(docs_cmd='setup', mcp=False, universal=False))
        self.assertEqual(caught.exception.code, 7)


class CmdDocsQueryTests(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _sandbox(self)

    def _args(self, library, query='how to X', refresh=False):
        return argparse.Namespace(docs_cmd='query', library=library, query=query, refresh=refresh)

    def test_unknown_id_raises(self):
        with self.assertRaises(SystemExit) as caught:
            tools.cmd_docs(self._args('unmapped-lib'))
        self.assertIn('Unknown Context7 ID', str(caught.exception))
        self.assertIn('ai docs library unmapped-lib', str(caught.exception))

    def test_mapped_name_resolves_to_stored_id(self):
        core.save_json(self.state / 'context7-libraries.json', {'mylib': {'id': '/org/mylib'}})
        with patch('tools.ctx7_cmd', return_value=['ctx7']), \
                patch('tools.run', return_value='{"docs": "hi"}') as run_mock:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                tools.cmd_docs(self._args('mylib'))
        called_argv = run_mock.call_args[0][0]
        self.assertIn('/org/mylib', called_argv)
        self.assertIn('hi', buf.getvalue())

    def test_cached_result_skips_ctx7(self):
        with patch('tools.ctx7_cmd', return_value=['ctx7']), \
                patch('tools.run', return_value='{"docs": "first"}'):
            with contextlib.redirect_stdout(io.StringIO()):
                tools.cmd_docs(self._args('/org/mylib'))
        with patch('tools.ctx7_cmd', return_value=['ctx7']), \
                patch('tools.run') as run_mock:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                tools.cmd_docs(self._args('/org/mylib'))
        run_mock.assert_not_called()
        self.assertIn('first', buf.getvalue())

    def test_refresh_bypasses_cache(self):
        with patch('tools.ctx7_cmd', return_value=['ctx7']), \
                patch('tools.run', return_value='{"docs": "first"}'):
            with contextlib.redirect_stdout(io.StringIO()):
                tools.cmd_docs(self._args('/org/mylib'))
        with patch('tools.ctx7_cmd', return_value=['ctx7']), \
                patch('tools.run', return_value='{"docs": "second"}') as run_mock:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                tools.cmd_docs(self._args('/org/mylib', refresh=True))
        run_mock.assert_called_once()
        self.assertIn('second', buf.getvalue())

    def test_missing_ctx7_and_no_cache_raises(self):
        with patch('tools.ctx7_cmd', return_value=None):
            with self.assertRaises(SystemExit) as caught:
                tools.cmd_docs(self._args('/org/mylib'))
        self.assertIn('ctx7 CLI missing', str(caught.exception))

    def test_writes_cache_after_a_live_query(self):
        with patch('tools.ctx7_cmd', return_value=['ctx7']), \
                patch('tools.run', return_value='{"docs": "hi"}'):
            with contextlib.redirect_stdout(io.StringIO()):
                tools.cmd_docs(self._args('/org/mylib'))
        cache_files = list((self.state / 'docs-cache').glob('*.json'))
        self.assertEqual(len(cache_files), 1)
        self.assertIn('hi', cache_files[0].read_text())


class CmdDocsDetectTests(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _sandbox(self)

    def test_prints_detected_dependencies_as_json(self):
        core.save_json(self.state / 'project-profile.json', {'dependencies': {'python': ['requests']}})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tools.cmd_docs(argparse.Namespace(docs_cmd='detect'))
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload, {'python': ['requests']})


class GraphDoctorTests(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _sandbox(self)

    def test_ready_and_missing(self):
        with patch('tools.shutil.which', return_value='/usr/local/bin/graphify'):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                tools.cmd_graph(argparse.Namespace(graph_cmd='doctor'))
        self.assertIn('Graphify: ready', buf.getvalue())

        with patch('tools.shutil.which', return_value=None):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                tools.cmd_graph(argparse.Namespace(graph_cmd='doctor'))
        self.assertIn('missing (recommended: uv tool install graphifyy)', buf.getvalue())


class GraphMissingBinaryTests(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _sandbox(self)

    def test_build_raises_when_graphify_missing(self):
        with patch('tools.shutil.which', return_value=None):
            with self.assertRaises(SystemExit) as caught:
                tools.cmd_graph(argparse.Namespace(graph_cmd='build'))
        self.assertIn('graphify missing', str(caught.exception))


class GraphBuildTests(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _sandbox(self)

    def test_build_propagates_rc_with_graphify_out_in_env(self):
        captured = {}
        def fake_run(cmd, cwd=None, env=None):
            captured['cmd'] = cmd; captured['env'] = env
            return argparse.Namespace(returncode=5)
        with patch('tools.shutil.which', return_value='/usr/local/bin/graphify'), \
                patch('tools.subprocess.run', side_effect=fake_run):
            with self.assertRaises(SystemExit) as caught:
                tools.cmd_graph(argparse.Namespace(graph_cmd='build'))
        self.assertEqual(caught.exception.code, 5)
        self.assertEqual(captured['cmd'], ['/usr/local/bin/graphify', str(self.root)])
        self.assertEqual(captured['env']['GRAPHIFY_OUT'], str(self.state / 'graphify'))

    def test_sync_uses_the_same_path_as_build(self):
        with patch('tools.shutil.which', return_value='/usr/local/bin/graphify'), \
                patch('tools.subprocess.run', return_value=argparse.Namespace(returncode=0)) as run_mock:
            with self.assertRaises(SystemExit) as caught:
                tools.cmd_graph(argparse.Namespace(graph_cmd='sync'))
        self.assertEqual(caught.exception.code, 0)
        run_mock.assert_called_once()


class GraphQueryTests(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _sandbox(self)

    def test_query_without_a_built_graph_raises(self):
        with patch('tools.shutil.which', return_value='/usr/local/bin/graphify'):
            with self.assertRaises(SystemExit) as caught:
                tools.cmd_graph(argparse.Namespace(graph_cmd='query', query='foo'))
        self.assertIn('Graph not built', str(caught.exception))

    def _build_graph_file(self):
        graph_dir = self.state / 'graphify'
        graph_dir.mkdir(parents=True, exist_ok=True)
        (graph_dir / 'graph.json').write_text('{}')

    def _capture_run(self, captured):
        def fake_run(cmd, **k):
            captured['cmd'] = cmd
            return argparse.Namespace(returncode=0)
        return fake_run

    def test_query_argv(self):
        self._build_graph_file()
        captured = {}
        with patch('tools.shutil.which', return_value='/usr/local/bin/graphify'), \
                patch('tools.subprocess.run', side_effect=self._capture_run(captured)):
            with self.assertRaises(SystemExit):
                tools.cmd_graph(argparse.Namespace(graph_cmd='query', query='foo'))
        self.assertEqual(captured['cmd'], ['/usr/local/bin/graphify', 'query', 'foo',
                                           '--graph', str(self.state / 'graphify' / 'graph.json')])

    def test_path_argv(self):
        self._build_graph_file()
        captured = {}
        with patch('tools.shutil.which', return_value='/usr/local/bin/graphify'), \
                patch('tools.subprocess.run', side_effect=self._capture_run(captured)):
            with self.assertRaises(SystemExit):
                tools.cmd_graph(argparse.Namespace(graph_cmd='path', start='a', end='b'))
        self.assertEqual(captured['cmd'], ['/usr/local/bin/graphify', 'path', 'a', 'b',
                                           '--graph', str(self.state / 'graphify' / 'graph.json')])

    def test_explain_argv(self):
        self._build_graph_file()
        captured = {}
        with patch('tools.shutil.which', return_value='/usr/local/bin/graphify'), \
                patch('tools.subprocess.run', side_effect=self._capture_run(captured)):
            with self.assertRaises(SystemExit):
                tools.cmd_graph(argparse.Namespace(graph_cmd='explain', node='n1'))
        self.assertEqual(captured['cmd'], ['/usr/local/bin/graphify', 'explain', 'n1',
                                           '--graph', str(self.state / 'graphify' / 'graph.json')])


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
