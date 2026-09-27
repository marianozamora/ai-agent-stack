"""Direct, in-process unit coverage for ai_stack/gates.py.

gates.py is otherwise only exercised through the slow end-to-end subprocess
tests in tests/test_workflow.py (driving `ai plan` / `ai gate` / `ai pipeline`).
This file covers its pure / near-pure logic without ever spawning codex/claude:

  * evidence_fingerprint()  -- the security-critical staleness/tamper detector
  * validator_config()       -- config load + schema validation
  * cmd_validators()         -- install / show / remove of the builtin validators
  * cmd_validate()           -- the guard paths that raise before invoking Codex

Run just this file:
    python3 -m unittest discover -s tests -p 'test_gates.py' -v
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
# ai_stack/ modules use bare same-directory imports (`from core import ...`),
# matching the script-execution model every real invocation relies on; a direct
# load needs ai_stack/ on sys.path first.
sys.path.insert(0, str(ROOT / 'ai_stack'))
import core  # noqa: E402
import crg  # noqa: E402
import gates  # noqa: E402
import lifecycle  # noqa: E402
import providers  # noqa: E402
import validators  # noqa: E402


def _git(repo, *args):
    subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True)


def _accept(state):
    """cmd_pipeline refuses a contract with no acceptance criteria before any gate runs."""
    contract = core.task_state(state) / 'contracts/current-pr.yml'
    contract.write_text(contract.read_text().replace('acceptance: []', 'acceptance: ["fixture criterion"]'))


def _sandbox(tc):
    """Build an isolated git repo + redirected external state for `tc`.

    Mirrors tests/test_workflow.py::setUp and tests/test_lifecycle.py: a real
    `git init` repo with one commit, HOME / XDG_CONFIG_HOME redirected,
    core.CONFIG_ROOT (fixed at import) patched to a tmp dir, AI_GATE /
    AI_TASK_DIR stripped, cwd chdir'd into the repo and restored on cleanup.
    Returns (root, state).
    """
    tmp = tempfile.TemporaryDirectory(prefix='gates-test-')
    tc.addCleanup(tmp.cleanup)
    home = Path(tmp.name)
    repo = home / 'repo'
    repo.mkdir()
    cfg = home / 'config' / 'ai-agent-stack'

    env = {k: v for k, v in os.environ.items() if k not in ('AI_GATE', 'AI_TASK_DIR')}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'), AI_TASK_ID='gates-task')

    for args in (('init', '-q'), ('config', 'user.name', 'T'), ('config', 'user.email', 't@e.com')):
        _git(repo, *args)
    (repo / 'app.txt').write_text('initial\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'init')

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


class EvidenceFingerprintTests(unittest.TestCase):
    """evidence_fingerprint(root, state, plan) -- the priority coverage."""

    def setUp(self):
        self.root, self.state = _sandbox(self)
        # Hand-rolled minimal plan: evidence_fingerprint only reads
        # plan['scope']['base'] (git rev-parse target) and json.dumps(plan).
        # A real lifecycle.build_prompt plan works too but adds nothing here.
        self.plan = {'scope': {'base': 'HEAD'}, 'profile': 'fast'}
        self.task = core.task_state(self.state)

    def fp(self, plan=None):
        return gates.evidence_fingerprint(self.root, self.state, plan or self.plan)

    def test_deterministic_for_unchanged_tree_and_plan(self):
        self.assertEqual(self.fp(), self.fp())

    def test_tracked_file_edit_changes_fingerprint(self):
        base = self.fp()
        (self.root / 'app.txt').write_text('edited in working tree\n')
        self.assertNotEqual(base, self.fp())

    def test_rewording_a_commit_keeps_code_evidence_but_not_message_evidence(self):
        # Rewording (or squashing to the same tree) changes no code: a code review
        # must stay fresh, while the gates that judge commit messages must re-run.
        first = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=self.root, check=True,
                               capture_output=True, text=True).stdout.strip()
        (self.root / 'app.txt').write_text('second\n')
        _git(self.root, 'commit', '-qam', 'change\n\nCo-Authored-By: Bot <b@e.com>')
        plan = {'scope': {'base': first}, 'profile': 'fast'}
        code = gates.evidence_fingerprint(self.root, self.state, plan)
        messages = gates.evidence_fingerprint(self.root, self.state, plan, gate='provenance')
        _git(self.root, 'commit', '-q', '--amend', '-m', 'change')
        self.assertEqual(code, gates.evidence_fingerprint(self.root, self.state, plan))
        self.assertEqual(code, gates.evidence_fingerprint(self.root, self.state, plan, gate='review'))
        self.assertNotEqual(messages, gates.evidence_fingerprint(self.root, self.state, plan, gate='provenance'))

    def test_a_new_commit_changing_code_moves_every_fingerprint(self):
        base = self.fp()
        (self.root / 'app.txt').write_text('committed change\n')
        _git(self.root, 'commit', '-qam', 'change')
        self.assertNotEqual(base, self.fp())

    def test_reviewer_effort_change_changes_fingerprint(self):
        # A different effort is a different judge: a PASS given at 'low' must not read
        # as fresh once the repository asks for 'high'.
        base = self.fp()
        meta = core.load_json(self.state / 'repo.json', {})
        meta['providers'] = {'reviewer_effort': {'cleanup': 'high'}}
        core.save_json(self.state / 'repo.json', meta)
        self.assertNotEqual(base, self.fp())

    def test_fast_reviewer_effort_change_changes_a_fast_fingerprint(self):
        base = self.fp()
        meta = core.load_json(self.state / 'repo.json', {})
        meta['providers'] = {'fast_reviewer_effort': 'low'}
        core.save_json(self.state / 'repo.json', meta)
        self.assertNotEqual(base, self.fp())

    def test_stack_version_change_changes_fingerprint(self):
        # A stack upgrade reships the bundled validator instructions, so evidence a
        # prior version's prompts produced must not still read as fresh.
        base = self.fp()
        with mock.patch.object(gates, 'VERSION', gates.VERSION + '-next'):
            self.assertNotEqual(base, self.fp())
        self.assertEqual(base, self.fp())

    def test_switching_reviewer_provider_changes_fingerprint(self):
        # Same diff, different model judging it: a review/security/ponytail PASS from
        # the old reviewer must not survive `ai providers` pointing somewhere else.
        base = self.fp()
        other = types.SimpleNamespace(name='some-other-reviewer')
        with mock.patch.object(gates, 'get_reviewer', return_value=other):
            self.assertNotEqual(base, self.fp())
        other_builder = types.SimpleNamespace(name='some-other-builder')
        with mock.patch.object(gates, 'get_builder', return_value=other_builder):
            self.assertNotEqual(base, self.fp())

    def test_new_untracked_file_changes_fingerprint(self):
        base = self.fp()
        (self.root / 'scratch.txt').write_text('untracked evidence gap\n')
        self.assertNotEqual(base, self.fp())

    def test_removing_untracked_file_restores_fingerprint(self):
        base = self.fp()
        extra = self.root / 'scratch.txt'
        extra.write_text('temporary\n')
        self.assertNotEqual(base, self.fp())
        extra.unlink()
        self.assertEqual(base, self.fp())

    def test_plan_change_changes_fingerprint(self):
        base = self.fp()
        other = {'scope': {'base': 'HEAD'}, 'profile': 'strict'}
        self.assertNotEqual(base, self.fp(other))

    def test_declared_state_files_fold_in_others_do_not(self):
        base = self.fp()

        # Files the code explicitly does NOT fold in.
        for name in ('patterns.json', 'prompt-history.jsonl'):
            path = self.state / name
            path.write_text('{"ignored": true}\n')
            self.assertEqual(base, self.fp(), f'{name} must not affect the fingerprint')
            path.unlink()

        # Files it DOES fold in: repo-state configs.
        for name in ('rules.json', 'skill-overrides.json', 'capability-overrides.json', 'validators.json', 'prompt-overrides.json'):
            path = self.state / name
            original = path.read_text() if path.exists() else None
            path.write_text(f'{{"folded": "{name}"}}\n')
            self.assertNotEqual(base, self.fp(), f'{name} must change the fingerprint')
            if original is None:
                path.unlink()
            else:
                path.write_text(original)
            self.assertEqual(base, self.fp())

        # Files it folds in: per-task curated context + contracts.
        for rel in ('state/lessons.json', 'state/prompt-assignment.json', 'state/ticket.json',
                    'contracts/current-pr.yml'):
            path = self.task / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'folded: {rel}\n')
            self.assertNotEqual(base, self.fp(), f'{rel} must change the fingerprint')
            path.unlink()
            self.assertEqual(base, self.fp())

    def test_symlink_hashed_by_target_not_followed(self):
        # An out-of-repo target file so its own bytes are not otherwise folded in.
        outside = self.root.parent / 'link-target-a.txt'
        outside.write_text('target A contents\n')
        other = self.root.parent / 'link-target-b.txt'
        other.write_text('target B contents\n')

        link = self.root / 'evidence-link'
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform dependent
            self.skipTest(f'symlinks unavailable on this platform: {exc}')

        with_link = self.fp()

        # Rewriting the *target file's* bytes must not move the fingerprint:
        # the symlink is hashed by readlink(), never followed.
        outside.write_text('target A rewritten, much longer contents\n')
        self.assertEqual(with_link, self.fp())

        # Repointing the symlink at a different path DOES move it.
        link.unlink()
        link.symlink_to(other)
        self.assertNotEqual(with_link, self.fp())


class EvidenceFingerprintTrackedContentTests(unittest.TestCase):
    """Guards the tracked-content shortcut in evidence_fingerprint().

    The function no longer re-reads tracked files: HEAD, `ls-files --stage` and
    `diff --binary HEAD` already pin their content exactly, so hashing them again
    only made the walk O(repo). That is only safe while every way of mutating a
    tracked file still moves the digest through one of those three, so each case
    below mutates a tracked path a different way and asserts the fingerprint moves.
    Delete these and the shortcut silently becomes a hole in the tamper detector.
    """

    def setUp(self):
        self.root, self.state = _sandbox(self)
        self.plan = {'scope': {'base': 'HEAD'}, 'profile': 'fast'}
        core.task_state(self.state)

    def fp(self):
        return gates.evidence_fingerprint(self.root, self.state, self.plan)

    def test_same_size_same_mtime_content_swap_still_moves_fingerprint(self):
        # The adversarial case the whole function exists for: a gate that edits a
        # tracked file and restores its stat metadata to hide the edit. Equal byte
        # length and a restored mtime defeat any stat-only shortcut, so this is the
        # case that proves the digest is still content-derived and not stat-derived.
        target = self.root / 'app.txt'
        original = target.read_bytes()
        before = self.fp()
        stat = target.stat()
        swapped = bytes(original[:-1].upper()) + b'\n'
        self.assertEqual(len(swapped), len(original))
        self.assertNotEqual(swapped, original)
        target.write_bytes(swapped)
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(target.stat().st_mtime_ns, stat.st_mtime_ns)
        self.assertNotEqual(before, self.fp())

    def test_touching_mtime_alone_does_not_move_fingerprint(self):
        # The converse guard: evidence stays fresh across a no-op rebuild that only
        # restats files. A digest that moved here would make every gate look stale.
        before = self.fp()
        os.utime(self.root / 'app.txt', (100000, 100000))
        self.assertEqual(before, self.fp())

    def test_tracked_mode_change_moves_fingerprint(self):
        before = self.fp()
        os.chmod(self.root / 'app.txt', 0o755)
        self.assertNotEqual(before, self.fp())

    def test_tracked_deletion_moves_fingerprint(self):
        before = self.fp()
        (self.root / 'app.txt').unlink()
        self.assertNotEqual(before, self.fp())

    def test_staging_a_tracked_edit_moves_fingerprint(self):
        before = self.fp()
        (self.root / 'app.txt').write_text('staged edit\n')
        _git(self.root, 'add', 'app.txt')
        self.assertNotEqual(before, self.fp())

    def test_tracked_rename_moves_fingerprint(self):
        before = self.fp()
        _git(self.root, 'mv', 'app.txt', 'renamed.txt')
        self.assertNotEqual(before, self.fp())

    def test_tracked_binary_edit_moves_fingerprint(self):
        (self.root / 'blob.bin').write_bytes(b'\x00\x01\x02')
        _git(self.root, 'add', 'blob.bin')
        _git(self.root, 'commit', '-qm', 'add blob')
        before = self.fp()
        (self.root / 'blob.bin').write_bytes(b'\xff\xfe\xfd')
        self.assertNotEqual(before, self.fp())

    def test_repointing_a_tracked_symlink_moves_fingerprint(self):
        link = self.root / 'tracked-link'
        try:
            link.symlink_to('app.txt')
        except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform dependent
            self.skipTest(f'symlinks unavailable on this platform: {exc}')
        (self.root / 'other.txt').write_text('other\n')
        _git(self.root, 'add', '-A')
        _git(self.root, 'commit', '-qm', 'add tracked symlink')
        before = self.fp()
        link.unlink()
        link.symlink_to('other.txt')
        self.assertNotEqual(before, self.fp())

    def test_untracked_content_is_still_read_in_full(self):
        # Untracked files are the one gap no git diff describes, so they must still
        # be hashed byte for byte -- same length, same mtime, different bytes.
        stray = self.root / 'stray.txt'
        stray.write_text('aaaa\n')
        before = self.fp()
        stat = stray.stat()
        stray.write_text('bbbb\n')
        os.utime(stray, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertNotEqual(before, self.fp())


class ValidatorConfigTests(unittest.TestCase):
    """validator_config(state) -- pure: only reads state/'validators.json'."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='gates-vc-')
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)

    def test_missing_file_returns_validated_empty_config(self):
        self.assertEqual(gates.validator_config(self.state), {'version': 1, 'validators': {}})

    def test_valid_handwritten_config_is_returned_and_validated(self):
        config = {'version': 1, 'validators': {
            'cleanup': {'command': ['/bin/true'], 'adapter': 'json', 'timeout': 60, 'evidence': None}}}
        (self.state / 'validators.json').write_text(json.dumps(config))
        self.assertEqual(gates.validator_config(self.state), config)

    def test_malformed_json_raises_systemexit(self):
        (self.state / 'validators.json').write_text('{ not json')
        with self.assertRaises(SystemExit) as ctx:
            gates.validator_config(self.state)
        self.assertIn('Invalid validator configuration', str(ctx.exception))

    def test_schema_violation_raises_systemexit(self):
        # version != 1 -> workflow.validate_config raises ValueError -> SystemExit.
        (self.state / 'validators.json').write_text(json.dumps({'version': 2, 'validators': {}}))
        with self.assertRaises(SystemExit) as ctx:
            gates.validator_config(self.state)
        self.assertIn('Invalid validator configuration', str(ctx.exception))


