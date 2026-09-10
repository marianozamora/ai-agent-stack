import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import capabilities  # noqa: E402
import core  # noqa: E402
import crg  # noqa: E402
import repo  # noqa: E402


def _valid_deep_profile():
    return {
        'architecture_summary': 'Single Python CLI package.',
        'stack': {'backend': ['Python'], 'frontend': [], 'other': ['Bash']},
        'database': 'none detected',
        'deployment': 'GitHub Actions',
        'related_repos': [],
        'key_docs': ['README.md: describes the CLI'],
        'confidence_caveats': ['Inferred from static analysis only.'],
    }


class RepoPureTests(unittest.TestCase):
    """check_deep_profile / constants — pure, no I/O."""

    def test_valid_profile_is_returned_unchanged(self):
        value = _valid_deep_profile()
        self.assertIs(repo.check_deep_profile(value), value)

    def test_missing_required_top_level_key_raises(self):
        value = _valid_deep_profile()
        del value['deployment']
        with self.assertRaises(ValueError):
            repo.check_deep_profile(value)

    def test_non_dict_input_raises(self):
        with self.assertRaises(ValueError):
            repo.check_deep_profile(['not', 'a', 'dict'])

    def test_stack_not_a_dict_raises(self):
        value = _valid_deep_profile()
        value['stack'] = ['Python']
        with self.assertRaises(ValueError):
            repo.check_deep_profile(value)

    def test_stack_missing_a_sub_array_raises(self):
        value = _valid_deep_profile()
        del value['stack']['frontend']
        with self.assertRaises(ValueError):
            repo.check_deep_profile(value)

    def test_list_typed_field_that_is_not_a_list_raises(self):
        for key in ('related_repos', 'key_docs', 'confidence_caveats'):
            value = _valid_deep_profile()
            value[key] = 'oops, a string'
            with self.assertRaises(ValueError):
                repo.check_deep_profile(value)

    def test_metrics_advisory_rows_pin(self):
        # Cheap sanity pin, mirrors the doctor test in test_workflow.py.
        self.assertEqual(repo.METRICS_ADVISORY_ROWS, 5000)


