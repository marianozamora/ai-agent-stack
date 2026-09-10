import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

spec = importlib.util.spec_from_file_location('workflow', Path(__file__).resolve().parents[1] / 'ai_stack/workflow.py')
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


class HelpersTests(unittest.TestCase):
    def test_missing_invalid_and_partial_usage(self):
        self.assertEqual(workflow.usage_from_verdict({'usage': {
            'input_tokens': True, 'output_tokens': -1, 'cost_usd': float('nan')}}), {})
        self.assertEqual(workflow.usage_from_verdict({'usage': {'input_tokens': 1.5}}), {})
        rows = [{'event': 'gate', 'task_key': 'a', 'gate': 'checks', 'passed': False,
                 'usage': {'cost_usd': 0.02}},
                {'event': 'gate', 'task_key': 'b', 'gate': 'checks', 'passed': True, 'usage': {}}]
        report = workflow.summarize(rows)
        self.assertEqual(report['repeated_gate_attempts'], 0)
        self.assertEqual(report['usage']['cost_usd']['reported_attempts'], 1)
        self.assertIsNone(report['usage']['input_tokens']['reported_total'])

    def test_normalize_finding_folds_paths_lines_and_hex(self):
        a = workflow.normalize_finding('Leaked token 9f2c1a4b7d30ffab in src/auth.py:42')
        b = workflow.normalize_finding('Leaked token 00112233445566aa in src/auth.py:107')
        self.assertEqual(a, b)
        self.assertEqual(a, 'leaked token <hex> in src/auth.py:<n>')
        self.assertEqual(workflow.finding_signature('X'), workflow.finding_signature('x  '))
        self.assertEqual(len(workflow.finding_signature('anything')), 12)
        self.assertEqual(workflow.normalize_finding('x' * 500), ('x' * 500).lower()[:160])

    def test_detect_patterns_requires_distinct_tasks_and_tracks_resolution(self):
        rows = [
            {'event': 'gate', 'task_key': 't1', 'gate': 'cleanup', 'attempt': 1, 'passed': False,
             'profile': 'fast', 'risk': 'LOW', 'task_type': 'feature', 'ts': 1,
             'findings': [{'hash': 'abc123456789', 'text': 'leftover debug in src/app.py:<n>'}]},
            {'event': 'gate', 'task_key': 't1', 'gate': 'cleanup', 'attempt': 2, 'passed': True,
             'profile': 'fast', 'risk': 'LOW', 'task_type': 'feature', 'ts': 2, 'findings': []},
            {'event': 'gate', 'task_key': 't2', 'gate': 'cleanup', 'attempt': 1, 'passed': False,
             'profile': 'standard', 'risk': 'MEDIUM', 'task_type': 'bug', 'ts': 3,
             'findings': [{'hash': 'abc123456789', 'text': 'leftover debug in src/app.py:<n>'}]},
            {'event': 'gate', 'task_key': 't3', 'gate': 'cleanup', 'attempt': 1, 'passed': False,
             'profile': 'fast', 'risk': 'LOW', 'task_type': 'feature', 'ts': 4,
             'findings': [{'hash': 'zzz999', 'text': 'unrelated, only seen once per task'}]},
        ]
        patterns = workflow.detect_patterns(rows)
        self.assertEqual(len(patterns), 1)
        pattern = patterns[0]
        self.assertEqual(pattern['occurrences'], 2)
        self.assertEqual(pattern['distinct_tasks'], 2)
        self.assertEqual(pattern['resolved_next_attempt'], 1)
        self.assertEqual(pattern['by_profile'], {'fast': 1, 'standard': 1})
        self.assertEqual(pattern['scope_hint'], 'src/**')

    def test_outcome_stats_marks_low_evidence_below_min_n(self):
        thin = [
            {'event': 'gate', 'gate': 'checks', 'passed': True, 'attempt': 1, 'task_key': 't1', 'profile': 'fast'},
            {'event': 'gate', 'gate': 'checks', 'passed': True, 'attempt': 1, 'task_key': 't2', 'profile': 'fast'},
        ]
        stats = workflow.outcome_stats(thin, profile='fast', min_n=5)
        self.assertFalse(stats[0]['sufficient'])
        self.assertNotIn('pass_rate', stats[0])

        rich = [{'event': 'gate', 'gate': 'checks', 'passed': i != 0, 'attempt': 2 if i == 0 else 1,
                 'task_key': f't{i}', 'profile': 'fast',
                 'usage': {'input_tokens': 100, 'output_tokens': 10}} for i in range(6)]
        stats = workflow.outcome_stats(rich, profile='fast', min_n=5)
        row = stats[0]
        self.assertTrue(row['sufficient'])
        self.assertEqual(row['n'], 6)
        self.assertAlmostEqual(row['pass_rate'], 5 / 6, places=3)
        self.assertEqual(row['median_usage_tokens'], 110)

    def test_outcome_stats_usage_ignores_retry_thrash_and_never_passed_gates(self):
        # One task whose cleanup gate thrashed through 5 failing attempts and never
        # passed: it must not set the usage projection at all.
        thrash = [{'event': 'gate', 'gate': 'cleanup', 'passed': False, 'attempt': i + 1,
                   'task_key': 'stuck', 'profile': 'standard',
                   'usage': {'input_tokens': 200_000 + i * 50_000, 'output_tokens': 3_000}}
                  for i in range(5)]
        stats = workflow.outcome_stats(thrash, profile='standard', min_n=5)
        self.assertTrue(stats[0]['sufficient'])
        self.assertIsNone(stats[0]['median_usage_tokens'])

        # A gate that passes on the 2nd attempt contributes one sample: the sum of
        # both attempts (the real end-to-end cost of the run that worked), not two.
        rows = thrash + [
            {'event': 'gate', 'gate': 'cleanup', 'passed': False, 'attempt': 1, 'task_key': 't_a',
             'profile': 'standard', 'usage': {'input_tokens': 40_000, 'output_tokens': 1_000}},
            {'event': 'gate', 'gate': 'cleanup', 'passed': True, 'attempt': 2, 'task_key': 't_a',
             'profile': 'standard', 'usage': {'input_tokens': 30_000, 'output_tokens': 1_000}},
            {'event': 'gate', 'gate': 'cleanup', 'passed': True, 'attempt': 1, 'task_key': 't_b',
             'profile': 'standard', 'usage': {'input_tokens': 50_000, 'output_tokens': 2_000}},
        ]
        stats = workflow.outcome_stats(rows, profile='standard', min_n=5)
        # per-task successful-run totals: t_a -> 72_000, t_b -> 52_000; median -> 62_000
        self.assertEqual(stats[0]['median_usage_tokens'], 62_000)

    def test_analyze_ticket_text_extracts_acceptance_figma_and_blockers(self):
        text = (
            "Implement the retry handler.\n\n"
            "## Acceptance Criteria\n"
            "- Retries on 5xx\n"
            "- Gives up after 3 attempts\n\n"
            "Design: https://www.figma.com/file/abc123/Retry-Flow\n"
            "Blocked by: PROJ-42\n"
        )
        result = workflow.analyze_ticket_text(text)
        self.assertEqual(result['acceptance_items'], ['Retries on 5xx', 'Gives up after 3 attempts'])
        self.assertTrue(result['has_acceptance'])
        self.assertEqual(result['figma_url'], 'https://www.figma.com/file/abc123/Retry-Flow')
        self.assertEqual(result['blockers_mentioned'], ['PROJ-42'])

    def test_analyze_ticket_text_checklist_and_empty_input(self):
        checklist = "- [ ] Handles empty input\n- [x] Logs the error\n"
        result = workflow.analyze_ticket_text(checklist)
        self.assertEqual(result['acceptance_items'], ['Handles empty input', 'Logs the error'])

        empty = workflow.analyze_ticket_text("No structure here at all.")
        self.assertFalse(empty['has_acceptance'])
        self.assertIsNone(empty['figma_url'])
        self.assertEqual(empty['blockers_mentioned'], [])

    def test_bucket_ts_uses_utc_day_and_iso_week(self):
        import datetime
        ts = datetime.datetime(2026, 1, 1, 23, 30, tzinfo=datetime.timezone.utc).timestamp()
        self.assertEqual(workflow.bucket_ts(ts, 'day'), '2026-01-01')
        self.assertEqual(workflow.bucket_ts(ts, 'week'), '2026-W01')
        with self.assertRaises(ValueError):
            workflow.bucket_ts(ts, 'month')

    def test_parse_window_relative_and_absolute(self):
        now = time.time()
        self.assertAlmostEqual(workflow.parse_window('1d'), now - 86400, delta=2)
        self.assertAlmostEqual(workflow.parse_window('2w'), now - 2 * 604800, delta=2)
        import datetime
        expected = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc).timestamp()
        self.assertEqual(workflow.parse_window('2026-01-01'), expected)
        with self.assertRaises(ValueError):
            workflow.parse_window('nonsense')

    def test_usage_report_groups_windows_and_never_zero_fills(self):
        rows = [
            {'event': 'gate', 'ts': 100, 'gate': 'checks', 'passed': True, 'task_id': 't1', 'task_key': 'k1',
             'usage': {'input_tokens': 100, 'output_tokens': 10}},
            {'event': 'gate', 'ts': 100, 'gate': 'checks', 'passed': False, 'task_id': 't1', 'task_key': 'k1',
             'usage': {}},
            {'event': 'gate', 'ts': 200000, 'gate': 'cleanup', 'passed': True, 'task_id': 't2', 'task_key': 'k2',
             'usage': {'input_tokens': 5, 'output_tokens': 1}},
            {'event': 'pipeline', 'ts': 200000, 'status': 'PR_READY', 'task_id': 't2', 'task_key': 'k2'},
        ]
        by_gate = workflow.usage_report(rows, group_by='gate')
        checks = next(r for r in by_gate if r['key'] == 'checks')
        self.assertEqual(checks['gate_attempts'], 2)
        self.assertEqual(checks['reported_attempts'], 1)
        self.assertEqual(checks['unreported_attempts'], 1)
        self.assertEqual(checks['input_tokens'], 100)

        by_task = workflow.usage_report(rows, group_by='task', since=1000)
        self.assertEqual(len(by_task), 1)
        self.assertEqual(by_task[0]['key'], 'k2')
        self.assertEqual(by_task[0]['task_id'], 't2')
        self.assertEqual(by_task[0]['pipeline_runs'], 1)

        top = workflow.usage_report(rows, group_by='gate', top=1)
        self.assertEqual(len(top), 1)
        self.assertEqual(top[0]['key'], 'checks')

    def test_timeout_kills_child_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'survived'
            child = f'import time; time.sleep(0.8); open({str(marker)!r}, "w").write("alive")'
            parent = f'import subprocess, sys, time; subprocess.Popen([sys.executable, "-c", {child!r}]); time.sleep(10)'
            with (root / 'log').open('w') as output:
                code = workflow.execute([sys.executable, '-c', parent], root, dict(os.environ), output, 0.3)
            self.assertEqual(code, 124)
            time.sleep(0.8)
            self.assertFalse(marker.exists())