class CmdValidatorsTests(unittest.TestCase):
    """cmd_validators(args) -- install / show / remove against a real sandbox."""

    def setUp(self):
        self.root, self.state = _sandbox(self)

    def _run(self, **ns):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            gates.cmd_validators(argparse.Namespace(**ns))
        return buf.getvalue()

    def test_install_writes_builtin_validator_entries(self):
        out = self._run(action='install')
        self.assertIn('Saved:', out)
        config = json.loads((self.state / 'validators.json').read_text())
        self.assertEqual(config['version'], 1)
        from validators import INSTRUCTIONS
        self.assertEqual(set(config['validators']), set(INSTRUCTIONS))
        for name, entry in config['validators'].items():
            self.assertEqual(entry['adapter'], 'json')
            self.assertEqual(entry['builtin'], name)
            self.assertIsInstance(entry['command'], list)
            self.assertTrue(entry['command'])
            self.assertEqual(entry['command'][-1], name)
        # Re-validates cleanly.
        self.assertEqual(gates.validator_config(self.state)['validators'], config['validators'])

    def test_show_prints_config_without_saving_line(self):
        self._run(action='install')
        out = self._run(action='show')
        self.assertNotIn('Saved:', out)
        self.assertEqual(json.loads(out)['version'], 1)

    def test_remove_drops_the_named_validator(self):
        self._run(action='install')
        from validators import INSTRUCTIONS
        victim = next(iter(INSTRUCTIONS))
        out = self._run(action='remove', name=victim)
        self.assertIn('Saved:', out)
        config = json.loads((self.state / 'validators.json').read_text())
        self.assertNotIn(victim, config['validators'])
        self.assertEqual(set(config['validators']), set(INSTRUCTIONS) - {victim})


