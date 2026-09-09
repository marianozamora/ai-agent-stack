import subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import crg  # noqa: E402


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


if __name__ == '__main__':
    unittest.main()
