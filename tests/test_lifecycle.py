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
import core  # noqa: E402
import crg  # noqa: E402
import lifecycle  # noqa: E402
import providers  # noqa: E402


class LifecyclePureTests(unittest.TestCase):
    """Functions that only need a tmp dir / tmp files (no git, no chdir)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='lc-pure-')
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    # --- rules_text -----------------------------------------------------
    def test_rules_text_none_when_file_absent(self):
        # No state/rules.json at all -> the literal "(none)".
        self.assertEqual(lifecycle.rules_text(self.dir), '(none)')

    def test_rules_text_one_line_per_rule_with_scope_fallback(self):
        # Each rule renders as "- [scope] rule"; missing scope falls back to **.
        (self.dir / 'rules.json').write_text(json.dumps([
            {'rule': 'no raw SQL', 'scope': 'src/**'},
            {'rule': 'no console.log'},
        ]))
        self.assertEqual(
            lifecycle.rules_text(self.dir),
            '- [src/**] no raw SQL\n- [**] no console.log',
        )

    # --- populate_acceptance_if_empty ---------------------------------
    def test_populate_acceptance_noop_on_empty_items(self):
        # items == [] -> False and the file is left byte-for-byte untouched.
        p = self.dir / 'c.yml'
        p.write_text('acceptance: []\n')
        self.assertFalse(lifecycle.populate_acceptance_if_empty(p, []))
        self.assertEqual(p.read_text(), 'acceptance: []\n')

    def test_populate_acceptance_fills_a_truly_empty_list(self):
        # A blank "acceptance: []" line gets the detected items as a JSON list.
        p = self.dir / 'c.yml'
        p.write_text('objective: "x"\nacceptance: []\nmust_not_change: []\n')
        self.assertTrue(lifecycle.populate_acceptance_if_empty(p, ['does A', 'does B']))
        self.assertIn('acceptance: ["does A", "does B"]', p.read_text())
        self.assertIn('must_not_change: []', p.read_text())

    def test_populate_acceptance_never_overwrites_a_human_list(self):
        # The emptiness regex only matches a truly empty list, so a populated one is safe.
        p = self.dir / 'c.yml'
        original = 'acceptance: ["already here"]\n'
        p.write_text(original)
        self.assertFalse(lifecycle.populate_acceptance_if_empty(p, ['new item']))
        self.assertEqual(p.read_text(), original)

    def test_populate_acceptance_survives_non_ascii_items(self):
        # Regression: json.dumps() escapes non-ASCII as \uXXXX by default, and re.sub()
        # interprets a plain string repl as its own backslash-escape template - a bare
        # accented word ("función") used to raise "re.error: bad escape \u".
        p = self.dir / 'c.yml'
        p.write_text('acceptance: []\n')
        items = ['La función suma(a, b) sigue funcionando', 'Añade soporte para ñ']
        self.assertTrue(lifecycle.populate_acceptance_if_empty(p, items))
        self.assertIn('acceptance: ' + json.dumps(items), p.read_text())

    # --- semantic_fingerprint ---------------------------------------
    def test_semantic_fingerprint_hashes_only_present_files(self):
        (self.dir / 'package.json').write_text('{"name":"x"}')
        (self.dir / 'pyproject.toml').write_text('[project]\nname = "x"\n')
        fp = lifecycle.semantic_fingerprint(self.dir)
        self.assertEqual(set(fp), {'package.json', 'pyproject.toml'})
        for value in fp.values():
            self.assertRegex(value, r'^[0-9a-f]{16}$')
        # An absent manifest is simply absent from the dict.
        self.assertNotIn('go.mod', fp)

    def test_semantic_fingerprint_tracks_file_bytes(self):
        (self.dir / 'package.json').write_text('{"name":"x"}')
        before = lifecycle.semantic_fingerprint(self.dir)['package.json']
        (self.dir / 'package.json').write_text('{"name":"y"}')
        self.assertNotEqual(before, lifecycle.semantic_fingerprint(self.dir)['package.json'])

    # --- task_cache_key -------------------------------------------------
    def test_task_cache_key_is_deterministic_and_24_chars(self):
        a = lifecycle.task_cache_key(self.dir, 'do X', 'fast', 'HEAD', ['s1'])
        b = lifecycle.task_cache_key(self.dir, 'do X', 'fast', 'HEAD', ['s1'])
        self.assertEqual(a, b)
        self.assertEqual(len(a), 24)

    def test_task_cache_key_varies_with_every_input(self):
        base = lifecycle.task_cache_key(self.dir, 'do X', 'fast', 'HEAD', ['s1'])
        self.assertNotEqual(base, lifecycle.task_cache_key(self.dir, 'do Y', 'fast', 'HEAD', ['s1']))
        self.assertNotEqual(base, lifecycle.task_cache_key(self.dir, 'do X', 'strict', 'HEAD', ['s1']))
        self.assertNotEqual(base, lifecycle.task_cache_key(self.dir, 'do X', 'fast', 'HEAD', ['s2']))
        # A change to a fingerprinted manifest also moves the key.
        (self.dir / 'package.json').write_text('{}')
        self.assertNotEqual(base, lifecycle.task_cache_key(self.dir, 'do X', 'fast', 'HEAD', ['s1']))


class LifecycleSandboxTests(unittest.TestCase):
    """Targets that reach core.git_root()/task_state() need a real git repo + cwd."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='lc-sandbox-')
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        self.repo = home / 'repo'
        self.repo.mkdir()
        self.cfg = home / 'config' / 'ai-agent-stack'

        env = {k: v for k, v in os.environ.items() if k not in ('AI_GATE', 'AI_TASK_DIR')}
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'), AI_TASK_ID='lc-task')

        for args in (('init', '-q'), ('config', 'user.name', 'T'), ('config', 'user.email', 't@e.com')):
            subprocess.run(['git', *args], cwd=self.repo, check=True, capture_output=True)
        (self.repo / 'app.txt').write_text('initial\n')
        subprocess.run(['git', 'add', '.'], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-qm', 'init'], cwd=self.repo, check=True, capture_output=True)

        # Redirect env + external state root; fake away every external binary.
        # The builder/reviewer availability check now lives in providers.py (phase 5:
        # provider abstraction), not in lifecycle.py directly, so that module joins
        # crg here.
        envp = mock.patch.dict(os.environ, env, clear=True)
        envp.start()
        self.addCleanup(envp.stop)
        cfgp = mock.patch.object(core, 'CONFIG_ROOT', self.cfg)
        cfgp.start()
        self.addCleanup(cfgp.stop)
        for module in (crg, providers):
            whichp = mock.patch.object(module.shutil, 'which', return_value=None)
            whichp.start()
            self.addCleanup(whichp.stop)

        old_cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, old_cwd)

    # --- ensure_contract ---------------------------------------------
    def test_ensure_contract_creates_pr_contract(self):
        state = Path(self.tmp.name) / 'st1'
        state.mkdir()
        lifecycle.ensure_contract(state, 'Add a retry', None)
        ts = core.task_state(state)
        text = (ts / 'contracts' / 'current-pr.yml').read_text()
        self.assertIn('objective: "Add a retry"', text)
        self.assertIn('acceptance: []', text)
        self.assertIn('enabled: false', text)
        self.assertFalse((ts / 'contracts' / 'current-design.yml').exists())

    def test_ensure_contract_with_figma_writes_design_and_enables(self):
        state = Path(self.tmp.name) / 'st2'
        state.mkdir()
        lifecycle.ensure_contract(state, 'Build the UI', 'https://figma.com/x')
        ts = core.task_state(state)
        self.assertIn('enabled: true', (ts / 'contracts' / 'current-pr.yml').read_text())
        design = ts / 'contracts' / 'current-design.yml'
        self.assertTrue(design.is_file())
        self.assertIn('figma.com/x', design.read_text())

    def test_ensure_contract_rewrites_objective_only_keeping_human_acceptance(self):
        state = Path(self.tmp.name) / 'st3'
        state.mkdir()
        lifecycle.ensure_contract(state, 'First task', None)
        pr = core.task_state(state) / 'contracts' / 'current-pr.yml'
        pr.write_text(pr.read_text().replace('acceptance: []', 'acceptance: ["human wrote this"]'))
        lifecycle.ensure_contract(state, 'Second task', None)
        text = pr.read_text()
        self.assertIn('objective: "Second task"', text)
        self.assertNotIn('First task', text)
        self.assertIn('acceptance: ["human wrote this"]', text)

    def test_ensure_contract_rewrites_objective_with_non_ascii_text(self):
        # Regression: same re.sub()-repl-is-a-template pitfall as populate_acceptance_if_empty -
        # rewriting the objective on an existing contract used to crash on an accented task string.
        state = Path(self.tmp.name) / 'st3b'
        state.mkdir()
        lifecycle.ensure_contract(state, 'First task', None)
        task = 'agregar función resta a app.py'
        lifecycle.ensure_contract(state, task, None)
        pr = core.task_state(state) / 'contracts' / 'current-pr.yml'
        self.assertIn(f'objective: {json.dumps(task)}', pr.read_text())

    # --- cmd_handoff -------------------------------------------------
    def test_cmd_handoff_writes_payload_within_budget_and_prints_it(self):
        ns = argparse.Namespace(task='wip work', state=None, profile='fast',
                                base='HEAD', evidence=[], next='inspect')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            lifecycle.cmd_handoff(ns)
        self.assertIn('Handoff:', buf.getvalue())
        state = core.repo_state(self.repo)
        handoffs = list((core.task_state(state) / 'handoffs').glob('handoff-*.json'))
        self.assertEqual(len(handoffs), 1)
        payload = json.loads(handoffs[0].read_text())
        self.assertEqual(payload['task'], 'wip work')
        self.assertEqual(payload['next_action'], 'inspect')
        self.assertEqual(payload['state'], 'in_progress')
        self.assertEqual(payload['profile'], 'fast')

    # --- cmd_ticket ------------------------------------------------------
    def test_cmd_ticket_writes_state_ticket_json(self):
        ns = argparse.Namespace(file=None, text='## Acceptance Criteria\n- does X\n', json=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            lifecycle.cmd_ticket(ns)
        printed = json.loads(buf.getvalue())
        self.assertEqual(printed['acceptance_items'], ['does X'])
        state = core.repo_state(self.repo)
        snapshot = json.loads((core.task_state(state) / 'state' / 'ticket.json').read_text())
        self.assertEqual(snapshot['acceptance_items'], ['does X'])
        self.assertEqual(snapshot['source'], 'pasted')

    # --- build_prompt (optional; kept because it runs cleanly in the sandbox) ---
    def test_build_prompt_writes_run_md_plan_and_contract(self):
        state = core.repo_state(self.repo)
        prompt = lifecycle.build_prompt(self.repo, state, 'small change', 'fast', 'HEAD', None)
        self.assertIn('# AI Agent Stack orchestration', prompt)
        self.assertIn('Task: small change', prompt)
        task = core.task_state(state)
        self.assertTrue((task / 'state' / 'current-run.md').is_file())
        plan = json.loads((task / 'state' / 'current-plan.json').read_text())
        self.assertEqual(plan['task'], 'small change')
        self.assertEqual(plan['profile'], 'fast')
        self.assertIn('objective: "small change"', (task / 'contracts' / 'current-pr.yml').read_text())


if __name__ == '__main__':
    unittest.main()