class CmdValidatorsProposeTests(unittest.TestCase):
    """cmd_validators(action='propose') -- what `ai finish` tells the user to run,
    previously untested. Detection needs real, available tooling (ai_stack/detect.py),
    so this sandbox commits a pyproject.toml with a [tool.ruff] section (checks) and a
    tests/test_*.py file (regression) -- both ruff and python3 are on PATH wherever
    this suite runs."""

    def setUp(self):
        self.root, self.state = _sandbox(self)
        (self.root / 'pyproject.toml').write_text('[tool.ruff]\nline-length = 100\n')
        (self.root / 'tests').mkdir()
        (self.root / 'tests' / 'test_sample.py').write_text('def test_ok():\n    assert True\n')
        _git(self.root, 'add', '.')
        _git(self.root, 'commit', '-qm', 'add detectable tooling')
        # detect.detect() marks a match 'available' only if its binary is actually on
        # PATH; this test only cares that config detection wires up correctly, not
        # whether ruff/pytest happen to be installed in whatever environment the
        # suite runs under (e.g. CI's quick-test job never installs ruff), so every
        # queried binary is reported present.
        which_patcher = mock.patch('detect.shutil.which', return_value='/usr/bin/true')
        which_patcher.start()
        self.addCleanup(which_patcher.stop)

    def _run(self, **ns):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            gates.cmd_validators(argparse.Namespace(action='propose', **ns))
        return buf.getvalue()

    def test_without_apply_nothing_is_written(self):
        out = self._run(json=False, apply=False)
        self.assertIn('Nothing written', out)
        self.assertFalse((self.state / 'validators.json').exists())

    def test_apply_writes_checks_and_regression(self):
        out = self._run(json=False, apply=True)
        self.assertIn('Applied:', out)
        self.assertIn('checks', out)
        self.assertIn('regression', out)
        config = json.loads((self.state / 'validators.json').read_text())
        self.assertIn('checks', config['validators'])
        self.assertIn('regression', config['validators'])

    def test_a_second_apply_has_nothing_left_to_add(self):
        self._run(json=False, apply=True)
        out = self._run(json=False, apply=True)
        self.assertIn('Applied: nothing to add', out)

    def test_json_output_is_valid_json(self):
        out = self._run(json=True, apply=False)
        report = json.loads(out)
        self.assertIn('rows', report)
        gates_seen = {row['gate'] for row in report['rows']}
        self.assertEqual(gates_seen, {'checks', 'regression'})


