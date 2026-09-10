import json
import os
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

    def test_resolve_profile_auto_downgrades_only_small_low_risk_routine_changes(self):
        small_feature = {'files': ['src/ui/footer.tsx'], 'file_count': 1, 'changed_lines': 20}
        self.assertEqual(core.resolve_profile(None, small_feature, 'agrega un footer'), ('fast', 'small low-risk change'))
        self.assertEqual(core.resolve_profile(None, small_feature, 'fix broken footer link'), ('fast', 'small low-risk change'))

        # Explicit choice always wins, downgrade or not.
        self.assertEqual(core.resolve_profile('standard', small_feature, 'agrega un footer'), ('standard', None))
        self.assertEqual(core.resolve_profile('strict', small_feature, 'agrega un footer'), ('strict', None))

        # High/medium-risk path, security boundary, big diff, or architectural intent -> standard.
        self.assertEqual(core.resolve_profile(None, {'files': ['src/auth/token.py'], 'file_count': 1, 'changed_lines': 5}, 'tweak')[0], 'standard')
        self.assertEqual(core.resolve_profile(None, {'files': ['a.py'] * 7, 'file_count': 7, 'changed_lines': 5}, 'small change')[0], 'standard')
        self.assertEqual(core.resolve_profile(None, small_feature, 'refactor the footer component')[0], 'standard')
        self.assertEqual(core.resolve_profile(None, small_feature, 'redesign footer', 'https://figma.com/x')[0], 'standard')

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


