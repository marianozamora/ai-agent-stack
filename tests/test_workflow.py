import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installer', ROOT / 'ai_stack/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

sys.path.insert(0, str(ROOT / 'ai_stack'))
import core  # noqa: E402
import lifecycle  # noqa: E402
import skills  # noqa: E402


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='stack-test-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.repo = self.home / 'repo'
        self.repo.mkdir()
        # Strip any ambient AI_GATE/AI_TASK_DIR (e.g. this suite running as a validator
        # command itself, under `ai gate checks -- pytest`) so tests start from a clean,
        # reproducible "outside a gate" baseline regardless of how they were invoked.
        base_env = {k: v for k, v in os.environ.items() if k not in ('AI_GATE', 'AI_TASK_DIR')}
        self.env = dict(base_env, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / 'config'), AI_TASK_ID='one')
        for args in [('init', '-q'), ('config', 'user.name', 'Test'), ('config', 'user.email', 'test@example.com')]:
            self.git(*args)
        (self.repo / 'app.txt').write_text('initial\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'initial')

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.repo, env=self.env, text=True).strip()

    def ai(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(ROOT / 'ai_stack/cli.py'), *args], cwd=self.repo, env=self.env, text=True, capture_output=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def plan(self, *args):
        self.ai('plan', 'small change', '--profile', 'fast', '--base', 'HEAD', *args)

    def pass_gates(self):
        for gate in ('checks', 'regression', 'cleanup', 'provenance', 'ponytail', 'summary', 'contract'):
            self.ai('gate', gate, '--', sys.executable, '-c', 'import json; print(json.dumps({"status":"PASS","evidence":["verified fixture"]}))')

    def test_readiness_requires_fresh_evidence(self):
        self.plan()
        self.assertIn('NEEDS_HUMAN', self.ai('ready', ok=False))
        self.pass_gates()
        self.assertIn('PR_READY', self.ai('ready'))
        (self.repo / 'app.txt').write_text('changed\n')
        self.assertIn('NEEDS_HUMAN', self.ai('ready', ok=False))

    def test_failure_and_modified_gate_are_not_passes(self):
        self.plan()
        self.pass_gates()
        self.ai('gate', 'checks', '--', sys.executable, '-c', 'raise SystemExit(2)', ok=False)
        self.assertIn('FAILED', self.ai('ready', ok=False))
        output = self.ai('gate', 'checks', '--', sys.executable, '-c', 'open("app.txt","w").write("changed")', ok=False)
        self.assertIn('changed during gate', output)

    def test_untracked_changes_invalidate_evidence(self):
        self.plan()
        self.pass_gates()
        (self.repo / 'new.txt').write_text('untracked')
        self.assertIn('NEEDS_HUMAN', self.ai('ready', ok=False))

    def test_missing_or_tampered_log_invalidates_gate(self):
        self.plan()
        self.pass_gates()
        state = Path(self.ai('path').strip())
        record = json.loads((state / 'gates/checks.json').read_text())
        Path(record['log']).write_text('changed evidence')
        self.assertIn('NEEDS_HUMAN', self.ai('ready', ok=False))

    def test_rules_invalidate_evidence(self):
        self.plan()
        self.pass_gates()
        self.ai('rules', 'add', 'New acceptance rule')
        self.assertIn('NEEDS_HUMAN', self.ai('ready', ok=False))

    def test_doctor_reports_status_and_corrupt_state_files(self):
        self.plan()
        clean = self.ai('doctor')
        self.assertIn('State files: PASS', clean)
        self.assertIn('Zero-footprint: PASS', clean)

        state_root = Path(self.ai('path').strip()).parents[1]
        (state_root / 'rules.json').write_text('{"not":"closed"')
        corrupt = self.ai('doctor')
        self.assertIn('State files: CORRUPT (1)', corrupt)
        self.assertIn('rules.json', corrupt)
        # A corrupt rules.json still doesn't crash the rest of the command,
        # and load_json() keeps degrading it to [] rather than raising.
        self.assertIn('Zero-footprint: PASS', corrupt)

    def test_doctor_warns_on_an_oversized_metrics_log(self):
        self.plan()
        self.assertNotIn('Metrics log:', self.ai('doctor'))
        state_root = Path(self.ai('path').strip()).parents[1]
        with (state_root / 'metrics.jsonl').open('a') as f:
            for _ in range(5001):
                f.write('{"event":"plan"}\n')
        output = self.ai('doctor')
        self.assertIn('Metrics log:  5002 events', output)
        self.assertIn('ai metrics prune', output)

    def test_task_and_worktree_isolation(self):
        self.plan('--task-id', 'one')
        one = Path(self.ai('path', '--task-id', 'one').strip())
        before = (one / 'state/current-plan.json').read_text()
        self.plan('--task-id', 'two')
        two = Path(self.ai('path', '--task-id', 'two').strip())
        self.assertNotEqual(one, two)
        self.assertEqual(before, (one / 'state/current-plan.json').read_text())
        self.git('remote', 'add', 'origin', 'https://example.com/repo.git')
        first = self.ai('path').strip()
        other = self.home / 'worktree'
        self.git('worktree', 'add', '-qb', 'other', str(other))
        self.repo = other
        self.assertNotEqual(first, self.ai('path').strip())

    def test_oversized_context_does_not_replace_plan(self):
        self.plan()
        state = Path(self.ai('path').strip())
        before = (state / 'state/current-plan.json').read_text()
        output = self.ai('plan', 'x' * 40000, '--profile', 'fast', '--base', 'HEAD', ok=False)
        self.assertIn('exceeds budget', output)
        self.assertEqual(before, (state / 'state/current-plan.json').read_text())

    def test_oversized_handoff_is_rejected(self):
        self.plan()
        state = Path(self.ai('path').strip())
        self.assertIn('exceeds budget', self.ai('handoff', '--evidence', 'x' * 10000, ok=False))
        self.assertEqual(list((state / 'handoffs').iterdir()), [])

    def test_risk_adds_security_and_review(self):
        self.plan()
        self.pass_gates()
        (self.repo / 'auth').mkdir()
        (self.repo / 'auth/token.py').write_text('token = 1')
        output = self.ai('ready', ok=False)
        self.assertIn('review', output)
        self.assertIn('security', output)

    def test_success_exit_without_review_verdict_is_rejected(self):
        self.plan()
        self.ai('gate', 'review', '--', sys.executable, '-c', 'print("review failed")', ok=False)

    def test_gate_timeout(self):
        self.plan()
        self.ai('gate', '--timeout', '1', 'checks', '--', sys.executable, '-c', 'import time; time.sleep(10)', ok=False)
        record = json.loads((Path(self.ai('path').strip()) / 'gates/checks.json').read_text())
        self.assertEqual(record['exit_code'], 124)

    def test_gate_metrics_capture_attempt_findings_and_context(self):
        self.plan()
        self.ai('gate', 'checks', '--', sys.executable, '-c',
                'import json, sys; print(json.dumps({"status":"FAIL","evidence":["fixture"],'
                '"findings":["Debug print left in app.txt:12","Debug print left in app.txt:99"]})); sys.exit(1)',
                ok=False)
        self.ai('gate', 'checks', '--', sys.executable, '-c',
                'import json; print(json.dumps({"status":"PASS","evidence":["fixture verified"]}))')
        state_root = Path(self.ai('path').strip()).parents[1]
        rows = [json.loads(line) for line in (state_root / 'metrics.jsonl').read_text().splitlines()]
        gates = [row for row in rows if row['event'] == 'gate']
        self.assertEqual([g['attempt'] for g in gates], [1, 2])
        self.assertEqual(gates[0]['profile'], 'fast')
        self.assertEqual(gates[0]['task_type'], 'feature')
        self.assertIn('stack_version', gates[0])
        first_findings = gates[0]['findings']
        self.assertEqual(len(first_findings), 2)
        self.assertEqual(first_findings[0]['text'], 'debug print left in app.txt:<n>')
        self.assertEqual(first_findings[0]['hash'], first_findings[1]['hash'])
        self.assertEqual(gates[1]['findings'], [])

    def test_failures_detects_patterns_across_tasks(self):
        self.assertIn('No recurring', self.ai('failures'))

        def fail_with_finding(task_id):
            self.plan('--task-id', task_id)
            self.ai('gate', '--task-id', task_id, 'cleanup', '--', sys.executable, '-c',
                     'import json, sys; print(json.dumps({"status":"FAIL","evidence":["fixture"],'
                     '"findings":["Leftover debug print in src/workers/app.py:12"]})); sys.exit(1)',
                     ok=False)
        fail_with_finding('alpha')
        fail_with_finding('beta')
        output = self.ai('failures')
        self.assertIn('cleanup', output)
        self.assertIn('x2', output)
        report = json.loads(self.ai('failures', '--json'))
        pattern = report['patterns'][0]
        self.assertEqual(pattern['distinct_tasks'], 2)
        self.assertEqual(pattern['scope_hint'], 'src/workers/**')
        self.assertEqual(pattern['resolved_next_attempt'], 0)
        export = json.loads(self.ai('failures', 'export'))
        self.assertEqual(export, [{'gate': 'cleanup', 'hash': pattern['hash'], 'occurrences': 2}])
        detail = json.loads(self.ai('failures', 'show', pattern['id']))
        self.assertEqual(detail['id'], pattern['id'])
        self.assertIn('No recurring', self.ai('failures', '--min', '3'))
        self.ai('failures', 'rebuild')

    def test_lessons_derive_confirm_inject_and_promote(self):
        def fail_with_finding(task_id):
            self.plan('--task-id', task_id)
            self.ai('gate', '--task-id', task_id, 'cleanup', '--', sys.executable, '-c',
                     'import json, sys; print(json.dumps({"status":"FAIL","evidence":["fixture"],'
                     '"findings":["Leftover debug print in src/workers/app.py:12"]})); sys.exit(1)',
                     ok=False)
        fail_with_finding('alpha')
        fail_with_finding('beta')

        self.assertIn('No lessons', self.ai('lessons'))
        self.ai('lessons', 'derive')
        candidates = json.loads(self.ai('lessons', '--json'))
        self.assertEqual(len(candidates), 1)
        lesson_id = candidates[0]['id']
        self.assertEqual(candidates[0]['status'], 'candidate')
        self.assertEqual(candidates[0]['scope'], 'src/workers/**')

        # A model running inside a gate cannot confirm, inject or retire a lesson itself.
        self.env['AI_GATE'] = 'cleanup'
        self.ai('lessons', 'confirm', lesson_id, ok=False)
        self.ai('lessons', 'add', 'Sneaked-in lesson', ok=False)
        self.ai('lessons', 'retire', lesson_id, ok=False)
        del self.env['AI_GATE']
        self.ai('lessons', 'confirm', lesson_id)
        self.assertEqual(json.loads(self.ai('lessons', '--status', 'confirmed', '--json'))[0]['status'], 'confirmed')

        # fast injects nothing; standard/strict inject scope-matched confirmed lessons.
        self.plan('--task-id', 'gamma')
        gamma_task = Path(self.ai('path', '--task-id', 'gamma').strip())
        self.assertNotIn('leftover debug print', (gamma_task / 'state/current-run.md').read_text())

        (self.repo / 'src' / 'workers').mkdir(parents=True, exist_ok=True)
        (self.repo / 'src' / 'workers' / 'app.py').write_text('# app\n')
        self.ai('plan', 'small change', '--profile', 'standard', '--base', 'HEAD', '--task-id', 'delta')
        delta_task = Path(self.ai('path', '--task-id', 'delta').strip())
        self.assertIn('leftover debug print', (delta_task / 'state/current-run.md').read_text())
        snapshot = json.loads((delta_task / 'state/lessons.json').read_text())
        self.assertEqual(snapshot['lessons'][0]['id'], lesson_id)

        # Deriving again does not duplicate or mutate a confirmed lesson.
        self.ai('lessons', 'derive')
        self.assertEqual(len(json.loads(self.ai('lessons', '--json', '--status', 'confirmed'))), 1)

        self.ai('lessons', 'promote', lesson_id)
        rules = json.loads((delta_task.parents[1] / 'rules.json').read_text())
        self.assertEqual(rules[-1]['source'], 'lesson')
        self.assertEqual(json.loads(self.ai('lessons', '--status', 'retired', '--json'))[0]['id'], lesson_id)

    def install_fake_codex(self):
        fakebin = self.home / 'tools'
        fakebin.mkdir(exist_ok=True)
        fake = fakebin / 'codex'
        fake.write_text('#!' + sys.executable + '\n' + '''import json, sys
from pathlib import Path
args = sys.argv
assert args[1] == 'exec' and args[args.index('-s') + 1] == 'read-only'
assert '--output-schema' in args and '--ephemeral' in args
with open('codex-calls.count', 'a') as marker: marker.write('x')
value = {
    'architecture_summary': 'Single Python CLI package.',
    'stack': {'backend': ['Python'], 'frontend': [], 'other': []},
    'database': 'none detected', 'deployment': 'none detected',
    'related_repos': [], 'key_docs': ['README.md: describes the CLI'],
    'confidence_caveats': ['Inferred from static analysis only.'],
}
Path(args[args.index('--output-last-message') + 1]).write_text(json.dumps(value))
print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 5, 'output_tokens': 1}}))
''')
        fake.chmod(0o755)
        self.env['PATH'] = str(fakebin) + os.pathsep + self.env['PATH']

    def test_ticket_check_and_plan_ticket_file_populate_contract(self):
        ticket_text = (
            "## Acceptance Criteria\n"
            "- Retries on 5xx\n"
            "- Gives up after 3 attempts\n\n"
            "Design: https://www.figma.com/file/abc123/Retry-Flow\n"
            "Blocked by: PROJ-42\n"
        )
        ticket_path = self.repo / 'ticket.txt'
        ticket_path.write_text(ticket_text)

        checked = json.loads(self.ai('ticket', 'check', '--file', str(ticket_path), '--json'))
        self.assertEqual(len(checked['acceptance_items']), 2)
        self.assertEqual(checked['figma_url'], 'https://www.figma.com/file/abc123/Retry-Flow')
        self.assertEqual(checked['blockers_mentioned'], ['PROJ-42'])

        self.ai('plan', 'small change', '--profile', 'fast', '--base', 'HEAD', '--ticket-file', str(ticket_path))
        task = Path(self.ai('path').strip())
        contract = (task / 'contracts/current-pr.yml').read_text()
        self.assertIn('Retries on 5xx', contract)
        self.assertIn('Gives up after 3 attempts', contract)
        design = (task / 'contracts/current-design.yml')
        self.assertTrue(design.is_file())
        self.assertIn('figma.com/file/abc123', design.read_text())
        snapshot = json.loads((task / 'state/ticket.json').read_text())
        self.assertEqual(snapshot['blockers_mentioned'], ['PROJ-42'])

        # A human-authored acceptance list is never overwritten by a later --ticket-file.
        contract_path = task / 'contracts/current-pr.yml'
        text = contract_path.read_text().replace(
            'acceptance: ["Retries on 5xx", "Gives up after 3 attempts"]', 'acceptance: ["Human wrote this"]')
        contract_path.write_text(text)
        self.ai('plan', 'small change', '--profile', 'fast', '--base', 'HEAD', '--ticket-file', str(ticket_path))
        self.assertIn('Human wrote this', contract_path.read_text())
        self.assertNotIn('Retries on 5xx', contract_path.read_text())

    def test_profile_static_and_deep_with_caching(self):
        profile = json.loads(self.ai('profile'))
        self.assertIn('languages', profile)

        self.install_fake_codex()
        deep = json.loads(self.ai('profile', '--deep'))
        self.assertEqual(deep['stack']['backend'], ['Python'])
        self.assertIn('caveat', deep)
        self.assertEqual((self.repo / 'codex-calls.count').read_text(), 'x')

        # Same analyzed commit: cached, no second reviewer invocation.
        cached = self.ai('profile', '--deep')
        self.assertIn('up to date', cached)
        self.assertEqual((self.repo / 'codex-calls.count').read_text(), 'x')

        # --refresh forces a fresh reviewer invocation regardless of commit.
        self.ai('profile', '--deep', '--refresh')
        self.assertEqual((self.repo / 'codex-calls.count').read_text(), 'xx')

        state_root = Path(self.ai('path').strip()).parents[1]
        self.assertTrue((state_root / 'project-deep-profile.json').is_file())
        self.assertTrue((state_root / 'review/deep-profile-events.jsonl').is_file())

    def test_confidence_is_advisory_and_never_loosens_gating(self):
        self.plan()
        card = json.loads(self.ai('confidence', '--profile', 'fast', '--base', 'HEAD', '--json'))
        self.assertIn('required_gates', card)
        self.assertIsNone(card['weakest_gate'])

        required = ('checks', 'regression', 'cleanup', 'provenance', 'ponytail', 'summary', 'contract')
        for i in range(6):
            task_id = f'seed-{i}'
            self.plan('--task-id', task_id)
            for gate in required:
                self.ai('gate', '--task-id', task_id, gate, '--', sys.executable, '-c',
                        'import json; print(json.dumps({"status":"PASS","evidence":["fixture"]}))')

        card = json.loads(self.ai('confidence', '--profile', 'fast', '--base', 'HEAD', '--json'))
        self.assertIsNotNone(card['weakest_gate'])
        self.assertEqual(card['weakest_gate']['pass_rate'], 1.0)
        self.assertEqual(card['weakest_gate']['n'], 6)

        # A brand-new task still needs fresh evidence for every required gate.
        self.plan('--task-id', 'fresh')
        output = self.ai('ready', '--task-id', 'fresh', ok=False)
        self.assertIn('NEEDS_HUMAN', output)
        for gate in required:
            self.assertIn(gate, output)

    def test_prompt_experiment_report_and_promote(self):
        slot_dir = ROOT / 'templates/prompts/validator.cleanup'
        created_dir = not slot_dir.exists()
        slot_dir.mkdir(parents=True, exist_ok=True)
        variant_b = slot_dir / 'b.md'
        variant_b.write_text('Alternate cleanup instructions for testing.\n')
        def cleanup_files():
            variant_b.unlink(missing_ok=True)
            if created_dir:
                try: slot_dir.rmdir()
                except OSError: pass
        self.addCleanup(cleanup_files)

        listing = self.ai('prompt', 'list')
        self.assertIn('validator.cleanup', listing)
        self.assertIn('variants=a,b', listing)
        self.assertEqual(self.ai('prompt', 'show', 'cleanup', 'b').strip(),
                          'Alternate cleanup instructions for testing.')

        self.ai('prompt', 'experiment', 'start', 'cleanup', '--variants', 'a,b', '--min-samples', '2')
        self.assertIn('"slot": "validator.cleanup"', self.ai('prompt', 'experiment', 'status'))
        self.ai('prompt', 'experiment', 'start', 'cleanup', '--variants', 'a,b', ok=False)

        # Deterministic replacement for the old probabilistic `for i in range(30)`
        # sampling loop: compute IN-PROCESS which `small change N` task texts the
        # experiment hash assigns to variant 'a' vs 'b', then shell out for exactly
        # two known-'a' and two known-'b' tasks. No retries, no probability -- so
        # coverage's ~3x subprocess slowdown can no longer make this flake.
        def predict_cleanup_variant(task):
            # Mirrors prompts.assign_prompt_variants for the active experiment slot:
            #   variant = variants[int(shasum(cache_key + slot), 16) % len(variants)]
            # with cache_key from lifecycle.task_cache_key(root, task, profile, base, skills).
            sel = skills.select_skills(self.home / 'no-skill-state', task, 'fast')
            cache_key = lifecycle.task_cache_key(self.repo, task, 'fast', 'HEAD', sel)
            return ['a', 'b'][int(core.shasum(cache_key + 'validator.cleanup'), 16) % 2]

        want = {'a': [], 'b': []}
        for i in range(60):
            if len(want['a']) >= 2 and len(want['b']) >= 2: break
            v = predict_cleanup_variant(f'small change {i}')
            if len(want[v]) < 2: want[v].append(i)
        self.assertEqual((len(want['a']), len(want['b'])), (2, 2))

        seen = {'a': 0, 'b': 0}
        self_checked = False
        for variant, indices in want.items():
            for i in indices:
                task_id = f'variant-probe-{i}'
                self.ai('plan', f'small change {i}', '--profile', 'fast', '--base', 'HEAD', '--task-id', task_id)
                task_dir = Path(self.ai('path', '--task-id', task_id).strip())
                assignment = json.loads((task_dir / 'state/prompt-assignment.json').read_text())
                actual = assignment.get('validator.cleanup', {}).get('variant')
                # Self-check: the in-process prediction MUST match what the CLI wrote.
                # A mismatch means the skills/fingerprint assumption behind the
                # prediction is wrong -- fail loudly rather than silently reintroduce
                # flakiness.
                self.assertEqual(actual, variant,
                    f'predicted {variant!r} for "small change {i}" but CLI assigned {actual!r}')
                self_checked = True
                self.ai('gate', '--task-id', task_id, 'cleanup', '--', sys.executable, '-c',
                         'import json; print(json.dumps({"status":"PASS","evidence":["fixture"]}))')
                seen[variant] += 1
        self.assertTrue(self_checked)
        self.assertEqual(seen, {'a': 2, 'b': 2})

        report = json.loads(self.ai('prompt', 'report', '--json'))
        by_variant = {s['variant']: s for s in report['stats']}
        self.assertGreaterEqual(by_variant['a']['n'], 2)
        self.assertGreaterEqual(by_variant['b']['n'], 2)
        self.assertEqual(by_variant['a']['pass_rate'], 1.0)

        self.env['AI_GATE'] = 'cleanup'
        self.ai('prompt', 'promote', 'cleanup', 'b', '--confirm', ok=False)
        del self.env['AI_GATE']
        self.ai('prompt', 'promote', 'cleanup', 'b', '--confirm')
        self.assertIn('No active experiment', self.ai('prompt', 'experiment', 'status'))

        # A promoted override wins regardless of the (now-stopped) experiment's hash.
        self.plan('--task-id', 'after-promote')
        after = Path(self.ai('path', '--task-id', 'after-promote').strip())
        assignment = json.loads((after / 'state/prompt-assignment.json').read_text())
        self.assertEqual(assignment['validator.cleanup']['variant'], 'b')

        history = json.loads(self.ai('prompt', 'history', '--json'))
        self.assertEqual(history[-1]['action'], 'promote')
        self.assertEqual(history[-1]['variant'], 'b')
        self.assertIsNone(history[-1]['previous'])
        self.assertTrue(history[-1]['evidence'])
        self.assertIn('by_stack_version', history[-1]['evidence'][0])

        # A model running inside a gate cannot reset or roll back a promoted prompt either.
        self.env['AI_GATE'] = 'cleanup'
        self.ai('prompt', 'reset', 'cleanup', ok=False)
        self.ai('prompt', 'rollback', 'cleanup', '--confirm', ok=False)
        del self.env['AI_GATE']

        # This was the first promotion for the slot: nothing earlier to roll back to.
        self.ai('prompt', 'rollback', 'cleanup', '--confirm', ok=False)

        self.ai('prompt', 'reset', 'cleanup')
        history = json.loads(self.ai('prompt', 'history', '--json'))
        self.assertEqual(history[-1]['action'], 'reset')
        self.assertEqual(history[-1]['previous']['variant'], 'b')

        # Restarting an experiment on the same slot must not let stale samples (from
        # before this run, or against edited text) satisfy the new sample requirement.
        variant_b.write_text('A different alternate cleanup instruction, edited later.\n')
        self.ai('prompt', 'experiment', 'start', 'cleanup', '--variants', 'a,b', '--min-samples', '2')
        output = self.ai('prompt', 'promote', 'cleanup', 'b', '--confirm', ok=False)
        self.assertIn('Not enough samples', output)
        self.ai('prompt', 'experiment', 'stop')

    def test_prompt_rollback_restores_previous_promotion(self):
        slot_dir = ROOT / 'templates/prompts/validator.cleanup'
        created_dir = not slot_dir.exists()
        slot_dir.mkdir(parents=True, exist_ok=True)
        variant_b = slot_dir / 'b.md'
        variant_b.write_text('Rollback test variant b.\n')
        def cleanup_files():
            variant_b.unlink(missing_ok=True)
            if created_dir:
                try: slot_dir.rmdir()
                except OSError: pass
        self.addCleanup(cleanup_files)

        self.ai('prompt', 'promote', 'cleanup', 'b', '--confirm')
        self.ai('prompt', 'promote', 'cleanup', 'a', '--confirm')
        history = json.loads(self.ai('prompt', 'history', '--json'))
        self.assertEqual([h['action'] for h in history], ['promote', 'promote'])
        self.assertEqual(history[-1]['previous']['variant'], 'b')

        self.ai('prompt', 'rollback', 'cleanup', '--confirm')
        self.assertIn('promoted=b', self.ai('prompt', 'list'))
        history = json.loads(self.ai('prompt', 'history', '--json'))
        self.assertEqual(history[-1]['action'], 'rollback')
        self.assertEqual(history[-1]['variant'], 'b')

        self.ai('prompt', 'reset', 'cleanup')

    def test_benchmark_run_report_compare_and_isolation(self):
        self.plan()
        self.pass_gates()
        state_root = Path(self.ai('path').strip()).parents[1]
        metrics_before = (state_root / 'metrics.jsonl').read_text()

        listing = self.ai('benchmark', 'list')
        self.assertIn('clean-small-change', listing)

        output = self.ai('benchmark', 'run', '--scenario', 'clean-small-change', '--profile', 'fast')
        self.assertIn('case(s) passed', output)
        self.assertNotIn('FAIL', output)

        # The benchmark's own sandboxed pipeline runs must never touch the real metrics.jsonl.
        self.assertEqual((state_root / 'metrics.jsonl').read_text(), metrics_before)

        report = json.loads(self.ai('benchmark', 'report', '--json'))
        self.assertEqual(report['cases_failed'], 0)
        self.assertEqual(len(report['case_results']), 1)

        self.ai('benchmark', 'run', '--scenario', 'mutating-validator')
        run_ids = [json.loads(line)['run_id'] for line in (state_root / 'benchmarks/index.jsonl').read_text().splitlines()]
        self.assertEqual(len(run_ids), 2)
        self.ai('benchmark', 'compare', run_ids[0], run_ids[1], ok=False)

    def test_benchmark_compares_profiles_without_a_plan(self):
        report = json.loads(self.ai('benchmark', '--json'))
        self.assertEqual(report['fixtures'], len(report['results']))
        by_id = {row['id']: row['profiles'] for row in report['results']}
        self.assertEqual(by_id['payments-migration']['fast']['risk'], 'HIGH')
        self.assertEqual(by_id['payments-migration']['strict']['risk'], 'HIGH')
        self.assertEqual(by_id['ui-copy']['fast']['risk'], 'LOW')
        self.assertEqual(by_id['ui-copy']['strict']['risk'], 'MEDIUM')
        for profile, tokens in (('fast', 40000), ('standard', 120000), ('strict', 250000)):
            self.assertEqual(by_id['ui-copy'][profile]['usage_tokens'], tokens)
        self.assertEqual(by_id['onboarding-design']['standard']['task_type'], 'design')
        self.assertEqual(by_id['sdk-docs']['fast']['skills'], ['source-driven-development'])
        self.assertEqual(by_id['service-observability']['fast']['skills'], ['observability-and-instrumentation'])

    def configure_pipeline(self, failing=None):
        for name in ('cleanup','checks','regression','contract','provenance','ponytail','summary'):
            script = 'import json; print(json.dumps({"status":"PASS","evidence":["fixture validated"],"usage":{"input_tokens":12,"output_tokens":3,"cost_usd":0.01}}))'
            if name == failing:
                script = 'raise SystemExit(2)'
            self.ai('validators','set',name,'--',sys.executable,'-c',script)

    def test_pipeline_preflight_success_resume_and_metrics(self):
        self.plan()
        self.assertIn('configure validators',self.ai('pipeline',ok=False))
        self.configure_pipeline()
        preview=json.loads(self.ai('pipeline','--dry-run'))
        self.assertEqual(preview['order'][0],'cleanup')
        self.assertEqual(json.loads(self.ai('metrics','--json'))['gate_attempts'],0)
        self.assertIn('PR_READY',self.ai('pipeline'))
        report=json.loads(self.ai('metrics','--json'))
        self.assertEqual(report['gate_passes'],7)
        self.assertEqual(report['usage']['input_tokens']['reported_total'],84)
        self.assertEqual(report['pipeline_successes'],1)
        self.assertIn('fresh evidence reused',self.ai('pipeline','--resume'))
        self.assertEqual(json.loads(self.ai('metrics','--json'))['gate_attempts'],7)
        self.assertEqual(json.loads(self.ai('metrics','--task-id','two','--json'))['gate_attempts'],0)
        self.assertEqual(json.loads(self.ai('metrics','--all-tasks','--json'))['gate_attempts'],7)
        self.assertEqual(self.git('status','--porcelain'),'')

    def test_metrics_by_dimension_csv_budget_and_prune(self):
        self.plan()
        self.configure_pipeline()
        self.ai('pipeline')
        by_gate = json.loads(self.ai('metrics', '--all-tasks', '--by', 'gate', '--json'))
        checks = next(row for row in by_gate if row['key'] == 'checks')
        self.assertEqual(checks['input_tokens'], 12)
        self.assertEqual(checks['reported_attempts'], 1)

        csv_output = self.ai('metrics', '--all-tasks', '--by', 'gate', '--format', 'csv')
        self.assertIn('key,gate_attempts', csv_output.splitlines()[0])
        self.assertIn('checks', csv_output)

        text_output = self.ai('metrics', '--all-tasks', '--by', 'gate', '--budget', '10')
        self.assertIn('exceeds budget', text_output)

        self.env['AI_GATE'] = 'checks'
        self.ai('metrics', 'prune', '--older-than', '0d', '--confirm', ok=False)
        del self.env['AI_GATE']
        self.assertIn('Re-run with --confirm', self.ai('metrics', 'prune', '--older-than', '0d', ok=False))

        state_root = Path(self.ai('path').strip()).parents[1]
        self.ai('metrics', 'prune', '--older-than', '0d', '--confirm')
        self.assertEqual((state_root / 'metrics.jsonl').read_text().strip(), '')

    def test_metrics_label_and_campaign_report(self):
        self.plan()
        task_key = Path(self.ai('path').strip()).name
        self.ai('gate', 'checks', '--', sys.executable, '-c',
                'import json, sys; print(json.dumps({"status":"FAIL","evidence":["fixture"],'
                '"findings":["Debug print left in app.txt:12"]})); sys.exit(1)', ok=False)
        self.ai('gate', 'checks', '--', sys.executable, '-c',
                'import json; print(json.dumps({"status":"PASS","evidence":["fixture verified"]}))')
        self.configure_pipeline()
        self.ai('pipeline')

        # Labeling is human-only, same invariant as lessons/prompts.
        self.env['AI_GATE'] = 'checks'
        self.ai('metrics', 'label', 'checks', '--task-key', task_key, '--false-positive', ok=False)
        del self.env['AI_GATE']

        # The PASSED (second) attempt cannot be judged true/false positive.
        out = self.ai('metrics', 'label', 'checks', '--task-key', task_key, '--true-positive', ok=False)
        self.assertIn('Only a FAILED gate attempt', out)

        # Label the failed attempt explicitly (attempt 1).
        out = self.ai('metrics', 'label', 'checks', '--task-key', task_key, '--attempt', '1', '--false-positive')
        self.assertIn('checks attempt=1 -> false_positive', out)

        # A gate/attempt with no recorded rows is a clear error, not a crash.
        self.ai('metrics', 'label', 'regression', '--task-key', task_key, '--true-positive', ok=False)
        self.ai('metrics', 'label', 'checks', '--task-key', task_key, '--attempt', '9', '--true-positive', ok=False)

        report = json.loads(self.ai('metrics', '--campaign', '--json'))
        self.assertEqual(report['tasks'], 1)
        self.assertEqual(report['reached_pr_ready'], 1)
        self.assertEqual(report['gate_labels']['checks'],
                         {'true_positive': 0, 'false_positive': 1, 'labeled': 1, 'false_positive_rate': 1.0})
        detail = report['tasks_detail'][0]
        self.assertEqual(detail['task_type'], 'feature')
        # attempt 1 (manual FAIL) + attempt 2 (manual PASS) + attempt 3 (pipeline re-runs
        # checks since its own configured command differs from the manual one above).
        self.assertEqual(detail['retries_by_gate']['checks'], 3)
        self.assertTrue(detail['reached_pr_ready'])
        self.assertIsNotNone(detail['time_to_ready_seconds'])  # falls back to the `plan` event's ts
        self.assertIn('volume signal', report['findings_raised_caveat'])

        text = self.ai('metrics', '--campaign')
        self.assertIn('feature', text)
        self.assertIn('false_positive_rate', text)

    def test_pipeline_usage_budget_stops_pipeline(self):
        self.plan()
        self.configure_pipeline()
        big_script = ('import json; print(json.dumps({"status":"PASS","evidence":["fixture validated"],'
                      '"usage":{"input_tokens":30000,"output_tokens":20000}}))')
        self.ai('validators', 'set', 'cleanup', '--', sys.executable, '-c', big_script)
        output = self.ai('pipeline', ok=False)
        self.assertIn('BUDGET_EXCEEDED', output)
        # The gate that crossed the budget is named, along with what never ran.
        self.assertIn('crossed at:  cleanup', output)
        self.assertIn('not run:', output)
        report = json.loads(self.ai('metrics', '--json'))
        self.assertEqual(report['gate_attempts'], 1)
        self.assertEqual(report['pipeline_budget_exceeded'], 1)
        self.assertEqual(report['pipeline_usage']['input_tokens']['reported_total'], 30000)

    def test_pipeline_stops_on_failure_and_config_changes_invalidate(self):
        self.plan()
        self.configure_pipeline(failing='checks')
        self.ai('pipeline',ok=False)
        report=json.loads(self.ai('metrics','--json'))
        self.assertEqual(report['gate_attempts'],2)
        self.assertEqual(report['gate_failures'],1)
        self.assertEqual(report['pipeline_successes'],0)
        self.configure_pipeline()
        self.assertIn('PR_READY',self.ai('pipeline','--resume'))
        self.ai('validators','remove','checks')
        self.assertIn('NEEDS_HUMAN',self.ai('ready',ok=False))

    def test_exit_code_adapter_and_json_checks_are_explicit(self):
        self.plan()
        self.configure_pipeline()
        self.ai('validators','set','checks','--',sys.executable,'-c','print("not a verdict")')
        self.ai('pipeline',ok=False)
        self.ai('validators','set','--adapter','exit-code','checks','--',sys.executable,'-c','pass',ok=False)
        self.ai('validators','set','--adapter','exit-code','--evidence','Project tests succeeded',
                'checks','--',sys.executable,'-c','pass')
        self.assertIn('PR_READY',self.ai('pipeline'))
        self.ai('validators','set','--timeout','0','checks','--','true',ok=False)

    def test_pipeline_requires_security_and_rejects_mutation(self):
        self.plan()
        self.configure_pipeline()
        self.ai('validators','set','cleanup','--',sys.executable,'-c',
                'open("app.txt","w").write("changed"); print(\'{"status":"PASS","evidence":["fixture"]}\')')
        self.assertIn('changed during gate',self.ai('pipeline',ok=False))
        self.assertEqual(json.loads(self.ai('metrics','--json'))['gate_attempts'],1)
        (self.repo/'auth').mkdir()
        (self.repo/'auth/token.py').write_text('token = 1')
        output=self.ai('pipeline',ok=False)
        self.assertIn('security',output)
        self.assertIn('review',output)

    def bundled_pipeline(self, design=False):
        fakebin=self.home/'tools'
        fakebin.mkdir()
        fake=fakebin/'codex'
        fake.write_text('#!'+sys.executable+'\n'+'''import json, os, sys
from pathlib import Path
args=sys.argv
assert args[1]=='exec' and args[args.index('-s')+1]=='read-only'
assert 'approval_policy="never"' in args
assert '--output-schema' in args and '--ephemeral' in args
prompt=sys.stdin.read()
assert 'Read-only review' in prompt
name=os.environ['AI_GATE']
task=Path(os.environ['AI_TASK_DIR'])
with (task/'review/calls').open('a') as out: out.write(name+'\\n')
mode=os.environ.get('FAKE_VERDICT','PASS')
value={'status':mode if mode in ('PASS','FAIL','NEEDS_HUMAN') else 'PASS',
       'evidence':['app.txt:1 inspected fixture'], 'findings':[],
       'summary_markdown':'# Fixture change\\nVerified fixture tests.' if name=='summary' else ''}
if mode=='contradiction': value['findings']=['app.txt:1 unresolved blocker']
if mode!='missing': Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(value))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':2}}))
if mode=='exit': sys.exit(2)
''')
        fake.chmod(0o755)
        self.env['PATH']=str(fakebin)+os.pathsep+self.env['PATH']
        if design:
            self.plan('--figma','https://figma.com/design/fixture')
        else:
            self.plan()
        task=Path(self.ai('path').strip())
        contract=task/'contracts/current-pr.yml'
        contract.write_text(contract.read_text().replace('acceptance: []','acceptance: ["Fixture remains readable"]'))
        for name in ('checks','regression'):
            self.ai('validators','set','--adapter','exit-code','--evidence','Fixture check passed',
                    name,'--',sys.executable,'-c','pass')
        self.ai('validators','install')
        return task

    def test_bundled_pipeline_summary_provenance_and_resume(self):
        task=self.bundled_pipeline()
        config=json.loads(self.ai('validators','show'))
        self.ai('validators','install')
        self.assertEqual(config,json.loads(self.ai('validators','show')))
        self.assertEqual(config['validators']['checks']['adapter'],'exit-code')
        self.assertIn('PR_READY',self.ai('pipeline'))
        self.assertEqual((task/'review/calls').read_text().splitlines(),
                         ['cleanup','contract','ponytail','summary','provenance'])
        self.assertTrue((task/'state/pr-summary.md').is_file())
        report=json.loads(self.ai('metrics','--json'))
        self.assertEqual(report['usage']['input_tokens']['reported_total'],50)
        self.ai('pipeline','--resume')
        self.assertEqual(len((task/'review/calls').read_text().splitlines()),5)
        (task/'state/pr-summary.md').write_text('tampered')
        self.assertIn('summary',self.ai('ready',ok=False))
        self.assertIn('PR_READY',self.ai('pipeline','--resume'))
        self.assertEqual((task/'review/calls').read_text().splitlines()[-1],'summary')
        # Restoring the identical reviewed summary may reuse provenance evidence.
        self.assertEqual(self.git('status','--porcelain'),'')

    def test_bundled_validator_fails_closed(self):
        task=self.bundled_pipeline()
        for mode in ('FAIL','NEEDS_HUMAN','contradiction','missing','exit'):
            with self.subTest(mode=mode):
                self.env['FAKE_VERDICT']=mode
                self.ai('pipeline',ok=False)
                record=json.loads((task/'gates/cleanup.json').read_text())
                self.assertFalse(record['passed'])
        self.assertEqual(set((task/'review/calls').read_text().splitlines()),{'cleanup'})

    def test_bundled_contract_requires_criteria_and_gates(self):
        task=self.bundled_pipeline()
        self.assertIn('through ai pipeline',self.ai('validate','contract',ok=False))
        self.ai('gate','contract','--',sys.executable,str(ROOT/'ai_stack/cli.py'),'validate','contract',ok=False)
        contract=task/'contracts/current-pr.yml'
        contract.write_text('objective: fixture\nacceptance: []\n')
        self.ai('pipeline',ok=False)
        record=json.loads((task/'gates/contract.json').read_text())
        self.assertIn('no acceptance criteria',Path(record['log']).read_text())
        self.assertEqual((task/'review/calls').read_text().splitlines(),['cleanup'])

    def test_bundled_conditional_review_security_and_design(self):
        task=self.bundled_pipeline(design=True)
        (self.repo/'auth').mkdir()
        (self.repo/'auth/token.py').write_text('token = 1')
        self.assertIn('PR_READY',self.ai('pipeline'))
        calls=(task/'review/calls').read_text().splitlines()
        self.assertIn('review',calls)
        self.assertIn('security',calls)
        self.assertIn('design',calls)
        self.assertEqual(calls[-2:],['summary','provenance'])

    def test_init_reports_repository_state_and_languages(self):
        output = self.ai('init')
        self.assertIn('Repository:', output)
        self.assertIn('State:', output)
        self.assertIn('Repository modified: NO', output)
        self.assertIn('Languages:', output)

    def test_optimize_runs_without_a_git_repository(self):
        outside = self.home / 'outside'
        outside.mkdir()
        self.repo = outside
        output = self.ai('optimize')
        self.assertIn('Prompt/context optimization audit', output)
        self.assertIn('Repository modified: NO', output)

    def test_skill_list_explain_enable_disable_and_dry_run(self):
        listing = self.ai('skill')
        self.assertIn('Skills', listing)
        self.assertIn('handoff', listing)

        explain = self.ai('skill', 'explain', 'handoff')
        self.assertIn('"category"', explain)

        disabled = self.ai('skill', 'disable', 'handoff')
        self.assertIn('handoff: disabled', disabled)
        self.assertIn('· handoff', self.ai('skill'))

        enabled = self.ai('skill', 'enable', 'handoff')
        self.assertIn('handoff: enabled', enabled)
        self.assertIn('✓ handoff', self.ai('skill'))

        dry_run = self.ai('skill', 'dry-run', 'handoff')
        self.assertIn('SKILL DRY RUN', dry_run)
        self.assertIn('repo writes: NO', dry_run)

        self.assertIn('Unknown skill', self.ai('skill', 'explain', 'not-a-real-skill', ok=False))

    def test_rules_add_list_and_remove(self):
        self.assertIn('No repository-specific rules.', self.ai('rules'))
        self.ai('rules', 'add', 'Never touch payments code', '--scope', 'payments/**')
        listing = self.ai('rules')
        self.assertIn('[payments/**] Never touch payments code', listing)
        self.assertIn('Invalid rule index.', self.ai('rules', 'remove', '5', ok=False))
        self.assertIn('Removed: Never touch payments code', self.ai('rules', 'remove', '1'))
        self.assertIn('No repository-specific rules.', self.ai('rules'))

    def test_handoff_writes_payload_within_budget(self):
        self.plan()
        output = self.ai('handoff', 'in progress work', '--next', 'run tests')
        self.assertIn('Handoff:', output)
        state = Path(self.ai('path').strip())
        handoffs = list((state / 'handoffs').glob('handoff-*.json'))
        self.assertEqual(len(handoffs), 1)
        payload = json.loads(handoffs[0].read_text())
        self.assertEqual(payload['task'], 'in progress work')
        self.assertEqual(payload['next_action'], 'run tests')
        self.assertEqual(payload['state'], 'in_progress')

    def test_impact_falls_back_to_heuristic_without_crg(self):
        (self.repo / 'app.txt').write_text('changed for impact\n')
        output = self.ai('impact', '--base', 'HEAD')
        self.assertIn('Change impact', output)
        self.assertIn('CRG:         unavailable', output)
        self.assertIn('final risk:', output)

    def test_review_prepares_prompt_without_launching_codex(self):
        (self.repo / 'app.txt').write_text('changed for review\n')
        output = self.ai('review', '--base', 'HEAD', '--no-launch')
        self.assertIn('Review context:', output)
        self.assertIn('CRG:', output)
        state = Path(self.ai('path').strip())
        self.assertTrue((state / 'state/current-review.md').exists())

    def test_review_commit_targets_a_specific_commit_without_touching_the_checkout(self):
        # `ai review --commit` reviews one commit in isolation, in a disposable
        # worktree, and must never move this checkout's own HEAD or leave files dirty.
        before_head = self.git('rev-parse', 'HEAD')
        (self.repo / 'app.txt').write_text('second commit content\n')
        self.git('commit', '-am', 'second commit')
        second = self.git('rev-parse', 'HEAD')
        self.git('reset', '--hard', before_head)  # this checkout goes back to being clean/behind

        output = self.ai('review', '--commit', second, '--no-launch')
        self.assertIn(f'(commit {second[:12]})', output)
        state = Path(self.ai('path').strip()).parents[1]
        artifact = state / 'adhoc-reviews' / second[:12] / 'current-review.md'
        self.assertTrue(artifact.is_file())
        self.assertIn('Changed files: 1', artifact.read_text())
        # The active task's own review artifact must be untouched by an adhoc review.
        self.assertFalse((Path(self.ai('path').strip()) / 'state/current-review.md').exists())

        # This checkout is exactly where it was before the review ran.
        self.assertEqual(self.git('rev-parse', 'HEAD'), before_head)
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertEqual(len(self.git('worktree', 'list').splitlines()), 1)  # only this repo's own entry left

    def test_review_commit_and_pr_together_is_rejected_by_the_parser(self):
        self.ai('review', '--commit', 'HEAD', '--pr', '1', '--no-launch', ok=False)

    def test_review_bad_commit_fails_closed(self):
        output = self.ai('review', '--commit', 'not-a-real-sha', '--no-launch', ok=False)
        self.assertIn('does not exist', output)

    def test_review_pr_without_gh_fails_with_an_actionable_message(self):
        # A hardcoded PATH (e.g. '/usr/bin:/bin') isn't reliably gh-free: GitHub Actions'
        # ubuntu-latest runners ship gh preinstalled at /usr/bin/gh - alongside git in the
        # same directory - so excluding any directory containing gh would take git down
        # with it, failing this subprocess for an unrelated reason before it ever reaches
        # the gh check. Build an explicit allowlist directory instead: just a symlink to
        # git (which git_root() needs to even get this far), nothing else reachable.
        git_path = shutil.which('git')
        self.assertIsNotNone(git_path, 'git must be on PATH for this test to mean anything')
        with tempfile.TemporaryDirectory(prefix='gh-free-path-') as bin_dir:
            (Path(bin_dir) / 'git').symlink_to(git_path)
            env = dict(self.env, PATH=bin_dir)
            result = subprocess.run([sys.executable, str(ROOT / 'ai_stack/cli.py'), 'review', '--pr', '1', '--no-launch'],
                                    cwd=self.repo, env=env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('GitHub CLI (gh) missing', result.stdout + result.stderr)

    def test_docs_and_figma_and_crg_and_graph_doctor_without_external_tools(self):
        docs = self.ai('docs', 'doctor')
        self.assertIn('Context7:', docs)

        figma = self.ai('figma', 'doctor')
        self.assertIn('Recommended remote:', figma)

        crg = self.ai('crg', 'doctor')
        self.assertIn('Code Review Graph:', crg)

        graph = self.ai('graph', 'doctor')
        self.assertIn('Graphify:', graph)
        self.assertIn('Graph storage:', graph)

    def test_deploy_reports_none_detected_on_a_plain_repo(self):
        output = self.ai('deploy')
        self.assertIn('Docker: none detected', output)
        self.assertIn('No deployment tooling or documentation detected', output)

    def test_deploy_detects_docker_ci_and_deploy_docs(self):
        (self.repo / 'Dockerfile').write_text('FROM scratch\n')
        workflows = self.repo / '.github/workflows'
        workflows.mkdir(parents=True)
        (workflows / 'ci.yml').write_text('name: ci\n')
        (self.repo / 'scripts').mkdir()
        (self.repo / 'scripts/deploy.sh').write_text('#!/bin/sh\necho deploying\n')
        (self.repo / 'README.md').write_text('# App\n\n## Deploy\n\nRun scripts/deploy.sh\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'add deploy tooling')

        output = self.ai('deploy')
        self.assertIn('Dockerfile', output)
        self.assertIn('.github/workflows/ci.yml', output)
        self.assertIn('scripts/deploy.sh', output)
        self.assertIn('README.md', output)
        self.assertNotIn('No deployment tooling', output)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='stack-install-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.destination = self.root / 'data/ai-agent-stack'
        self.bin = self.root / 'bin'

    def test_upgrade_and_reinstall_from_installed_source(self):
        first = installer.install(ROOT, self.destination, self.bin)
        second = installer.install(self.destination, self.destination, self.bin)
        self.assertTrue(first.exists())
        self.assertNotEqual(first, second)
        self.assertEqual(self.destination.resolve(), second.resolve())
        self.assertFalse((second / '.git').exists())
        self.assertFalse((second / 'tests').exists())

    def test_validation_failure_preserves_installation(self):
        first = installer.install(ROOT, self.destination, self.bin)
        with patch.object(installer.subprocess, 'check_output', return_value='wrong'):
            with self.assertRaises(RuntimeError):
                installer.install(ROOT, self.destination, self.bin)
        self.assertEqual(self.destination.resolve(), first.resolve())

    def test_activation_failure_restores_legacy_directory(self):
        self.destination.mkdir(parents=True)
        (self.destination / 'marker').write_text('old')
        real_replace = os.replace
        def fail_command_link(source, dest):
            if Path(dest) == self.bin / 'ai':
                raise OSError('simulated link failure')
            return real_replace(source, dest)
        with patch.object(installer.os, 'replace', side_effect=fail_command_link):
            with self.assertRaises(OSError):
                installer.install(ROOT, self.destination, self.bin)
        self.assertEqual((self.destination / 'marker').read_text(), 'old')

    def test_activation_failure_restores_previous_symlink(self):
        first = installer.install(ROOT, self.destination, self.bin)
        real_replace = os.replace
        def fail_command_link(source, dest):
            if Path(dest) == self.bin / 'ai':
                raise OSError('simulated link failure')
            return real_replace(source, dest)
        with patch.object(installer.os, 'replace', side_effect=fail_command_link):
            with self.assertRaises(OSError):
                installer.install(ROOT, self.destination, self.bin)
        self.assertEqual(self.destination.resolve(), first.resolve())


if __name__ == '__main__':
    unittest.main()