class CmdValidateGuardTests(unittest.TestCase):
    """cmd_validate(args) -- guard paths that raise before Codex is invoked."""

    def setUp(self):
        self.root, self.state = _sandbox(self)
        # build_prompt / crg would try to shell out to optional tools; stub them.
        # The reviewer-availability check now lives in providers.py (phase 5:
        # provider abstraction), not in lifecycle.py directly.
        for module in (crg, providers):
            p = mock.patch.object(module.shutil, 'which', return_value=None)
            p.start()
            self.addCleanup(p.stop)

    def _plan(self):
        """Write a real current-plan.json via lifecycle.build_prompt (fast/LOW)."""
        lifecycle.build_prompt(self.root, self.state, 'small change', 'fast', 'HEAD', None)

    def _call(self, name):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            gates.cmd_validate(argparse.Namespace(name=name))
        return buf.getvalue()

    def test_wrong_ai_gate_env_is_rejected(self):
        # AI_GATE unset (stripped in the sandbox) != args.name -> ValueError,
        # caught, printed as a NEEDS_HUMAN verdict, then SystemExit(1).
        with self.assertRaises(SystemExit):
            out = self._call('checks')
            self.assertIn('Run bundled validators through ai pipeline or ai gate', out)
        # And confirm the message really is emitted on stdout.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            gates.cmd_validate(argparse.Namespace(name='checks'))
        self.assertIn('Run bundled validators through ai pipeline or ai gate', buf.getvalue())

    def test_no_current_plan_raises_needs_human(self):
        with mock.patch.dict(os.environ, {'AI_GATE': 'checks'}):
            with self.assertRaises(SystemExit) as ctx:
                self._call('checks')
        self.assertIn('NEEDS_HUMAN: run ai plan', str(ctx.exception))

    def test_security_is_required_by_diff_content_or_contract_risk_notes(self):
        self._plan()
        plan = gates.current_plan(self.state)
        self.assertNotIn('security', core.required_gates(self.root, plan))
        (self.root / 'app.txt').write_text('verify the webhook signature before trusting the payload\n')
        self.assertIn('security', core.required_gates(self.root, plan))
        (self.root / 'app.txt').write_text('initial\n')
        contract = core.task_state(self.state) / 'contracts' / 'current-pr.yml'
        contract.write_text(contract.read_text().replace('risk_notes: []', 'risk_notes: ["afecta a los pagos"]'))
        self.assertIn('security', core.required_gates(self.root, plan))

    def test_gate_not_applicable_to_current_task(self):
        self._plan()
        # fast profile + empty diff => LOW risk => 'review' is not required.
        required = core.required_gates(self.root, gates.current_plan(self.state))
        self.assertNotIn('review', required)
        with mock.patch.dict(os.environ, {'AI_GATE': 'review'}):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
                gates.cmd_validate(argparse.Namespace(name='review'))
        self.assertIn('not applicable to the current task', buf.getvalue())

    def test_missing_stale_prerequisite_gate_record(self):
        self._plan()
        # 'checks' requires prerequisite gate records ('checks','regression') that
        # were never produced -> "Missing or stale prerequisite".
        with mock.patch.dict(os.environ, {'AI_GATE': 'checks'}):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
                gates.cmd_validate(argparse.Namespace(name='checks'))
        self.assertIn('Missing or stale prerequisite', buf.getvalue())
        # Tagged, so the retry streak can tell "never ran" from "ran and failed".
        self.assertEqual(json.loads(buf.getvalue().strip().splitlines()[-1])['blocked_by'], 'checks')

    def test_missing_pr_contract_is_reported(self):
        self._plan()
        # Past the codex-binary check (which() stubbed to a real path, never
        # spawned): with the PR contract removed the next guard fires.
        (core.task_state(self.state) / 'contracts' / 'current-pr.yml').unlink()
        with mock.patch.dict(os.environ, {'AI_GATE': 'cleanup'}), mock.patch.object(gates, 'required_gates', return_value=['cleanup']), \
                mock.patch.object(gates.shutil, 'which', return_value='/usr/bin/true'):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
                gates.cmd_validate(argparse.Namespace(name='cleanup'))
        self.assertIn('PR contract is missing', buf.getvalue())

    def test_missing_codex_binary_is_reported(self):
        self._plan()
        # 'cleanup' has no prerequisite gate records, so execution reaches the
        # shutil.which('codex') check next.
        with mock.patch.dict(os.environ, {'AI_GATE': 'cleanup'}), mock.patch.object(gates, 'required_gates', return_value=['cleanup']), \
                mock.patch.object(gates.shutil, 'which', return_value=None):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
                gates.cmd_validate(argparse.Namespace(name='cleanup'))
        self.assertIn('Codex CLI missing', buf.getvalue())

    def test_configured_reviewer_names_itself_when_missing(self):
        # phase 5: the reviewer is asked from providers.py, so a repository
        # configured for a different reviewer must name THAT one, not "Codex".
        core.save_json(self.state / 'repo.json',
                       {'providers': {'reviewer': 'command', 'reviewer_command': ['/no/such/reviewer-xyz']}})
        self._plan()
        with mock.patch.dict(os.environ, {'AI_GATE': 'cleanup'}), mock.patch.object(gates, 'required_gates', return_value=['cleanup']), \
                mock.patch.object(gates.shutil, 'which', return_value=None):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
                gates.cmd_validate(argparse.Namespace(name='cleanup'))
        self.assertIn('Command CLI missing', buf.getvalue())

    def test_unknown_configured_reviewer_is_needs_human_not_a_crash(self):
        core.save_json(self.state / 'repo.json', {'providers': {'reviewer': 'nonexistent'}})
        self._plan()
        with mock.patch.dict(os.environ, {'AI_GATE': 'cleanup'}), mock.patch.object(gates, 'required_gates', return_value=['cleanup']):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
                gates.cmd_validate(argparse.Namespace(name='cleanup'))
        verdict = json.loads(buf.getvalue())
        self.assertEqual(verdict['status'], 'NEEDS_HUMAN')
        self.assertIn('Unknown reviewer', verdict['findings'][0])


class BundledValidatorTests(unittest.TestCase):
    """summary, cleanup, ponytail and provenance are judged by one reviewer call; each gate
    still records its own verdict, and a stored verdict is used at most once."""

    def setUp(self):
        self.root, self.state = _sandbox(self)
        lifecycle.build_prompt(self.root, self.state, 'small change', 'fast', 'HEAD', None)
        self.task = core.task_state(self.state)
        self.calls = []; self.efforts = []
        passing = {'status': 'PASS', 'evidence': ['app.txt:1 inspected'], 'findings': []}

        def verdict(root, review_dir, name, prompt, schema, checker, timeout, effort=None):
            self.calls.append((name, prompt))
            self.efforts.append(effort)
            # Every bundle response needs a real summary_markdown under 'summary' specifically
            # (check_verdict rejects an empty one on PASS), regardless of which gate triggered.
            per_gate = {gate: {**passing, 'summary_markdown': '# fixture summary' if gate == 'summary' else ''}
                        for gate in schema['required']}
            return {**checker(per_gate), 'usage': {'input_tokens': 9, 'output_tokens': 1}}
        reviewer = types.SimpleNamespace(name='codex', probe_binary='codex', verdict=verdict)
        for patch in (mock.patch.object(gates, 'required_gates', return_value=list(validators.BUNDLE)),
                      mock.patch.object(gates, 'get_reviewer', return_value=reviewer),
                      mock.patch.object(gates.shutil, 'which', return_value='/usr/bin/true')):
            patch.start()
            self.addCleanup(patch.stop)

    def _validate(self, name):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {'AI_GATE': name}), contextlib.redirect_stdout(buf):
            gates.cmd_validate(argparse.Namespace(name=name))
        return json.loads(buf.getvalue().strip().splitlines()[-1])

    def test_one_call_serves_all_four_gates(self):
        # 'summary' is always bundle[0] and, in a real pipeline run, always the trigger --
        # it writes state/pr-summary.md before cleanup/ponytail/provenance ever consume their
        # stored verdicts. This is also the regression check for keying the cache on that
        # file's hash: doing so used to self-invalidate the very next bundle member.
        first = self._validate('summary')
        self.assertEqual(first['usage'], {'input_tokens': 9, 'output_tokens': 1})
        self.assertEqual(first['summary_markdown'], '# fixture summary')
        for name in ('cleanup', 'ponytail', 'provenance'):
            self.assertNotIn('usage', self._validate(name))
        self.assertEqual(len(self.calls), 1)
        name, prompt = self.calls[0]
        self.assertEqual(name, 'bundle')
        self.assertIn('Validate gates: summary, cleanup, ponytail, provenance', prompt)
        self.assertIn('## provenance\n' + validators.INSTRUCTIONS['provenance'], prompt)
        self.assertIn("summary_markdown you return under the 'summary' key IS the generated PR", prompt)

    def test_any_member_can_trigger_the_call(self):
        self._validate('cleanup')
        for name in ('summary', 'ponytail', 'provenance'):
            self.assertNotIn('usage', self._validate(name))
        self.assertEqual(len(self.calls), 1)

    def test_a_rerun_gate_asks_the_reviewer_again(self):
        self._validate('cleanup')
        self._validate('ponytail')
        self._validate('ponytail')
        self.assertEqual(len(self.calls), 2)

    def test_the_bundle_call_keeps_the_default_effort_ponytail_needs(self):
        # summary/cleanup/provenance are 'low' but ponytail has no entry: the one shared
        # call must not shortchange it, so it runs at the default (None).
        with mock.patch.dict(os.environ, {'CODEX_HOME': str(self.root.parent / 'no-codex')}):
            self._validate('cleanup')
        self.assertEqual(self.efforts, [None])

    def test_lowering_ponytail_lets_the_bundle_run_at_the_highest_member_effort(self):
        meta = core.load_json(self.state / 'repo.json', {})
        meta['providers'] = {'reviewer_effort': {'ponytail': 'medium'}}
        core.save_json(self.state / 'repo.json', meta)
        with mock.patch.dict(os.environ, {'CODEX_HOME': str(self.root.parent / 'no-codex')}):
            self._validate('cleanup')
        self.assertEqual(self.efforts, ['medium'])

    def test_a_failed_gate_is_rereviewed_against_its_prior_findings(self):
        core.save_json(self.task / 'gates' / 'cleanup.json',
                       {'passed': False, 'verdict': {'status': 'FAIL', 'findings': ['debug print at app.txt:1']}})
        self._validate('cleanup')
        prompt = self.calls[0][1]
        self.assertIn('Re-review:', prompt)
        self.assertIn('- [cleanup] debug print at app.txt:1', prompt)
        self.assertIn('do not raise new non-blocking issues', prompt)

    def test_a_needs_human_record_is_not_a_prior_finding(self):
        # A missing CLI or a timeout says nothing about the code; re-reviewing against it
        # would only narrow a review that never happened.
        core.save_json(self.task / 'gates' / 'cleanup.json',
                       {'passed': False, 'verdict': {'status': 'NEEDS_HUMAN', 'findings': ['Codex CLI missing']}})
        self._validate('cleanup')
        self.assertNotIn('Re-review:', self.calls[0][1])

    def test_the_reviewer_is_built_for_the_task_profile(self):
        self._validate('cleanup')
        gates.get_reviewer.assert_called_with(self.state, 'fast')

    def test_first_pass_is_told_not_to_hold_findings_back(self):
        self._validate('cleanup')
        self.assertIn('rather than leaving any for a later round', self.calls[0][1])

    def test_contract_changed_files_and_diff_are_inlined(self):
        (self.root / 'app.txt').write_text('edited\n')
        self._validate('cleanup')
        prompt = self.calls[0][1]
        self.assertIn('--- Changed files ---\napp.txt\n', prompt)
        self.assertIn('--- PR contract ---', prompt)
        self.assertIn('--- git diff HEAD ---', prompt)
        self.assertIn('+edited', prompt)

    def test_a_response_missing_a_gate_is_rejected(self):
        passing = {'status': 'PASS', 'evidence': ['x'], 'findings': [], 'summary_markdown': ''}
        with self.assertRaises(ValueError):
            validators.check_bundle({'cleanup': passing}, validators.BUNDLE)