class ProcessCacheTests(unittest.TestCase):
    """Guards the per-process caches in core.py.

    git_root() and task_state()'s scaffolding are memoized because every command
    paid a ~16ms git spawn (and five mkdirs plus an atomic rewrite) to re-derive
    values that cannot change under a running process. "Cannot change" is only
    true with the right key, so these tests pin the two ways a wrong key would
    silently corrupt state: one cwd's root leaking into another repo, and a task
    switch handing back the previous task's directory.
    """

    def setUp(self):
        core._clear_caches()
        self.addCleanup(core._clear_caches)
        self.tmp = tempfile.TemporaryDirectory(prefix='cache-test-')
        self.addCleanup(self.tmp.cleanup)
        self.old_cwd = os.getcwd()
        self.addCleanup(lambda: os.chdir(self.old_cwd))

    def _repo(self, name):
        repo = Path(self.tmp.name) / name
        repo.mkdir()
        for args in (('init', '-q'), ('config', 'user.name', 'T'), ('config', 'user.email', 't@e.com')):
            subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True)
        (repo / 'f.txt').write_text('x\n')
        subprocess.run(['git', 'add', '.'], cwd=repo, check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-qm', 'init'], cwd=repo, check=True, capture_output=True)
        return repo

    def test_git_root_is_keyed_by_cwd_not_a_single_slot(self):
        # The failure this prevents: a global one-slot cache returning repo A's
        # root while the process is inside repo B, so every path derived from it
        # (external state, task dirs) silently belongs to the wrong repository.
        a, b = self._repo('a'), self._repo('b')
        os.chdir(a)
        root_a = core.git_root()
        os.chdir(b)
        root_b = core.git_root()
        self.assertEqual(root_a.resolve(), a.resolve())
        self.assertEqual(root_b.resolve(), b.resolve())
        self.assertNotEqual(root_a, root_b)

    def test_git_root_caches_and_stops_respawning_git(self):
        repo = self._repo('cached')
        os.chdir(repo)
        core.git_root()
        with mock.patch.object(core, 'run', side_effect=AssertionError('git respawned')):
            self.assertEqual(core.git_root().resolve(), repo.resolve())

    def test_git_root_failure_is_not_cached(self):
        # A caller that recovers by chdir'ing into a repo must not keep getting the
        # cached failure -- so failures are never stored in the first place.
        plain = Path(self.tmp.name) / 'not-a-repo'
        plain.mkdir()
        os.chdir(plain)
        with self.assertRaises(SystemExit):
            core.git_root()
        self.assertNotIn(os.getcwd(), core._GIT_ROOT_CACHE)

    def test_task_state_switching_identity_returns_a_different_directory(self):
        # The scaffolding cache must never outrank a live identity change: `ai start`
        # / `ai switch` move the active task mid-process, and a cache keyed only by
        # state would hand the new task the old task's contracts and evidence.
        repo = self._repo('tasks')
        os.chdir(repo)
        state = Path(self.tmp.name) / 'state'
        state.mkdir()
        with mock.patch.object(core, 'TASK_ID', 'ticket-one'):
            first = core.task_state(state)
        with mock.patch.object(core, 'TASK_ID', 'ticket-two'):
            second = core.task_state(state)
        self.assertNotEqual(first, second)
        for directory in (first, second):
            self.assertTrue((directory / 'contracts').is_dir())
            self.assertTrue((directory / 'task.json').is_file())
        self.assertEqual(json.loads((first / 'task.json').read_text())['id'], 'ticket-one')
        self.assertEqual(json.loads((second / 'task.json').read_text())['id'], 'ticket-two')

    def test_task_state_repeat_call_skips_the_rewrite_but_returns_same_dir(self):
        repo = self._repo('repeat')
        os.chdir(repo)
        state = Path(self.tmp.name) / 'state2'
        state.mkdir()
        with mock.patch.object(core, 'TASK_ID', 'ticket-repeat'):
            first = core.task_state(state)
            with mock.patch.object(core, 'save_json', side_effect=AssertionError('rewrote task.json')):
                self.assertEqual(core.task_state(state), first)

    def test_clear_caches_resets_every_process_cache(self):
        repo = self._repo('clearable')
        os.chdir(repo)
        core.git_root()
        self.assertTrue(core._GIT_ROOT_CACHE)
        core._clear_caches()
        self.assertFalse(core._GIT_ROOT_CACHE)
        self.assertFalse(core._TASK_SCAFFOLD_DONE)
        self.assertFalse(core._REMOTE_ID_CACHE)
        self.assertFalse(core._ORIGIN_URL_CACHE)

class StackRootOverrideTests(unittest.TestCase):
    """Covers AI_STACK_HOME, the override that says where shipped assets live.

    resolve_stack_root() is a pure function on purpose: STACK_ROOT is computed at
    import time, so the alternative would be reimport gymnastics in every test.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='stack-home-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'source'
        self.bundled = self.base / 'bundled'
        for d in (self.source, self.bundled):
            d.mkdir()
            (d / 'VERSION').write_text('0.0.0\n')

    def test_no_override_prefers_source_when_it_has_a_version(self):
        self.assertEqual(core.resolve_stack_root(None, self.source, self.bundled), self.source)

    def test_no_override_falls_back_to_bundled_without_a_version(self):
        (self.source / 'VERSION').unlink()
        self.assertEqual(core.resolve_stack_root(None, self.source, self.bundled), self.bundled)

    def test_override_wins_over_both(self):
        other = self.base / 'elsewhere'
        other.mkdir()
        (other / 'VERSION').write_text('9.9.9\n')
        self.assertEqual(core.resolve_stack_root(str(other), self.source, self.bundled), other.resolve())

    def test_override_without_version_fails_closed(self):
        # The point of failing here: a typo'd path that quietly fell back to the source
        # tree would hide which prompts and skills a run actually loaded.
        empty = self.base / 'not-an-install'
        empty.mkdir()
        with self.assertRaises(SystemExit) as ctx:
            core.resolve_stack_root(str(empty), self.source, self.bundled)
        self.assertIn('AI_STACK_HOME', str(ctx.exception))
        self.assertIn('VERSION', str(ctx.exception))

    def test_empty_override_is_treated_as_unset(self):
        self.assertEqual(core.resolve_stack_root('', self.source, self.bundled), self.source)

    def test_override_expands_user_and_resolves(self):
        nested = self.base / 'a' / '..' / 'source'
        self.assertEqual(core.resolve_stack_root(str(nested), self.source, self.bundled), self.source.resolve())
