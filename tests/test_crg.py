import argparse, contextlib, io, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import core  # noqa: E402
import crg  # noqa: E402


def _sandbox(tc):
    """Isolated git repo + redirected external state, mirroring tests/test_gates.py::_sandbox."""
    tmp = tempfile.TemporaryDirectory(prefix='crg-test-')
    tc.addCleanup(tmp.cleanup)
    home = Path(tmp.name)
    repo = home / 'repo'
    repo.mkdir()
    cfg = home / 'config' / 'ai-agent-stack'

    env = {k: v for k, v in os.environ.items() if k not in ('AI_GATE', 'AI_TASK_DIR')}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'), AI_TASK_ID='review-task')

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


class ParseCrgRiskTests(unittest.TestCase):
    def test_labelled_forms_yield_uppercased_severity(self):
        # "risk: X" / "risk level = X" / "risk score - X" -> bare severity, uppercased
        self.assertEqual(crg.parse_crg_risk('risk: HIGH'), 'HIGH')
        self.assertEqual(crg.parse_crg_risk('Risk Level = medium'), 'MEDIUM')
        self.assertEqual(crg.parse_crg_risk('risk score - low'), 'LOW')

    def test_adjective_forms_match(self):
        # "<severity> risk" phrasing is the second accepted pattern
        self.assertEqual(crg.parse_crg_risk('HIGH risk detected'), 'HIGH')
        self.assertEqual(crg.parse_crg_risk('this is LOW risk'), 'LOW')

    def test_no_risk_mention_returns_none(self):
        self.assertIsNone(crg.parse_crg_risk('all clear, nothing notable to report'))

    def test_word_boundary_prevents_false_match(self):
        # "brisket" contains the letters "risk" but not as a word
        self.assertIsNone(crg.parse_crg_risk('brisket for dinner, HIGH hopes'))


class ElevateRiskTests(unittest.TestCase):
    def test_none_crg_risk_returns_base_untouched(self):
        # code path: `if not crg_risk: return base_risk` (same object)
        base = {'risk': 'LOW', 'reason': 'heuristic'}
        self.assertIs(crg.elevate_risk(base, None), base)

    def test_crg_elevates_low_to_high_and_returns_a_copy(self):
        base = {'risk': 'LOW', 'reason': 'heuristic'}
        out = crg.elevate_risk(base, 'HIGH')
        self.assertEqual(out['risk'], 'HIGH')
        self.assertTrue(out['crg_elevated'])
        self.assertIn('CRG', out['reason'])
        # input dict is not mutated - a fresh copy is returned
        self.assertEqual(base, {'risk': 'LOW', 'reason': 'heuristic'})
        self.assertIsNot(out, base)

    def test_lower_crg_risk_never_downgrades_base(self):
        out = crg.elevate_risk({'risk': 'HIGH', 'reason': 'auth path'}, 'LOW')
        self.assertEqual(out['risk'], 'HIGH')
        self.assertFalse(out['crg_elevated'])

    def test_equal_rank_is_not_an_elevation(self):
        out = crg.elevate_risk({'risk': 'MEDIUM', 'reason': 'size'}, 'MEDIUM')
        self.assertEqual(out['risk'], 'MEDIUM')
        self.assertFalse(out['crg_elevated'])


class CrgCmdTests(unittest.TestCase):
    def test_missing_binary_returns_none(self):
        with patch('crg.shutil.which', return_value=None):
            self.assertIsNone(crg.crg_cmd())

    def test_present_binary_returns_argv_list(self):
        with patch('crg.shutil.which', return_value='/usr/bin/code-review-graph'):
            self.assertEqual(crg.crg_cmd(), ['/usr/bin/code-review-graph'])