class AssessBundleTests(unittest.TestCase):
    """contract, review (and security when due) share one reviewer call, judging only
    the members still owed a verdict."""

    def setUp(self):
        self.root, self.state = _sandbox(self)
        lifecycle.build_prompt(self.root, self.state, 'small change', 'standard', 'HEAD', None)
        _accept(self.state)
        self.task = core.task_state(self.state)
        self.calls = []
        self.required = ['contract', 'review']
        passing = {'status': 'PASS', 'evidence': ['app.txt:1 inspected'], 'findings': [], 'summary_markdown': ''}

        def verdict(root, review_dir, name, prompt, schema, checker, timeout, effort=None):
            self.calls.append((name, prompt))
            members = schema.get('required', [])
            value = {gate: dict(passing) for gate in members} if 'properties' in schema and members != list(validators.SCHEMA['required']) else dict(passing)
            return {**checker(value), 'usage': {'input_tokens': 9, 'output_tokens': 1}}
        reviewer = types.SimpleNamespace(name='codex', probe_binary='codex', verdict=verdict)
        for patch in (mock.patch.object(gates, 'required_gates', side_effect=lambda *a: list(self.required)),
                      mock.patch.object(gates, 'get_reviewer', return_value=reviewer),
                      mock.patch.object(gates.shutil, 'which', return_value='/usr/bin/true')):
            patch.start()
            self.addCleanup(patch.stop)

    def _validate(self, name):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {'AI_GATE': name}), contextlib.redirect_stdout(buf):
            gates.cmd_validate(argparse.Namespace(name=name))
        return json.loads(buf.getvalue().strip().splitlines()[-1])

    def test_one_call_serves_contract_and_review(self):
        self.assertIn('usage', self._validate('contract'))
        self.assertNotIn('usage', self._validate('review'))
        self.assertEqual(len(self.calls), 1)
        name, prompt = self.calls[0]
        self.assertEqual(name, 'assess')
        self.assertIn('Validate gates: contract, review', prompt)

    def test_security_joins_when_it_is_required(self):
        self.required = ['contract', 'review', 'security']
        self._validate('contract')
        self.assertIn('Validate gates: contract, review, security', self.calls[0][1])

    def _fresh_pass(self, name):
        plan = gates.current_plan(self.state)
        log = self.task / 'gates' / f'{name}-x.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text('ok\n')
        core.save_json(self.task / 'gates' / f'{name}.json', {
            'passed': True, 'verdict': {'status': 'PASS'}, 'log': str(log), 'artifacts': [],
            'command': ['true'], 'exit_code': 0,
            'log_hash': hashlib.sha256(log.read_bytes()).hexdigest(),
            'fingerprint': gates.evidence_fingerprint(self.root, self.state, plan)})

    def test_a_fresh_contract_pass_is_not_rejudged(self):
        for name in ('checks', 'regression', 'contract'): self._fresh_pass(name)
        self._validate('review')
        name, prompt = self.calls[0]
        self.assertEqual(name, 'review')
        self.assertIn('Validate gate: review', prompt)

    def test_review_consumes_the_shared_verdict_after_contract_passed(self):
        # The real pipeline order: contract triggers the shared call, run_gate records its
        # PASS, and only then does review run -- by which time contract has left the set of
        # members owed a verdict. Review must still take its stored verdict, not call again.
        self.required = ['checks', 'regression', 'contract', 'review']
        for name in ('checks', 'regression'): self._fresh_pass(name)
        self._validate('contract')
        self._fresh_pass('contract')
        self.assertNotIn('usage', self._validate('review'))
        self.assertEqual([name for name, _ in self.calls], ['assess'])

    def test_every_member_consumes_the_shared_verdict_as_the_others_pass(self):
        # With security also due, review is left with a partner once contract passes, and
        # security is left alone once review passes; both must still hit the one shared call.
        self.required = ['checks', 'regression', 'contract', 'review', 'security']
        for name in ('checks', 'regression'): self._fresh_pass(name)
        self._validate('contract'); self._fresh_pass('contract')
        self.assertNotIn('usage', self._validate('review')); self._fresh_pass('review')
        self.assertNotIn('usage', self._validate('security'))
        self.assertEqual([name for name, _ in self.calls], ['assess'])
        self.assertIn('Validate gates: contract, review, security', self.calls[0][1])

    def test_a_lone_member_runs_on_its_own(self):
        self.required = ['contract']
        for name in ('checks', 'regression'): self._fresh_pass(name)
        self._validate('contract')
        self.assertEqual(self.calls[0][0], 'contract')


