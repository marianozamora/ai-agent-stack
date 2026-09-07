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

        seen = {'a': 0, 'b': 0}
        for i in range(30):
            if seen['a'] >= 2 and seen['b'] >= 2: break
            task_id = f'variant-probe-{i}'
            self.ai('plan', f'small change {i}', '--profile', 'fast', '--base', 'HEAD', '--task-id', task_id)
            task_dir = Path(self.ai('path', '--task-id', task_id).strip())
            assignment = json.loads((task_dir / 'state/prompt-assignment.json').read_text())
            variant = assignment.get('validator.cleanup', {}).get('variant')
            if variant not in seen or seen[variant] >= 2: continue
            self.ai('gate', '--task-id', task_id, 'cleanup', '--', sys.executable, '-c',
                     'import json; print(json.dumps({"status":"PASS","evidence":["fixture"]}))')
            seen[variant] += 1
        self.assertGreaterEqual(seen['a'], 2)
        self.assertGreaterEqual(seen['b'], 2)

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

    def test_pipeline_usage_budget_stops_pipeline(self):
        self.plan()
        self.configure_pipeline()
        big_script = ('import json; print(json.dumps({"status":"PASS","evidence":["fixture validated"],'
                      '"usage":{"input_tokens":30000,"output_tokens":20000}}))')
        self.ai('validators', 'set', 'cleanup', '--', sys.executable, '-c', big_script)
        output = self.ai('pipeline', ok=False)
        self.assertIn('usage budget exceeded', output)
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