class CrgEnvTests(unittest.TestCase):
    def test_env_points_at_external_state_dirs_and_keeps_ambient(self):
        state = Path('/tmp/some-repo-state')
        env = crg.crg_env(state)
        self.assertEqual(env['CRG_DATA_DIR'], str(state / 'code-review-graph'))
        self.assertEqual(env['GRAPHIFY_OUT'], str(state / 'graphify'))
        self.assertEqual(env['AI_REPO_STATE'], str(state))
        # ambient environment is preserved (copied, not replaced)
        self.assertIn('PATH', env)


class CrgExecTests(unittest.TestCase):
    def test_missing_binary_soft_fails_with_127(self):
        with patch('crg.shutil.which', return_value=None):
            self.assertEqual(
                crg.crg_exec(Path('.'), Path('/tmp/s'), ['status'], check=False), (127, ''))

    def test_missing_binary_raises_when_checked(self):
        with patch('crg.shutil.which', return_value=None):
            with self.assertRaises(RuntimeError):
                crg.crg_exec(Path('.'), Path('/tmp/s'), ['status'], check=True)


class CrgImpactTests(unittest.TestCase):
    def test_impact_is_empty_without_the_binary(self):
        # crg_impact short-circuits before touching any state when crg_cmd() is None
        with patch('crg.shutil.which', return_value=None):
            self.assertEqual(crg.crg_impact(Path('.'), Path('/tmp/s'), 'HEAD'), '')