class InlineEvidenceTests(unittest.TestCase):
    """inline_evidence() must never push a prompt over its context budget."""

    def setUp(self):
        self.root, self.state = _sandbox(self)
        self.plan = {'scope': {'base': 'HEAD'}}
        self.contract = self.root.parent / 'contract.yml'
        self.contract.write_text('objective: x\nacceptance:\n  - y\n')

    def test_nothing_fits_returns_empty(self):
        (self.root / 'app.txt').write_text('edited\n')
        self.assertEqual(gates.inline_evidence(self.root, self.plan, self.contract, 50), '')

    def test_an_oversized_diff_is_dropped_whole_but_the_file_list_stays(self):
        (self.root / 'app.txt').write_text('x' * 5000 + '\n')
        text = gates.inline_evidence(self.root, self.plan, self.contract, 1000)
        self.assertIn('--- Changed files ---\napp.txt', text)
        self.assertIn('--- PR contract ---', text)
        self.assertNotIn('--- git diff', text)
        self.assertLessEqual(len(text), 1000)


class RunGateFingerprintReuseTests(unittest.TestCase):
    """run_gate()/cmd_gate() accept a precomputed `before` fingerprint -- the
    optimization cmd_pipeline relies on to avoid a third full evidence_fingerprint()
    walk per gate (its own --resume freshness check, run_gate's `before`, run_gate's
    `after`). Must never weaken the before/after tamper check itself."""

    def setUp(self):
        self.root, self.state = _sandbox(self)
        lifecycle.build_prompt(self.root, self.state, 'small change', 'fast', 'HEAD', None)
        self.task = core.task_state(self.state)
        self.plan = gates.current_plan(self.state)
        _accept(self.state)

    def _args(self):
        # 'checks' is always in GATES[:7], so it's required regardless of profile.
        return argparse.Namespace(name='checks', timeout=30, adapter='exit-code', evidence='ok')

    def _counting_fingerprint(self):
        calls = []
        real = gates.evidence_fingerprint

        def counting(*a, **k):
            calls.append(1)
            return real(*a, **k)
        return calls, counting

    def test_precomputed_before_is_used_instead_of_recomputed(self):
        before = gates.evidence_fingerprint(self.root, self.state, self.plan)
        calls, counting = self._counting_fingerprint()
        with mock.patch.object(gates, 'evidence_fingerprint', side_effect=counting):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                gates.run_gate(self._args(), self.root, self.state, self.plan, self.task, ['true'], before)
        # Only the `after` measurement ran; `before` was supplied, not recomputed.
        self.assertEqual(len(calls), 1)
        self.assertIn('PASS', buf.getvalue())

    def test_without_before_it_is_computed_twice(self):
        calls, counting = self._counting_fingerprint()
        with mock.patch.object(gates, 'evidence_fingerprint', side_effect=counting):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                gates.run_gate(self._args(), self.root, self.state, self.plan, self.task, ['true'])
        self.assertEqual(len(calls), 2)
        self.assertIn('PASS', buf.getvalue())

    def test_precomputed_before_still_detects_mutation_during_the_gate(self):
        # The optimization must not open a hole in the tamper check: a command that
        # writes into the repo must still fail the gate even when `before` came from
        # the caller instead of being recomputed inside run_gate.
        before = gates.evidence_fingerprint(self.root, self.state, self.plan)
        mutate = ['bash', '-c', 'echo mutated > scratch-during-gate.txt']
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            gates.run_gate(self._args(), self.root, self.state, self.plan, self.task, mutate, before)
        out = buf.getvalue()
        self.assertIn('FAIL', out)
        self.assertIn('Repository or task changed during gate', out)
        record = core.load_json(self.task / 'gates/checks.json', {})
        self.assertFalse(record['passed'])

    def test_cmd_pipeline_reuses_its_own_fingerprint_for_the_gate_before(self):
        # End-to-end proof at the cmd_pipeline level (not just run_gate directly):
        # the fingerprint cmd_pipeline computes for its --resume check is the exact
        # value the gate it launches uses as `before`, with no separate recomputation.
        config = {'version': 1, 'validators': {
            'checks': {'command': ['true'], 'adapter': 'exit-code', 'evidence': 'ok', 'timeout': 30}}}
        core.save_json(self.state / 'validators.json', config)
        calls, counting = self._counting_fingerprint()
        with mock.patch.object(gates, 'evidence_fingerprint', side_effect=counting), \
                mock.patch.object(gates, 'required_gates', return_value=['checks']), \
                mock.patch.object(gates, 'cmd_ready'):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                gates.cmd_pipeline(argparse.Namespace(dry_run=False, resume=False, allow_overrun=False,
                                                       force_unlock=False))
        # One computation for cmd_pipeline's own --resume check (reused as `before`),
        # one for run_gate's `after` -- never a third, separate `before`.
        self.assertEqual(len(calls), 2)


class PipelineValidatorConfigChangeTests(unittest.TestCase):
    """cmd_pipeline() re-reads validator_config() before every gate in `required` and
    stops with NEEDS_HUMAN the moment it differs from what the run started with
    (gates.py, inside the `for name in required` loop) -- this is what protects a
    multi-gate pipeline from finishing a run against config a prior gate silently
    changed (the subprocess-level version of this is
    tests/test_workflow.py::test_a_validator_that_mutates_validators_json_fails_its_own_gate,
    which goes through the persistent-write path instead of a mock)."""

    def setUp(self):
        self.root, self.state = _sandbox(self)
        lifecycle.build_prompt(self.root, self.state, 'small change', 'fast', 'HEAD', None)
        _accept(self.state)
        self.config = {'version': 1, 'validators': {
            'checks': {'command': ['true'], 'adapter': 'exit-code', 'evidence': 'ok', 'timeout': 30},
            'regression': {'command': ['true'], 'adapter': 'exit-code', 'evidence': 'ok', 'timeout': 30}}}
        core.save_json(self.state / 'validators.json', self.config)
        self.changed = {'version': 1, 'validators': {
            'checks': self.config['validators']['checks'],
            'regression': {'command': ['false'], 'adapter': 'exit-code', 'evidence': 'ok', 'timeout': 30}}}

    def test_config_changing_mid_run_stops_the_pipeline(self):
        # Call order inside cmd_pipeline: (1) its own top-of-function read into
        # `config`, (2) the loop's pre-gate check for 'checks' (must still match, so
        # that gate runs), (3) the loop's pre-gate check for 'regression' (now
        # different -> NEEDS_HUMAN before 'regression' ever runs).
        side_effect = [self.config, self.config, self.changed]
        with mock.patch.object(gates, 'validator_config', side_effect=side_effect), \
                mock.patch.object(gates, 'required_gates', return_value=['checks', 'regression']), \
                mock.patch.object(gates, 'cmd_ready') as ready:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit) as caught:
                gates.cmd_pipeline(argparse.Namespace(dry_run=False, resume=False, allow_overrun=False,
                                                       force_unlock=False))
            ready.assert_not_called()
        self.assertIn('NEEDS_HUMAN: validator configuration changed; rerun pipeline.', str(caught.exception))
        record = core.load_json(core.task_state(self.state) / 'gates/checks.json', {})
        self.assertTrue(record.get('passed'), 'the gate before the config change should still have run and passed')
        self.assertFalse((core.task_state(self.state) / 'gates/regression.json').exists())

    def test_unchanged_config_runs_the_whole_pipeline(self):
        with mock.patch.object(gates, 'validator_config', return_value=self.config), \
                mock.patch.object(gates, 'required_gates', return_value=['checks', 'regression']), \
                mock.patch.object(gates, 'cmd_ready') as ready:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                gates.cmd_pipeline(argparse.Namespace(dry_run=False, resume=False, allow_overrun=False,
                                                       force_unlock=False))
            ready.assert_called_once()


