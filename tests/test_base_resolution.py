"""Fail-closed base resolution: an unknown base must never degrade to HEAD."""
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


if __name__ == '__main__':
    unittest.main()