def _build_repo(directory: Path) -> Path:
    def git(*argv):
        subprocess.run(['git', *argv], cwd=directory, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    git('init', '-b', 'main')
    git('config', 'user.email', 'test@example.com')
    git('config', 'user.name', 'Test')
    (directory / 'app.py').write_text('one\n')
    git('add', '.')
    git('commit', '-m', 'initial')
    return directory


def _head(root: Path) -> str:
    return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


class ResolveCommitTargetTests(unittest.TestCase):
    """`ai review --commit <sha>`'s target resolution: full sha + diff base."""

    def test_ordinary_commit_resolves_to_its_parent(self):
        with tempfile.TemporaryDirectory() as d:
            root = _build_repo(Path(d))
            first = _head(root)
            (root / 'app.py').write_text('two\n')
            subprocess.run(['git', 'commit', '-am', 'second'], cwd=root, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            second = _head(root)
            full, base = crg.resolve_commit_target(root, second[:12])
            self.assertEqual(full, second)
            self.assertEqual(base, first)

    def test_root_commit_uses_the_empty_tree(self):
        # A root commit has no parent to diff against; the well-known empty-tree
        # object is what makes it reviewable at all instead of crashing.
        with tempfile.TemporaryDirectory() as d:
            root = _build_repo(Path(d))
            full, base = crg.resolve_commit_target(root, 'HEAD')
            self.assertEqual(full, _head(root))
            self.assertEqual(base, crg.EMPTY_TREE)
            # And collect_scope() must actually accept it, not just this function.
            from core import collect_scope
            scope = collect_scope(root, base)
            self.assertEqual(scope['file_count'], 1)  # app.py, added

    def test_nonexistent_commit_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root = _build_repo(Path(d))
            with self.assertRaises(SystemExit) as caught:
                crg.resolve_commit_target(root, 'deadbeef1234')
            self.assertIn('does not exist', str(caught.exception))

    def test_commit_range_fails_closed_instead_of_crashing(self):
        # Regression: `git rev-parse --verify --quiet "A..B^{commit}"` doesn't reject a
        # range outright - it expands to two lines ("B" then "^A"), which used to be
        # passed straight to `git worktree add` as a garbage multi-line ref, crashing
        # with an unhandled traceback instead of a clean refusal.
        with tempfile.TemporaryDirectory() as d:
            root = _build_repo(Path(d))
            first = _head(root)
            (root / 'app.py').write_text('two\n')
            subprocess.run(['git', 'commit', '-am', 'second'], cwd=root, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            second = _head(root)
            with self.assertRaises(SystemExit) as caught:
                crg.resolve_commit_target(root, f'{first}..{second}')
            self.assertIn('not a single commit', str(caught.exception))
            self.assertIn('--base', str(caught.exception))


class ResolvePrTargetTests(unittest.TestCase):
    """`ai review --pr <n>`'s target resolution, with git/gh calls mocked."""

    def test_missing_gh_fails_with_actionable_message(self):
        with patch('crg.shutil.which', return_value=None):
            with self.assertRaises(SystemExit) as caught:
                crg.resolve_pr_target(Path('.'), 5)
        self.assertIn('GitHub CLI (gh) missing', str(caught.exception))
        self.assertIn('--commit', str(caught.exception))

    def test_happy_path_fetches_head_and_resolves_base(self):
        calls = []

        def fake_run(cmd, cwd=None, check=True, capture=True, env=None):
            calls.append(cmd)
            if cmd[1:3] == ['pr', 'view']:
                return '{"baseRefName": "main", "headRefName": "feature-x"}'
            if cmd[:2] == ['git', 'rev-parse']:
                return 'abc123headsha'
            return ''

        with patch('crg.shutil.which', return_value='/usr/bin/gh'), \
                patch('crg.run', side_effect=fake_run), \
                patch('crg.verify_ref', return_value=True):
            head, base, label = crg.resolve_pr_target(Path('.'), 5)
        self.assertEqual(head, 'abc123headsha')
        self.assertEqual(base, 'origin/main')
        self.assertIn('PR #5', label)
        self.assertIn('feature-x', label)
        self.assertIn('main', label)
        # The PR's head must be fetched by number, never by trusting a local branch.
        self.assertTrue(any(c[:3] == ['git', 'fetch', 'origin'] and 'pull/5/head' in c[3] for c in calls))

    def test_uses_merge_base_not_the_live_base_tip(self):
        # Regression: an already-merged (or simply stale) PR's head diffed against
        # today's origin/<base> tip shows everything the base branch picked up since,
        # not what the PR introduced. The merge-base must be used instead.
        def fake_run(cmd, cwd=None, check=True, capture=True, env=None):
            if cmd[1:3] == ['pr', 'view']:
                return '{"baseRefName": "main", "headRefName": "feature-x"}'
            if cmd[:2] == ['git', 'rev-parse']:
                return 'abc123headsha'
            if cmd[:2] == ['git', 'merge-base']:
                return 'deadfork00000000'
            return ''

        with patch('crg.shutil.which', return_value='/usr/bin/gh'), \
                patch('crg.run', side_effect=fake_run), \
                patch('crg.verify_ref', return_value=True):
            head, base, label = crg.resolve_pr_target(Path('.'), 5)
        self.assertEqual(base, 'deadfork00000000')
        self.assertNotEqual(base, 'origin/main')

    def test_falls_back_to_live_tip_when_merge_base_finds_no_ancestor(self):
        # Unrelated-history edge case: merge-base finds nothing (empty output) - degrade
        # to the old behavior instead of crashing or reviewing against a blank base.
        def fake_run(cmd, cwd=None, check=True, capture=True, env=None):
            if cmd[1:3] == ['pr', 'view']:
                return '{"baseRefName": "main", "headRefName": "feature-x"}'
            if cmd[:2] == ['git', 'rev-parse']:
                return 'abc123headsha'
            if cmd[:2] == ['git', 'merge-base']:
                return ''
            return ''

        with patch('crg.shutil.which', return_value='/usr/bin/gh'), \
                patch('crg.run', side_effect=fake_run), \
                patch('crg.verify_ref', return_value=True):
            head, base, label = crg.resolve_pr_target(Path('.'), 5)
        self.assertEqual(base, 'origin/main')

    def test_gh_output_missing_base_branch_fails_clearly(self):
        with patch('crg.shutil.which', return_value='/usr/bin/gh'), \
                patch('crg.run', return_value='{}'):
            with self.assertRaises(SystemExit) as caught:
                crg.resolve_pr_target(Path('.'), 5)
        self.assertIn('Could not resolve PR #5', str(caught.exception))

    def test_unresolvable_base_after_fetch_fails_closed(self):
        def fake_run(cmd, cwd=None, check=True, capture=True, env=None):
            if cmd[1:3] == ['pr', 'view']:
                return '{"baseRefName": "gone", "headRefName": "x"}'
            if cmd[:2] == ['git', 'rev-parse']:
                return 'abc123'
            return ''

        with patch('crg.shutil.which', return_value='/usr/bin/gh'), \
                patch('crg.run', side_effect=fake_run), \
                patch('crg.verify_ref', return_value=False):
            with self.assertRaises(SystemExit) as caught:
                crg.resolve_pr_target(Path('.'), 5)
        self.assertIn("PR #5's base branch", str(caught.exception))


class CmdReviewLaunchReviewerTests(unittest.TestCase):
    """cmd_review's launch path now goes through providers.reviewer(state) instead of
    hard-coding Codex -- it must refuse any reviewer that isn't proven read-only,
    since a launched review streams a reviewer directly against review_root (the
    caller's own checkout, or a disposable worktree for --commit/--pr)."""

    def setUp(self):
        self.root, self.state = _sandbox(self)

    def _args(self, **overrides):
        base = dict(commit=None, pr=None, base='HEAD', refresh=False, build=False,
                    profile='standard', launch=True)
        base.update(overrides)
        return argparse.Namespace(**base)

    def _configure_command_reviewer(self, command):
        meta = core.load_json(self.state / 'repo.json', {})
        meta['providers'] = {'reviewer': 'command', 'reviewer_command': command}
        core.save_json(self.state / 'repo.json', meta)

    def test_refuses_a_reviewer_without_a_readonly_guarantee(self):
        # CommandReviewer.read_only is always False: the stack cannot prove an
        # arbitrary command's sandboxing, so a launched review must refuse it
        # before ever invoking it, not just report it as unavailable.
        self._configure_command_reviewer(['/bin/echo'])
        with self.assertRaises(SystemExit) as caught:
            crg.cmd_review(self._args())
        message = str(caught.exception)
        self.assertIn('read-only', message)
        self.assertIn('sandbox', message)

    def test_missing_codex_reports_the_configured_reviewer_by_name(self):
        # Default provider config (no repo.json override) resolves to CodexReviewer,
        # which is read_only=True -- the refusal above must not fire for it, and a
        # missing binary must still be reported by the reviewer's own name.
        with mock.patch.object(crg.shutil, 'which', return_value=None):
            with self.assertRaises(SystemExit) as caught:
                crg.cmd_review(self._args())
        self.assertIn('Codex CLI missing', str(caught.exception))

    def test_no_launch_never_reaches_the_reviewer_check(self):
        # --no-launch (launch=False) must return after preparing the prompt,
        # regardless of what reviewer is configured or installed.
        self._configure_command_reviewer(['/bin/echo'])
        buf = []
        with mock.patch('builtins.print', side_effect=lambda *a, **k: buf.append(a)):
            crg.cmd_review(self._args(launch=False))
        self.assertTrue(any('Review context:' in str(a[0]) for a in buf if a))

    def test_launch_uses_the_reviewers_own_argv_not_a_hardcoded_codex_command(self):
        # cmd_review must launch whatever argv the active (read-only) reviewer
        # builds for itself -- not a Codex invocation baked into crg.py. A fake
        # read-only reviewer with its own review_argv() proves the launch path
        # is generic: it's safe today only because CodexReviewer is the sole
        # read_only provider, and this breaks the moment another one is.
        class FakeReadOnlyReviewer:
            name = 'fake'; read_only = True; executable = 'fake-exe'; probe_binary = 'fake-exe'
            def available(self): return True
            def review_argv(self, root, prompt):
                return [sys.executable, '-c', 'import sys; sys.exit(0)']

        fake = FakeReadOnlyReviewer()

        def fake_which(name):
            return '/usr/bin/fake-exe' if name == 'fake-exe' else None

        with mock.patch.object(crg, 'get_reviewer', return_value=fake), \
                mock.patch.object(crg.shutil, 'which', side_effect=fake_which):
            with self.assertRaises(SystemExit) as caught:
                crg.cmd_review(self._args())
        self.assertEqual(caught.exception.code, 0)


class CmdCrgTests(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _sandbox(self)

    def _args(self, crg_cmd, **overrides):
        base = dict(crg_cmd=crg_cmd, base='HEAD', brief=True)
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_doctor_reports_ready_and_missing(self):
        with patch('crg.shutil.which', return_value='/usr/bin/code-review-graph'):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                crg.cmd_crg(self._args('doctor'))
        self.assertIn('Code Review Graph: ready', buf.getvalue())

        with patch('crg.shutil.which', return_value=None):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                crg.cmd_crg(self._args('doctor'))
        self.assertIn('missing', buf.getvalue())

    def test_missing_binary_raises_for_a_mutating_subcommand(self):
        with patch('crg.shutil.which', return_value=None):
            with self.assertRaises(SystemExit) as caught:
                crg.cmd_crg(self._args('build'))
        self.assertIn('code-review-graph missing', str(caught.exception))

    def test_build_argv(self):
        captured = {}
        def fake_run(cmd, **k):
            captured['cmd'] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout='built\n')
        with patch('crg.shutil.which', return_value='/usr/bin/code-review-graph'), \
                patch('crg.subprocess.run', side_effect=fake_run):
            with self.assertRaises(SystemExit) as caught:
                crg.cmd_crg(self._args('build'))
        self.assertEqual(caught.exception.code, 0)
        self.assertEqual(captured['cmd'], ['/usr/bin/code-review-graph', 'build'])

    def test_update_brief_argv(self):
        captured = {}
        def fake_run(cmd, **k):
            captured['cmd'] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout='')
        with patch('crg.shutil.which', return_value='/usr/bin/code-review-graph'), \
                patch('crg.subprocess.run', side_effect=fake_run):
            with self.assertRaises(SystemExit):
                crg.cmd_crg(self._args('update', base='origin/main', brief=True))
        self.assertEqual(captured['cmd'],
                         ['/usr/bin/code-review-graph', 'update', '--base', 'origin/main', '--brief'])

    def test_detect_brief_argv(self):
        captured = {}
        def fake_run(cmd, **k):
            captured['cmd'] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout='')
        with patch('crg.shutil.which', return_value='/usr/bin/code-review-graph'), \
                patch('crg.subprocess.run', side_effect=fake_run):
            with self.assertRaises(SystemExit):
                crg.cmd_crg(self._args('detect', base='origin/main', brief=True))
        self.assertEqual(captured['cmd'],
                         ['/usr/bin/code-review-graph', 'detect-changes', '--base', 'origin/main', '--brief'])

    def test_nonzero_rc_propagates(self):
        with patch('crg.shutil.which', return_value='/usr/bin/code-review-graph'), \
                patch('crg.subprocess.run',
                      return_value=subprocess.CompletedProcess([], 3, stdout='boom')):
            with self.assertRaises(SystemExit) as caught:
                crg.cmd_crg(self._args('build'))
        self.assertEqual(caught.exception.code, 3)


class CrgImpactFlowTests(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _sandbox(self)

    def test_status_failure_without_build_if_missing_returns_empty(self):
        with patch('crg.crg_cmd', return_value=['crg']), \
                patch('crg.crg_exec', return_value=(1, '')) as exec_mock:
            out = crg.crg_impact(self.root, self.state, 'HEAD', build_if_missing=False)
        self.assertEqual(out, '')
        # only the status probe ran -- no build attempted
        self.assertEqual(exec_mock.call_count, 1)
        self.assertEqual(exec_mock.call_args[0][2], ['status'])

    def test_status_failure_with_build_if_missing_calls_build_then_detect(self):
        calls = []
        def fake_exec(root, state, argv, check=True):
            calls.append(argv)
            if argv == ['status']: return (1, '')
            if argv == ['build']: return (0, '')
            if argv[0] == 'detect-changes': return (0, 'impact text')
            return (0, '')
        with patch('crg.crg_cmd', return_value=['crg']), \
                patch('crg.crg_exec', side_effect=fake_exec):
            out = crg.crg_impact(self.root, self.state, 'HEAD', build_if_missing=True)
        self.assertEqual(out, 'impact text')
        self.assertIn(['build'], calls)

    def test_refresh_with_output_writes_last_impact_file(self):
        def fake_exec(root, state, argv, check=True):
            if argv == ['status']: return (0, '')
            if argv[0] == 'update': return (0, 'refreshed impact')
            return (0, '')
        with patch('crg.crg_cmd', return_value=['crg']), \
                patch('crg.crg_exec', side_effect=fake_exec):
            out = crg.crg_impact(self.root, self.state, 'HEAD', refresh=True)
        self.assertEqual(out, 'refreshed impact')
        impact_file = core.task_state(self.state) / 'review' / 'last-impact.txt'
        self.assertEqual(impact_file.read_text(), 'refreshed impact\n')

    def test_refresh_with_empty_update_falls_back_to_detect_changes(self):
        calls = []
        def fake_exec(root, state, argv, check=True):
            calls.append(argv)
            if argv == ['status']: return (0, '')
            if argv[0] == 'update': return (0, '')  # empty -> falls through
            if argv[0] == 'detect-changes': return (0, 'fallback impact')
            return (0, '')
        with patch('crg.crg_cmd', return_value=['crg']), \
                patch('crg.crg_exec', side_effect=fake_exec):
            out = crg.crg_impact(self.root, self.state, 'HEAD', refresh=True)
        self.assertEqual(out, 'fallback impact')
        self.assertTrue(any(c[0] == 'detect-changes' for c in calls))


class CmdImpactTests(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _sandbox(self)

    def test_prints_crg_risk_and_elevated_final_risk(self):
        crg_output = 'some analysis... risk: HIGH ...more text'
        with patch('crg.crg_cmd', return_value=['crg']), \
                patch('crg.crg_impact', return_value=crg_output):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                crg.cmd_impact(argparse.Namespace(base='HEAD', profile='standard', refresh=False, build=False))
        out = buf.getvalue()
        self.assertIn('CRG risk:     HIGH', out)
        self.assertIn('final risk:   HIGH', out)
        self.assertIn(crg_output, out)


class CmdReviewPrTargetTests(unittest.TestCase):
    """cmd_review --pr N writes into its own adhoc-reviews/pr-N/ directory,
    never into the active task's own state/ -- reviewing an unrelated PR must
    not clobber the active task's review artifact."""

    def setUp(self):
        self.root, self.state = _sandbox(self)

    def _args(self, **overrides):
        base = dict(commit=None, pr=7, base=None, refresh=False, build=False,
                    profile='standard', launch=False)
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_pr_review_writes_to_its_own_adhoc_dir_not_the_active_tasks_state(self):
        @contextlib.contextmanager
        def fake_worktree(root, ref):
            yield root  # reuse the sandbox repo itself; no real worktree needed

        with patch.object(crg, 'resolve_pr_target',
                          return_value=('deadbeef1234', 'main', 'PR #7 (feature -> main)')), \
                patch.object(crg, 'temp_worktree', side_effect=fake_worktree), \
                patch.object(crg, 'crg_cmd', return_value=None):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                crg.cmd_review(self._args())

        pr_review = self.state / 'adhoc-reviews' / 'pr-7' / 'current-review.md'
        self.assertTrue(pr_review.is_file())
        active_task_review = core.task_state(self.state) / 'state' / 'current-review.md'
        self.assertFalse(active_task_review.is_file())
        self.assertIn(str(pr_review), buf.getvalue())


if __name__ == '__main__':
    unittest.main()