class BundledValidatorEngineTests(unittest.TestCase):
    """validators.json stores an absolute release path for a bundled validator; installs
    retain old releases, so running that path after an upgrade silently used old code."""

    def setUp(self):
        self.root, self.state = _sandbox(self)
        lifecycle.build_prompt(self.root, self.state, 'small change', 'fast', 'HEAD', None)
        _accept(self.state)

    def test_a_builtin_resolves_to_the_running_engine_and_a_custom_command_is_untouched(self):
        stale = {'command': ['/old/python', '/old/release-x/ai_stack/cli.py', 'validate', 'cleanup'], 'builtin': 'cleanup'}
        self.assertEqual(gates.resolved_command(stale),
                         [sys.executable, str(ROOT / 'ai_stack/cli.py'), 'validate', 'cleanup'])
        self.assertEqual(gates.resolved_command({'command': ['npm', 'test']}), ['npm', 'test'])

    def test_the_pipeline_runs_and_reuses_evidence_with_the_resolved_command(self):
        stale = ['/old/python', '/old/release-x/ai_stack/cli.py', 'validate', 'cleanup']
        core.save_json(self.state / 'validators.json', {'version': 1, 'validators': {
            'cleanup': {'command': stale, 'adapter': 'json', 'timeout': 600, 'evidence': None, 'builtin': 'cleanup'}}})
        with mock.patch.object(gates, 'required_gates', return_value=['cleanup']), \
                mock.patch.object(gates, 'cmd_gate') as gate, mock.patch.object(gates, 'cmd_ready'), \
                contextlib.redirect_stdout(io.StringIO()):
            gates.cmd_pipeline(argparse.Namespace(dry_run=False, resume=False, allow_overrun=False, force_unlock=False))
        self.assertEqual(gate.call_args.args[0].command, gates.resolved_command({'builtin': 'cleanup'}))


class RetryBudgetTests(unittest.TestCase):
    """run_gate() enforces the profile's retry cap, which used to be advice rendered
    into the builder's prompt and nothing else -- so a gate could thrash indefinitely
    against one unresolved failure, spending a full model call every attempt."""

    def setUp(self):
        self.root, self.state = _sandbox(self)
        lifecycle.build_prompt(self.root, self.state, 'small change', 'fast', 'HEAD', None)
        self.task = core.task_state(self.state)
        self.plan = gates.current_plan(self.state)
        _accept(self.state)
        # fast profile: retries == 1, so attempt 1 and one retry may run.
        self.assertEqual(self.plan['caps']['retries'], 1)

    def _run(self, command, allow_overrun=False):
        """Run one gate attempt. A failing gate exits 1, which is the outcome under
        test here, so only the retry-cap refusal (its own SystemExit) propagates."""
        args = argparse.Namespace(name='checks', timeout=30, adapter='exit-code', evidence='ok',
                                  allow_overrun=allow_overrun)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                gates.run_gate(args, self.root, self.state, self.plan, self.task, command)
        except SystemExit as exc:
            if exc.code != 1: raise
        return buf.getvalue()

    def test_a_failing_gate_is_stopped_once_past_the_retry_budget(self):
        self.assertIn('FAIL', self._run(['false']))
        self.assertIn('FAIL', self._run(['false']))
        with self.assertRaises(SystemExit) as ctx:
            self._run(['false'])
        message = str(ctx.exception)
        self.assertIn('NEEDS_HUMAN', message)
        self.assertIn('failed 2 time(s) in a row', message)
        self.assertIn('--allow-overrun', message)

    def test_allow_overrun_runs_the_blocked_attempt_anyway(self):
        self._run(['false'])
        self._run(['false'])
        self.assertIn('PASS', self._run(['true'], allow_overrun=True))

    def test_a_pass_resets_the_streak(self):
        self._run(['false'])
        self.assertEqual(gates.consecutive_gate_failures(self.state, self.task.name, 'checks'), 1)
        self._run(['true'])
        self.assertEqual(gates.consecutive_gate_failures(self.state, self.task.name, 'checks'), 0)
        # Back to a full budget: two more failures are allowed before the cap bites.
        self._run(['false'])
        self.assertIn('FAIL', self._run(['false']))

    def test_a_changed_state_starts_a_fresh_streak(self):
        self._run(['false'])
        self._run(['false'])
        # A fix (any change to the evidence state) is not the same unresolved failure.
        (self.root / 'fix.txt').write_text('changed\n')
        self.assertIn('FAIL', self._run(['false']))
        self.assertIn('FAIL', self._run(['false']))
        with self.assertRaises(SystemExit) as ctx:
            self._run(['false'])
        self.assertIn('failed 2 time(s) in a row', str(ctx.exception))

    def test_prerequisite_blocked_attempts_do_not_count(self):
        # The contract gate blocked twice on stale checks evidence: it never ran,
        # so its budget is untouched once the prerequisite is fixed.
        fingerprint = gates.evidence_fingerprint(self.root, self.state, self.plan)
        for _ in range(3):
            gates.record_metric(self.state, 'gate', gate='checks', passed=False,
                                  fingerprint=fingerprint, blocked_by='regression')
        self.assertEqual(gates.consecutive_gate_failures(self.state, self.task.name, 'checks',
                                                         fingerprint=fingerprint), 0)
        self.assertIn('FAIL', self._run(['false']))

    def test_rows_without_a_fingerprint_end_the_streak(self):
        # Failures recorded before fingerprints were: they cannot be tied to this state.
        for _ in range(3):
            gates.record_metric(self.state, 'gate', gate='checks', passed=False)
        self.assertEqual(gates.consecutive_gate_failures(self.state, self.task.name, 'checks'), 3)
        self.assertIn('FAIL', self._run(['false']))


