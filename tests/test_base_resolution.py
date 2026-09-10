"""Fail-closed base resolution: an unknown base must never degrade to HEAD."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import core  # noqa: E402


def build_repo(directory: Path, branch: str = 'master') -> Path:
    def git(*argv):
        subprocess.run(['git', *argv], cwd=directory, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    git('init', '-b', branch)
    git('config', 'user.email', 'test@example.com')
    git('config', 'user.name', 'Test')
    (directory / 'app.py').write_text('value = 1\n')
    git('add', '.')
    git('commit', '-m', 'initial')
    return directory


class BaseResolutionTests(unittest.TestCase):
    def test_unknown_base_fails_and_names_real_refs(self):
        # The regression this whole phase exists for: `--base main` in a master repo
        # used to silently become HEAD, yielding an empty scope, LOW risk, no review
        # gate, and a PR_READY certification over a diff nobody reviewed.
        with tempfile.TemporaryDirectory() as d:
            root = build_repo(Path(d))
            with self.assertRaises(SystemExit) as caught:
                core.resolve_base(root, 'main')
            message = str(caught.exception)
            self.assertIn("'main' does not exist", message)
            self.assertIn('master', message)

    def test_collect_scope_refuses_an_unverified_base(self):
        with tempfile.TemporaryDirectory() as d:
            root = build_repo(Path(d))
            with self.assertRaises(SystemExit):
                core.collect_scope(root, 'main')

    def test_existing_base_resolves_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            root = build_repo(Path(d))
            self.assertEqual(core.resolve_base(root, 'master'), 'master')

    def test_commit_sha_and_tag_are_valid_bases(self):
        with tempfile.TemporaryDirectory() as d:
            root = build_repo(Path(d))
            subprocess.run(['git', 'tag', 'v1'], cwd=root, check=True)
            head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, check=True,
                                  capture_output=True, text=True).stdout.strip()
            self.assertEqual(core.resolve_base(root, 'v1'), 'v1')
            self.assertEqual(core.resolve_base(root, head), head)

    def test_autodetection_picks_the_existing_default_branch(self):
        with tempfile.TemporaryDirectory() as d:
            root = build_repo(Path(d))
            self.assertEqual(core.resolve_base(root, None), 'master')

    def test_stored_default_base_is_used_and_verified(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as s:
            root = build_repo(Path(d))
            state = Path(s)
            (state / 'repo.json').write_text('{"default_base": "master"}')
            self.assertEqual(core.resolve_base(root, None, state), 'master')

            # A recorded base that later disappears is an error, not a fallback.
            (state / 'repo.json').write_text('{"default_base": "gone"}')
            with self.assertRaises(SystemExit) as caught:
                core.resolve_base(root, None, state)
            self.assertIn('stored default base', str(caught.exception))

    def test_explicit_base_wins_over_the_stored_one(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as s:
            root = build_repo(Path(d))
            subprocess.run(['git', 'tag', 'v1'], cwd=root, check=True)
            state = Path(s)
            (state / 'repo.json').write_text('{"default_base": "master"}')
            self.assertEqual(core.resolve_base(root, 'v1', state), 'v1')

    def test_suggestions_offer_the_remote_name_for_a_local_near_miss(self):
        with tempfile.TemporaryDirectory() as d:
            root = build_repo(Path(d))
            # Simulate the common case: only origin/main exists, the user typed `main`.
            subprocess.run(['git', 'update-ref', 'refs/remotes/origin/main', 'HEAD'],
                           cwd=root, check=True)
            subprocess.run(['git', 'branch', '-m', 'master', 'work'], cwd=root, check=True)
            self.assertEqual(core.base_suggestions(root, 'main')[0], 'origin/main')

    def test_verify_ref_rejects_option_like_and_empty_refs(self):
        with tempfile.TemporaryDirectory() as d:
            root = build_repo(Path(d))
            self.assertFalse(core.verify_ref(root, ''))
            self.assertFalse(core.verify_ref(root, '--all'))


class RiskSignalTests(unittest.TestCase):
    """Covers the scope signals that decide whether the review gate is required.

    classify() reads collect_scope()'s numbers, required_gates() drops the review
    gate when the verdict is LOW, and `ai ready` then certifies PR_READY off that.
    So a change that scores zero here is a change no reviewer ever sees -- which is
    what made the two holes below worth closing: `git diff --numstat` prints `-` for
    binaries, and does not describe untracked files at all, so both used to arrive
    at classify() as a 0-line change.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='risk-signals-')
        self.addCleanup(self.tmp.cleanup)
        self.root = build_repo(Path(self.tmp.name))

    def git(self, *argv):
        subprocess.run(['git', *argv], cwd=self.root, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def scope(self):
        return core.collect_scope(self.root, 'HEAD')

    def test_replaced_binary_is_counted_and_never_scores_low(self):
        (self.root / 'blob.bin').write_bytes(os.urandom(100_000))
        self.git('add', '-A')
        self.git('commit', '-m', 'add binary')
        (self.root / 'blob.bin').write_bytes(os.urandom(200_000))
        scope = self.scope()
        self.assertEqual(scope['binary_files'], ['blob.bin'])
        verdict = core.classify(scope, 'standard')
        self.assertNotEqual(verdict['risk'], 'LOW')
        self.assertIn('binary', verdict['reason'])

    def test_new_untracked_file_contributes_its_lines(self):
        (self.root / 'brand_new.py').write_text('value = 1\n' * 2000)
        scope = self.scope()
        self.assertEqual(scope['changed_lines'], 2000)
        self.assertEqual(core.classify(scope, 'standard')['risk'], 'MEDIUM')

    def test_new_untracked_binary_is_reported_as_binary_not_lines(self):
        (self.root / 'asset.bin').write_bytes(b'\x00\x01\x02' * 1000)
        scope = self.scope()
        self.assertEqual(scope['binary_files'], ['asset.bin'])
        self.assertEqual(scope['changed_lines'], 0)
        self.assertNotEqual(core.classify(scope, 'standard')['risk'], 'LOW')

    def test_a_small_new_file_still_scores_low(self):
        # The counterweight: counting untracked lines must not turn every new file
        # into a review-gated change, or the signal stops meaning anything.
        (self.root / 'tiny.py').write_text('value = 2\n')
        scope = self.scope()
        self.assertEqual(scope['changed_lines'], 1)
        self.assertEqual(core.classify(scope, 'standard')['risk'], 'LOW')

    def test_untracked_file_without_trailing_newline_counts_its_last_line(self):
        (self.root / 'nonewline.py').write_text('a\nb\nc')
        self.assertEqual(self.scope()['changed_lines'], 3)

    def test_pure_rename_is_reported_and_still_scores_low(self):
        (self.root / 'old_name.py').write_text('value = 3\n' * 500)
        self.git('add', '-A')
        self.git('commit', '-m', 'add file to rename')
        self.git('mv', 'old_name.py', 'new_name.py')
        scope = self.scope()
        self.assertEqual(len(scope['renamed_files']), 1)
        self.assertIn('new_name.py', scope['renamed_files'][0])
        # A rename moves no content, so it legitimately stays LOW; the signal exists
        # so a large reorganisation is not indistinguishable from an empty diff.
        self.assertEqual(scope['changed_lines'], 0)
        self.assertEqual(core.classify(scope, 'standard')['risk'], 'LOW')

    def test_framework_paths_are_excluded_from_the_signals(self):
        for pattern in core.AI_PATTERNS[:1]:
            target = self.root / pattern
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.suffix:
                target.mkdir(parents=True, exist_ok=True)
                target = target / 'noise.bin'
            target.write_bytes(b'\x00' * 5000)
        scope = self.scope()
        self.assertEqual(scope['binary_files'], [])


class TempWorktreeTests(unittest.TestCase):
    """The disposable-worktree primitive `ai review --commit`/`--pr` builds on."""

    def test_checks_out_the_ref_and_cleans_up_on_exit(self):
        with tempfile.TemporaryDirectory() as d:
            root = build_repo(Path(d))
            (root / 'app.py').write_text('value = 2\n')
            subprocess.run(['git', 'commit', '-am', 'second'], cwd=root, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            first = subprocess.run(['git', 'rev-parse', 'HEAD^'], cwd=root, check=True,
                                   capture_output=True, text=True).stdout.strip()
            with core.temp_worktree(root, first) as checkout:
                self.assertTrue(checkout.is_dir())
                self.assertEqual((checkout / 'app.py').read_text(), 'value = 1\n')
                self.assertNotEqual(checkout, root)
            self.assertFalse(checkout.exists())
            listing = subprocess.run(['git', 'worktree', 'list'], cwd=root, check=True,
                                     capture_output=True, text=True).stdout
            self.assertNotIn(str(checkout), listing)

    def test_cleans_up_even_when_the_body_raises(self):
        with tempfile.TemporaryDirectory() as d:
            root = build_repo(Path(d))
            with self.assertRaises(RuntimeError):
                with core.temp_worktree(root, 'master') as checkout:
                    raise RuntimeError('review body failed')
            self.assertFalse(checkout.exists())

    def test_never_touches_the_callers_own_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            root = build_repo(Path(d))
            (root / 'app.py').write_text('value = 2\n')
            subprocess.run(['git', 'commit', '-am', 'second'], cwd=root, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            before = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, check=True,
                                    capture_output=True, text=True).stdout
            with core.temp_worktree(root, 'HEAD^'):
                pass
            after = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, check=True,
                                   capture_output=True, text=True).stdout
            self.assertEqual(before, after)
            status = subprocess.run(['git', 'status', '--porcelain'], cwd=root, check=True,
                                    capture_output=True, text=True).stdout
            self.assertEqual(status, '')


if __name__ == '__main__':
    unittest.main()