class RepoSandboxTests(unittest.TestCase):
    """cmd_* entrypoints that reach core.git_root()/repo_state() need a real repo + cwd."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='repo-sandbox-')
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        self.repo = home / 'repo'
        self.repo.mkdir()
        self.cfg = home / 'config' / 'ai-agent-stack'

        env = {k: v for k, v in os.environ.items() if k not in ('AI_GATE', 'AI_TASK_DIR')}
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'), AI_TASK_ID='repo-task')

        for args in (('init', '-q'), ('config', 'user.name', 'T'), ('config', 'user.email', 't@e.com')):
            subprocess.run(['git', *args], cwd=self.repo, check=True, capture_output=True)
        (self.repo / 'main.py').write_text('print("hi")\n')
        subprocess.run(['git', 'add', '.'], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-qm', 'init'], cwd=self.repo, check=True, capture_output=True)

        envp = mock.patch.dict(os.environ, env, clear=True)
        envp.start()
        self.addCleanup(envp.stop)
        cfgp = mock.patch.object(core, 'CONFIG_ROOT', self.cfg)
        cfgp.start()
        self.addCleanup(cfgp.stop)
        # Optional external tools are all "not installed" for these tests.
        for module in (repo, crg):
            whichp = mock.patch.object(module.shutil, 'which', return_value=None)
            whichp.start()
            self.addCleanup(whichp.stop)

        old_cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, old_cwd)

    def _run(self, fn, args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(args)
        return buf.getvalue()

    # --- cmd_rules ---------------------------------------------------
    def test_cmd_rules_add_list_remove(self):
        self._run(repo.cmd_rules, argparse.Namespace(rules_cmd='add', rule='no raw SQL', scope='src/**'))
        rules = json.loads((self.cfg_repo_state() / 'rules.json').read_text())
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]['rule'], 'no raw SQL')
        self.assertEqual(rules[0]['scope'], 'src/**')
        self.assertEqual(rules[0]['source'], 'user')
        self.assertEqual(rules[0]['confidence'], 1.0)

        listing = self._run(repo.cmd_rules, argparse.Namespace(rules_cmd='list'))
        self.assertIn('1. [src/**] no raw SQL', listing)

        self._run(repo.cmd_rules, argparse.Namespace(rules_cmd='remove', index=1))
        self.assertEqual(json.loads((self.cfg_repo_state() / 'rules.json').read_text()), [])

    def test_cmd_rules_remove_bad_index_exits(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run(repo.cmd_rules, argparse.Namespace(rules_cmd='remove', index=99))
        self.assertEqual(str(ctx.exception), 'Invalid rule index.')

    def cfg_repo_state(self):
        return core.repo_state(self.repo)

    # --- cmd_status / cmd_init / cmd_doctor / cmd_optimize -------------
    def test_cmd_status_prints_repository_anchor(self):
        out = self._run(repo.cmd_status, argparse.Namespace())
        self.assertIn('Repository:', out)
        self.assertIn('Zero-footprint:', out)

    def test_cmd_init_prints_repository_anchor(self):
        out = self._run(repo.cmd_init, argparse.Namespace())
        self.assertIn('Repository:', out)
        self.assertIn('Languages:', out)

    def test_cmd_doctor_prints_footprint_and_state_anchors(self):
        out = self._run(repo.cmd_doctor, argparse.Namespace())
        self.assertIn('Zero-footprint:', out)
        self.assertIn('State files:', out)

    def test_cmd_doctor_reports_the_configured_reviewer_not_a_hard_coded_codex(self):
        # Previously the tool row was a fixed ['claude','codex',...] list, so doctor
        # kept reporting on the defaults even once a different reviewer was
        # configured via `ai providers set`. It must now name the binary the
        # configured reviewer actually probes.
        meta = core.load_json(self.cfg_repo_state() / 'repo.json', {})
        meta['providers'] = {'reviewer': 'command', 'reviewer_command': ['my-custom-reviewer']}
        core.save_json(self.cfg_repo_state() / 'repo.json', meta)
        out = self._run(repo.cmd_doctor, argparse.Namespace())
        self.assertIn('my-custom-reviewer', out)
        self.assertNotIn('codex', out)

    def test_cmd_doctor_reports_orphaned_state_under_a_different_repo_id(self):
        # Simulates state left over from before repo_state()'s remote-normalization
        # migration existed: a repo.json recorded for this exact root, but under an
        # id that no longer matches the one this checkout resolves to today.
        orphan_dir = self.cfg / 'repos' / 'deadbeefdeadbeef'
        orphan_dir.mkdir(parents=True)
        core.save_json(orphan_dir / 'repo.json',
                       {'repo_id': 'deadbeefdeadbeef', 'last_root': str(self.repo.resolve())})
        out = self._run(repo.cmd_doctor, argparse.Namespace())
        self.assertIn('Orphaned state', out)
        self.assertIn(str(orphan_dir), out)

    def test_cmd_doctor_reports_no_orphaned_state_when_none_exists(self):
        out = self._run(repo.cmd_doctor, argparse.Namespace())
        self.assertNotIn('Orphaned state', out)

    def test_cmd_doctor_probes_every_registered_capability_binary(self):
        # Regression guard for the capability registry rewiring: doctor's probe list
        # used to hardcode ['rtk','codegraph','graphify','code-review-graph','ctx7']
        # directly, with rtk/codegraph never actually detectable anywhere else in the
        # codebase. It now reads the same CAPABILITIES registry build_prompt does.
        out = self._run(repo.cmd_doctor, argparse.Namespace())
        for meta in capabilities.CAPABILITIES.values():
            self.assertIn(meta['binary'], out)

    def test_cmd_optimize_prints_audit_without_writes(self):
        out = self._run(repo.cmd_optimize, argparse.Namespace())
        self.assertIn('Prompt/context optimization audit', out)
        self.assertIn('Repository modified: NO', out)

    # --- cmd_profile (static only; --deep needs codex) ----------------
    def test_cmd_profile_static_prints_json(self):
        out = self._run(repo.cmd_profile, argparse.Namespace(refresh=False, deep=False))
        parsed = json.loads(out)
        self.assertIn('languages', parsed)

    def test_cmd_profile_deep_reports_the_configured_reviewer_by_name(self):
        # setUp() already stubs repo.shutil.which -> None for every module, so this
        # reaches the reviewer-availability check with no reviewer available.
        with self.assertRaises(SystemExit) as caught:
            repo.cmd_profile(argparse.Namespace(refresh=False, deep=True, timeout=5))
        self.assertEqual(str(caught.exception),
                         'Codex CLI missing. Install/authenticate Codex to run a deep profile.')


class CmdInitOnAnEmptyRepoTests(unittest.TestCase):
    """A repo with zero commits has no ref any candidate base can verify against --
    exactly the state `ai init` exists to bootstrap from, and exactly what CI's
    package_smoke.sh exercises (`git init` with no commit, then `ai init`). This
    must never crash; only an explicit, invalid --base should still fail loudly."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='repo-empty-')
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        self.repo = home / 'repo'
        self.repo.mkdir()
        self.cfg = home / 'config' / 'ai-agent-stack'
        subprocess.run(['git', 'init', '-q'], cwd=self.repo, check=True, capture_output=True)

        env = {k: v for k, v in os.environ.items() if k not in ('AI_GATE', 'AI_TASK_DIR')}
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'), AI_TASK_ID='empty-repo')
        envp = mock.patch.dict(os.environ, env, clear=True)
        envp.start()
        self.addCleanup(envp.stop)
        cfgp = mock.patch.object(core, 'CONFIG_ROOT', self.cfg)
        cfgp.start()
        self.addCleanup(cfgp.stop)

        old_cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, old_cwd)

    def _run(self, args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            repo.cmd_init(args)
        return buf.getvalue()

    def test_autodetection_prints_a_note_and_does_not_crash(self):
        out = self._run(argparse.Namespace(base=None))
        self.assertIn('Repository:', out)
        self.assertIn('none detected yet', out)
        meta = core.load_json(core.repo_state(self.repo) / 'repo.json', {})
        self.assertNotIn('default_base', meta)

    def test_an_explicit_invalid_base_still_fails_loudly(self):
        with self.assertRaises(SystemExit) as caught:
            self._run(argparse.Namespace(base='nonexistent-ref'))
        self.assertIn("'nonexistent-ref' does not exist", str(caught.exception))


if __name__ == '__main__':
    unittest.main()