class SameStateRerunTests(unittest.TestCase):
    """A model-judged gate that FAILed is not re-run against the very same state."""

    FAIL = ['sh', '-c', 'echo \'{"status":"FAIL","evidence":[],"findings":["a real finding"]}\'']

    def setUp(self):
        self.root, self.state = _sandbox(self)
        lifecycle.build_prompt(self.root, self.state, 'small change', 'standard', 'HEAD', None)
        self.task = core.task_state(self.state)
        self.plan = gates.current_plan(self.state)

    def _run(self, name, command, adapter='json', allow_overrun=False):
        args = argparse.Namespace(name=name, timeout=30, adapter=adapter, evidence='ok',
                                  allow_overrun=allow_overrun)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                gates.run_gate(args, self.root, self.state, self.plan, self.task, command)
        except SystemExit as exc:
            if exc.code != 1: raise
        return buf.getvalue()

    def test_a_failed_model_gate_is_refused_on_unchanged_state(self):
        self.assertIn('FAIL', self._run('review', self.FAIL))
        with self.assertRaises(SystemExit) as ctx:
            self._run('review', self.FAIL)
        self.assertIn('nothing changed since review failed', str(ctx.exception))

    def test_a_change_or_an_override_lets_it_run_again(self):
        self._run('review', self.FAIL)
        self.assertIn('FAIL', self._run('review', self.FAIL, allow_overrun=True))
        (self.root / 'fix.txt').write_text('fixed\n')
        self.assertIn('FAIL', self._run('review', self.FAIL))

    def test_a_model_gate_stops_after_its_round_limit_even_on_new_code(self):
        # standard allows 4 FAIL verdicts per gate per task; each new version resets the
        # streak but not the rounds, so the 5th attempt asks a human to decide the scope.
        limit = self.plan['caps']['model_rounds']
        for i in range(limit):
            (self.root / 'round.txt').write_text(f'round {i}\n')
            self.assertIn('FAIL', self._run('review', self.FAIL))
        (self.root / 'round.txt').write_text('another version\n')
        with self.assertRaises(SystemExit) as ctx:
            self._run('review', self.FAIL)
        self.assertIn(f'returned FAIL {limit} times on this task', str(ctx.exception))
        self.assertIn('FAIL', self._run('review', self.FAIL, allow_overrun=True))

    def test_a_reviewer_crash_is_not_a_round(self):
        crash = ['sh', '-c', 'echo \'{"status":"NEEDS_HUMAN","evidence":[],"findings":["reviewer exited 1"]}\'']
        for _ in range(2): self._run('review', crash)  # the retry streak still stops a third
        self.assertEqual(gates.gate_fail_verdicts(self.state, self.task.name, 'review'), 0)

    def test_exit_code_gates_may_rerun_unchanged(self):
        # checks/regression are cheap and may be flaky; only model verdicts are cached.
        self._run('checks', ['false'], adapter='exit-code')
        self.assertIn('FAIL', self._run('checks', ['false'], adapter='exit-code'))

    def test_a_command_that_cannot_run_is_an_environment_problem(self):
        missing = ['sh', '-c', 'exit 127']
        out = self._run('checks', missing, adapter='exit-code')
        self.assertIn('environment problem', out)
        for _ in range(3): self._run('checks', missing, adapter='exit-code')
        self.assertEqual(gates.consecutive_gate_failures(self.state, self.task.name, 'checks'), 0)


class BuilderVariantMetricTests(unittest.TestCase):
    """Every gate row carries the task's builder-policy variant for `ai prompt report`."""

    def test_gate_rows_record_the_builder_variant(self):
        root, state = _sandbox(self)
        core.save_json(state / 'prompt-experiments.json', {'version': 1, 'active': {
            'slot': 'builder.policy', 'variants': ['b'], 'started_at': 0, 'min_samples_per_variant': 1}})
        lifecycle.build_prompt(root, state, 'small change', 'fast', 'HEAD', None)
        task = core.task_state(state); plan = gates.current_plan(state)
        args = argparse.Namespace(name='checks', timeout=30, adapter='exit-code', evidence='ok', allow_overrun=False)
        with contextlib.redirect_stdout(io.StringIO()):
            gates.run_gate(args, root, state, plan, task, ['true'])
        rows = [json.loads(line) for line in (state / 'metrics.jsonl').read_text().splitlines()]
        gate = [r for r in rows if r['event'] == 'gate'][-1]
        self.assertEqual(gate['prompt_variants']['builder.policy']['variant'], 'b')


class InspectionLineTests(unittest.TestCase):
    """The reviewer is told not to re-fetch a diff that is already inlined."""

    def test_inlined_diff_says_do_not_rerun_git_diff(self):
        prompt = gates.with_inspection(f'x {gates.INSPECT_MARKER} y', '--- git diff main ---\n+a\n', 'main')
        self.assertIn('do not re-run git diff', prompt)
        self.assertNotIn(gates.INSPECT_MARKER, prompt)

    def test_without_the_diff_it_still_asks_to_inspect_it(self):
        prompt = gates.with_inspection(f'{gates.INSPECT_MARKER}', '--- Changed files ---\na\n', 'main')
        self.assertIn('Inspect git diff against the base', prompt)


class ContractPathConstraintTests(unittest.TestCase):
    """The contract gate decides a path-shaped must_not_change entry itself, without
    spending a model call to be told what the diff plainly says."""

    def setUp(self):
        self.root, self.state = _sandbox(self)
        for module in (crg, providers):
            p = mock.patch.object(module.shutil, 'which', return_value=None)
            p.start()
            self.addCleanup(p.stop)
        lifecycle.build_prompt(self.root, self.state, 'small change', 'fast', 'HEAD', None)
        self.task = core.task_state(self.state)

    def _contract(self, must_not_change):
        path = self.task / 'contracts/current-pr.yml'
        path.write_text('objective: "small change"\n'
                        'acceptance: ["it does the thing"]\n'
                        f'must_not_change: {json.dumps(must_not_change)}\n'
                        'risk_notes: []\ndesign:\n  enabled: false\n')

    def _validate(self):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {'AI_GATE': 'contract'}), \
                contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            gates.cmd_validate(argparse.Namespace(name='contract'))
        return json.loads(buf.getvalue().strip().splitlines()[-1])

    def test_touching_a_forbidden_path_fails_without_a_model_call(self):
        (self.root / 'app.txt').write_text('changed\n')
        self._contract(['app.txt'])
        verdict = self._validate()
        # The sandbox has no reviewer CLI, so reaching the reviewer at all would have
        # produced NEEDS_HUMAN. A FAIL proves the deterministic check decided it first.
        self.assertEqual(verdict['status'], 'FAIL')
        self.assertIn('app.txt', verdict['findings'][0])

    def test_prose_constraints_and_untouched_paths_reach_the_validator(self):
        (self.root / 'app.txt').write_text('changed\n')
        self._contract(['the public API', 'docs/'])
        # Nothing deterministic fires, so cmd_validate proceeds far enough to hit the
        # missing-reviewer guard -- i.e. it did NOT short-circuit to a FAIL.
        verdict = self._validate()
        self.assertEqual(verdict['status'], 'NEEDS_HUMAN')


if __name__ == '__main__':
    unittest.main()