class CampaignReportTests(unittest.TestCase):
    """campaign_report(): the phase-6 instrumentation for a real-usage validation
    campaign. Never infers a false positive or an 'ignored' finding from outcomes
    alone -- both come from `gate_label` events or are reported as an explicitly
    caveated volume count (findings_raised)."""

    def test_percentile_nearest_rank_and_empty(self):
        self.assertIsNone(workflow._percentile([], 90))
        self.assertEqual(workflow._percentile([10], 90), 10)
        self.assertEqual(workflow._percentile([10, 20, 30, 40, 50], 0), 10)
        self.assertEqual(workflow._percentile([10, 20, 30, 40, 50], 100), 50)

    def _task(self, key, task_type, profile, started_at, ready_at=None, gates=()):
        """Build the rows one task contributes: a task_start, an optional PR_READY
        pipeline event, and the given gate attempts (each a dict of overrides)."""
        rows = [{'event': 'task_start', 'task_key': key, 'task_id': key, 'ts': started_at}]
        for gate in gates:
            row = {'event': 'gate', 'task_key': key, 'task_type': task_type, 'profile': profile,
                   'gate': gate['gate'], 'attempt': gate.get('attempt', 1), 'passed': gate.get('passed', True),
                   'duration_seconds': gate.get('duration_seconds', 0), 'findings': gate.get('findings', []),
                   'usage': gate.get('usage')}
            rows.append(row)
        rows.append({'event': 'plan', 'task_key': key, 'task_type': task_type, 'profile': profile, 'ts': started_at})
        if ready_at is not None:
            rows.append({'event': 'pipeline', 'task_key': key, 'status': 'PR_READY', 'ts': ready_at})
        return rows

    def test_time_to_ready_and_reached_flag(self):
        rows = self._task('k1', 'bug', 'fast', started_at=1000, ready_at=1300)
        report = workflow.campaign_report(rows)
        detail = report['tasks_detail'][0]
        self.assertEqual(detail['time_to_ready_seconds'], 300)
        self.assertTrue(detail['reached_pr_ready'])
        self.assertEqual(report['by_task_type']['bug']['n'], 1)
        self.assertEqual(report['by_task_type']['bug']['reached_pr_ready'], 1)

    def test_task_that_never_reached_ready_has_no_time_and_is_not_reached(self):
        rows = self._task('k1', 'feature', 'standard', started_at=1000)
        report = workflow.campaign_report(rows)
        detail = report['tasks_detail'][0]
        self.assertIsNone(detail['time_to_ready_seconds'])
        self.assertFalse(detail['reached_pr_ready'])
        self.assertEqual(report['reached_pr_ready'], 0)

    def test_falls_back_to_plan_ts_when_no_task_start_event(self):
        # Pre-lifecycle (phase 1) or branch-fallback tasks never recorded task_start.
        rows = [row for row in self._task('k1', 'bug', 'fast', started_at=1000, ready_at=1200)
               if row['event'] != 'task_start']
        report = workflow.campaign_report(rows)
        self.assertEqual(report['tasks_detail'][0]['time_to_ready_seconds'], 200)

    def test_retries_by_gate_and_max_retries(self):
        rows = self._task('k1', 'bug', 'fast', started_at=1000, ready_at=1500, gates=[
            {'gate': 'checks', 'attempt': 1, 'passed': False},
            {'gate': 'checks', 'attempt': 2, 'passed': True},
            {'gate': 'regression', 'attempt': 1, 'passed': True},
        ])
        detail = workflow.campaign_report(rows)['tasks_detail'][0]
        self.assertEqual(detail['retries_by_gate'], {'checks': 2, 'regression': 1})
        self.assertEqual(detail['max_retries'], 2)

    def test_findings_raised_counts_distinct_hashes_across_failed_attempts(self):
        rows = self._task('k1', 'bug', 'fast', started_at=1000, gates=[
            {'gate': 'checks', 'attempt': 1, 'passed': False,
             'findings': [{'hash': 'h1', 'text': 'x'}, {'hash': 'h2', 'text': 'y'}]},
            {'gate': 'checks', 'attempt': 2, 'passed': False, 'findings': [{'hash': 'h1', 'text': 'x'}]},
        ])
        detail = workflow.campaign_report(rows)['tasks_detail'][0]
        self.assertEqual(detail['findings_raised'], 2)
        self.assertIn("volume signal", workflow.campaign_report(rows)['findings_raised_caveat'])

    def test_tokens_summed_across_gate_attempts(self):
        rows = self._task('k1', 'bug', 'fast', started_at=1000, gates=[
            {'gate': 'checks', 'usage': {'input_tokens': 100, 'output_tokens': 10}},
            {'gate': 'regression', 'usage': {'input_tokens': 50, 'output_tokens': 5}},
        ])
        detail = workflow.campaign_report(rows)['tasks_detail'][0]
        self.assertEqual(detail['input_tokens'], 150)
        self.assertEqual(detail['output_tokens'], 15)

    def test_final_status_prefers_task_close_over_last_pipeline(self):
        rows = self._task('k1', 'bug', 'fast', started_at=1000, ready_at=1200)
        rows.append({'event': 'pipeline', 'task_key': 'k1', 'status': 'FAILED', 'ts': 1400})
        rows.append({'event': 'task_close', 'task_key': 'k1', 'readiness': 'PR_READY', 'ts': 1500})
        detail = workflow.campaign_report(rows)['tasks_detail'][0]
        self.assertEqual(detail['final_status'], 'PR_READY')

    def test_since_filters_rows_before_grouping(self):
        rows = self._task('k1', 'bug', 'fast', started_at=1000, ready_at=1200)
        rows += self._task('k2', 'bug', 'fast', started_at=500000, ready_at=500100)
        report = workflow.campaign_report(rows, since=100000)
        self.assertEqual(report['tasks'], 1)
        self.assertEqual(report['tasks_detail'][0]['task_key'], 'k2')

    def test_gate_labels_compute_false_positive_rate(self):
        rows = [
            {'event': 'gate_label', 'gate': 'security', 'label': 'false_positive'},
            {'event': 'gate_label', 'gate': 'security', 'label': 'false_positive'},
            {'event': 'gate_label', 'gate': 'security', 'label': 'true_positive'},
        ]
        stats = workflow.campaign_report(rows)['gate_labels']['security']
        self.assertEqual(stats, {'true_positive': 1, 'false_positive': 2, 'labeled': 3,
                                 'false_positive_rate': round(2 / 3, 3)})

    def test_unlabeled_gate_has_no_rate_not_zero(self):
        # No gate_label rows at all -> report['gate_labels'] must be empty, never a
        # fabricated 0% rate that looks like "this gate has no false positives".
        report = workflow.campaign_report(self._task('k1', 'bug', 'fast', 1000))
        self.assertEqual(report['gate_labels'], {})

    def test_recommendation_for_high_false_positive_rate_needs_minimum_sample(self):
        few = [{'event': 'gate_label', 'gate': 'security', 'label': 'false_positive'}] * 2
        self.assertEqual(workflow.campaign_report(few)['recommendations'], [])
        many = [{'event': 'gate_label', 'gate': 'security', 'label': 'false_positive'}] * 3 \
             + [{'event': 'gate_label', 'gate': 'security', 'label': 'true_positive'}] * 2
        recs = workflow.campaign_report(many)['recommendations']
        self.assertTrue(any('security' in r and 'advisory' in r for r in recs))

    def test_recommendation_for_p90_outlier_names_dominant_gate(self):
        rows = []
        for i, started in enumerate([0, 0, 0, 0, 0]):
            ready = 100 if i < 4 else 10000  # one big outlier among five fast tasks
            rows += self._task(f'k{i}', 'bug', 'fast', started_at=started, ready_at=ready, gates=[
                {'gate': 'review', 'duration_seconds': ready * 0.9},
            ])
        recs = workflow.campaign_report(rows)['recommendations']
        self.assertTrue(any('bug' in r and 'review' in r for r in recs))

    def test_recommendation_for_budget_needs_usage_budgets_argument(self):
        rows = self._task('k1', 'bug', 'fast', started_at=1000, gates=[
            {'gate': 'checks', 'usage': {'input_tokens': 39000, 'output_tokens': 1000}},
        ])
        without_budgets = workflow.campaign_report(rows)
        self.assertEqual(without_budgets['recommendations'], [])
        with_budgets = workflow.campaign_report(rows, usage_budgets={'fast': 40000})
        self.assertTrue(any('fast' in r and 'context_caps' in r for r in with_budgets['recommendations']))

    def test_by_profile_is_stratified_independently_of_task_type(self):
        rows = self._task('k1', 'bug', 'fast', started_at=0, ready_at=100)
        rows += self._task('k2', 'feature', 'fast', started_at=0, ready_at=200)
        report = workflow.campaign_report(rows)
        self.assertEqual(report['by_profile']['fast']['n'], 2)
        self.assertEqual(report['by_task_type']['bug']['n'], 1)

    def test_rows_without_a_task_key_are_ignored_not_a_crash(self):
        report = workflow.campaign_report([{'event': 'gate', 'gate': 'checks'}])
        self.assertEqual(report['tasks'], 0)


if __name__ == '__main__':
    unittest.main()
