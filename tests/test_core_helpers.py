import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
# core.py (and every other ai_stack/ module) uses bare same-directory imports
# (`from workflow import ...`), matching the script-execution model every real
# invocation path relies on — a direct load needs ai_stack/ on sys.path first,
# the same thing running the file as a script does implicitly.
sys.path.insert(0, str(ROOT / 'ai_stack'))
import core  # noqa: E402


class CoreHelpersTests(unittest.TestCase):
    def test_json_file_health_missing_ok_corrupt(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            missing = directory / 'missing.json'
            self.assertEqual(core.json_file_health(missing), 'missing')

            ok = directory / 'ok.json'
            ok.write_text('{"a": 1}')
            self.assertEqual(core.json_file_health(ok), 'ok')

            corrupt = directory / 'corrupt.json'
            corrupt.write_text('{"a": 1,')
            self.assertEqual(core.json_file_health(corrupt), 'corrupt')

    def test_load_json_stays_lenient_on_corruption(self):
        # load_json()'s ~40 callers all assume it never raises; json_file_health()
        # is the separate, explicit path for surfacing corruption (in `ai doctor`),
        # so this contract must not change.
        with tempfile.TemporaryDirectory() as d:
            corrupt = Path(d) / 'corrupt.json'
            corrupt.write_text('not json at all')
            self.assertEqual(core.load_json(corrupt, {'default': True}), {'default': True})

    def test_classify_risk_by_path_and_size(self):
        low = core.classify({'files': ['src/ui/copy.py'], 'file_count': 1, 'changed_lines': 5}, 'standard')
        self.assertEqual(low['risk'], 'LOW')

        high = core.classify({'files': ['src/auth/token.py'], 'file_count': 1, 'changed_lines': 5}, 'standard')
        self.assertEqual(high['risk'], 'HIGH')
        self.assertTrue(high['security'])

        big = core.classify({'files': ['a.py'] * 7, 'file_count': 7, 'changed_lines': 5}, 'standard')
        self.assertEqual(big['risk'], 'MEDIUM')

        strict_floor = core.classify({'files': ['a.py'], 'file_count': 1, 'changed_lines': 1}, 'strict')
        self.assertEqual(strict_floor['risk'], 'MEDIUM')

    def test_context_caps_scale_with_profile(self):
        fast, standard, strict = (core.context_caps(p) for p in ('fast', 'standard', 'strict'))
        self.assertLess(fast['skills'], standard['skills'])
        self.assertLess(standard['skills'], strict['skills'])
        self.assertLess(fast['usage_tokens'], standard['usage_tokens'])
        self.assertLess(standard['usage_tokens'], strict['usage_tokens'])


class NormalizeRemoteTests(unittest.TestCase):
    """normalize_remote() collapses equivalent remote URLs to the same string, so
    a routine `git remote set-url` between protocols (ssh<->https) or an
    equivalent URL spelling never changes a repository's repo_id and orphans its
    recorded rules/validators/lessons/tasks under the old one."""

    def test_ssh_and_https_variants_of_the_same_repo_agree(self):
        variants = [
            'git@github.com:x/y.git',
            'git@github.com:x/y',
            'https://github.com/x/y.git',
            'https://github.com/x/y',
            'ssh://git@github.com/x/y',
            'ssh://git@github.com/x/y.git',
        ]
        normalized = {core.normalize_remote(v) for v in variants}
        self.assertEqual(normalized, {'github.com/x/y'})

    def test_distinguishes_different_repos_and_hosts(self):
        self.assertNotEqual(core.normalize_remote('git@github.com:x/y.git'),
                            core.normalize_remote('git@github.com:x/z.git'))
        self.assertNotEqual(core.normalize_remote('git@github.com:x/y.git'),
                            core.normalize_remote('git@gitlab.com:x/y.git'))

    def test_strips_default_port_but_keeps_a_nondefault_one(self):
        self.assertEqual(core.normalize_remote('https://github.com:443/x/y'),
                         core.normalize_remote('https://github.com/x/y'))
        self.assertNotEqual(core.normalize_remote('https://example.com:8443/x/y'),
                            core.normalize_remote('https://example.com/x/y'))

    def test_host_is_lowercased_but_path_case_is_preserved(self):
        self.assertEqual(core.normalize_remote('https://GitHub.com/x/y'), 'github.com/x/y')
        self.assertEqual(core.normalize_remote('https://github.com/X/Y'), 'github.com/X/Y')

    def test_local_filesystem_path_passes_through_unchanged(self):
        # The fallback for a repo with no `origin` at all -- not a real URL, and
        # must never be reinterpreted as one.
        self.assertEqual(core.normalize_remote('/Users/dev/my-repo'), '/Users/dev/my-repo')

    def test_trailing_slash_is_stripped(self):
        self.assertEqual(core.normalize_remote('https://github.com/x/y/'), 'github.com/x/y')


class RepoStateMigrationTests(unittest.TestCase):
    """repo_state()'s one-time migration off remote_id()'s old normalization,
    which only stripped a trailing `.git` and so hashed an scp-form remote
    (`git@host:x/y`) completely differently from normalize_remote()'s
    `host/x/y`. A `git remote set-url` between ssh and https is routine; this
    is what stops it from silently orphaning a repository's recorded
    rules/validators/lessons/tasks under the old id."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='repo-state-migrate-')
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        self.repo = home / 'repo'
        self.repo.mkdir()
        self.cfg = home / 'config' / 'ai-agent-stack'
        for args in (('init', '-q'), ('config', 'user.name', 'T'), ('config', 'user.email', 't@e.com'),
                     ('remote', 'add', 'origin', 'git@github.com:x/y.git')):
            subprocess.run(['git', *args], cwd=self.repo, check=True, capture_output=True)
        cfgp = mock.patch.object(core, 'CONFIG_ROOT', self.cfg)
        cfgp.start()
        self.addCleanup(cfgp.stop)
        self.legacy_rid, _ = core._legacy_remote_id(self.repo)
        self.new_rid, _ = core.remote_id(self.repo)
        # The scenario this migration exists for: an scp-form remote hashes to a
        # different id under the old normalization than under the new one.
        self.assertNotEqual(self.legacy_rid, self.new_rid)

    def test_migrates_state_from_the_legacy_id_to_the_normalized_one(self):
        legacy_dir = self.cfg / 'repos' / self.legacy_rid
        legacy_dir.mkdir(parents=True)
        (legacy_dir / 'rules.json').write_text('[{"rule": "existing rule", "scope": "**"}]\n')

        state = core.repo_state(self.repo)

        self.assertEqual(state, self.cfg / 'repos' / self.new_rid)
        self.assertFalse(legacy_dir.exists())
        self.assertEqual(json.loads((state / 'rules.json').read_text())[0]['rule'], 'existing rule')
        meta = json.loads((state / 'repo.json').read_text())
        self.assertEqual(meta['migrated_from'], [self.legacy_rid])
        self.assertEqual(meta['repo_id'], self.new_rid)

    def test_migration_is_idempotent_on_a_second_call(self):
        legacy_dir = self.cfg / 'repos' / self.legacy_rid
        legacy_dir.mkdir(parents=True)
        (legacy_dir / 'rules.json').write_text('[]\n')

        core.repo_state(self.repo)
        state = core.repo_state(self.repo)  # legacy dir is already gone by now

        meta = json.loads((state / 'repo.json').read_text())
        self.assertEqual(meta['migrated_from'], [self.legacy_rid])  # not duplicated

    def test_state_under_both_ids_is_left_alone(self):
        legacy_dir = self.cfg / 'repos' / self.legacy_rid
        current_dir = self.cfg / 'repos' / self.new_rid
        legacy_dir.mkdir(parents=True)
        current_dir.mkdir(parents=True)
        (legacy_dir / 'marker.txt').write_text('legacy')

        core.repo_state(self.repo)

        # Ambiguous -- a human's call, never merged automatically.
        self.assertTrue(legacy_dir.is_dir())
        self.assertEqual((legacy_dir / 'marker.txt').read_text(), 'legacy')


if __name__ == '__main__':
    unittest.main()
