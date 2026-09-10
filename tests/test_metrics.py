import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
# ai_stack/ modules use bare same-directory imports; putting ai_stack/ on
# sys.path makes `import metrics` work, transitively pulling core/workflow.
sys.path.insert(0, str(ROOT / 'ai_stack'))
import metrics  # noqa: E402


class LoadMetricRowsTests(unittest.TestCase):
    def test_missing_file_returns_empty_and_zero(self):
        """No metrics.jsonl at all -> ([], 0), never an exception."""
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(metrics.load_metric_rows(Path(d)), ([], 0))

    def test_keeps_only_dict_rows_and_counts_malformed(self):
        """Non-dict JSON lines and unparseable lines are both counted as malformed."""
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            (state / 'metrics.jsonl').write_text(
                '\n'.join([
                    json.dumps({'event': 'gate', 'n': 1}),
                    '5',            # valid JSON, not a dict
                    '[]',           # valid JSON, not a dict
                    '{not valid',   # unparseable
                    json.dumps({'event': 'plan', 'n': 2}),
                ]) + '\n')
            rows, malformed = metrics.load_metric_rows(state)
            self.assertEqual(rows, [{'event': 'gate', 'n': 1}, {'event': 'plan', 'n': 2}])
            self.assertEqual(malformed, 3)

    def test_index_tracks_appends_and_rebuilds_after_jsonl_rewrite(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            log = state / 'metrics.jsonl'
            log.write_text(json.dumps({'event': 'gate', 'n': 1}) + '\n')
            self.assertEqual(metrics.load_metric_rows(state)[0], [{'event': 'gate', 'n': 1}])
            self.assertTrue((state / 'metrics.sqlite3').is_file())

            with log.open('a') as output:
                output.write(json.dumps({'event': 'plan', 'n': 2}) + '\n')
            self.assertEqual([row['n'] for row in metrics.load_metric_rows(state)[0]], [1, 2])

            log.write_text(json.dumps({'event': 'pipeline', 'n': 9}) + '\n')
            self.assertEqual(metrics.load_metric_rows(state)[0], [{'event': 'pipeline', 'n': 9}])

    def test_filters_are_served_by_the_index(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            (state / 'metrics.jsonl').write_text('\n'.join(json.dumps(row) for row in [
                {'event': 'gate', 'task_key': 'a'}, {'event': 'gate', 'task_key': 'b'},
                {'event': 'plan', 'task_key': 'a'}]) + '\n')
            rows, malformed = metrics.load_metric_rows(state, event='gate', task_key='a')
            self.assertEqual(rows, [{'event': 'gate', 'task_key': 'a'}])
            self.assertEqual(malformed, 0)


class MetricIndexHealthTests(unittest.TestCase):
    def test_no_log_yet(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(metrics.metric_index_health(Path(d)), 'no metrics recorded yet')

    def test_ok_reports_the_indexed_count_and_malformed_lines(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            (state / 'metrics.jsonl').write_text('\n'.join([
                json.dumps({'event': 'gate', 'n': 1}), '{bad', json.dumps({'event': 'plan', 'n': 2})]) + '\n')
            health = metrics.metric_index_health(state)
            self.assertEqual(health, 'OK: 2 events indexed, 1 malformed line(s) skipped')

    def test_an_index_left_behind_by_a_rewrite_reconciles_to_ok(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            log = state / 'metrics.jsonl'
            log.write_text(json.dumps({'event': 'gate', 'n': 1}) + '\n')
            metrics.metric_index_health(state)  # builds the index
            # A full rewrite (what `ai metrics prune` does) leaves the index describing
            # the old file; the health check must sync it and report OK, not a mismatch.
            log.write_text('\n'.join(json.dumps({'event': 'gate', 'n': k}) for k in (1, 2, 3)) + '\n')
            self.assertEqual(metrics.metric_index_health(state), 'OK: 3 events indexed')
            self.assertEqual([r['n'] for r in metrics.load_metric_rows(state)[0]], [1, 2, 3])

    def test_a_corrupt_index_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            (state / 'metrics.jsonl').write_text(json.dumps({'event': 'gate', 'n': 1}) + '\n')
            (state / 'metrics.sqlite3').write_bytes(b'not a database at all')
            health = metrics.metric_index_health(state)
            self.assertIn('corrupt', health)
            self.assertIn('re-indexed', health)


class RecordMetricTests(unittest.TestCase):
    """record_metric only needs task_state() to hand back a dir holding task.json
    and state/current-plan.json, so we patch metrics.task_state to a tmp task dir
    rather than standing up a whole git sandbox."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.task = self.state / 'tasks' / 'mytask'
        (self.task / 'state').mkdir(parents=True)
        (self.task / 'task.json').write_text(json.dumps({'id': 'task-abc'}))
        (self.task / 'state' / 'current-plan.json').write_text(
            json.dumps({'profile': 'fast', 'risk': {'risk': 'LOW'}, 'task_type': 'feature'}))
        patcher = patch.object(metrics, 'task_state', lambda state: self.task)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_appends_one_enriched_json_line(self):
        """A single call writes exactly one row carrying event/stack_version/ts and **data."""
        before = time.time()
        metrics.record_metric(self.state, 'gate', gate='checks', attempt=1)
        lines = (self.state / 'metrics.jsonl').read_text().splitlines()
        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        self.assertEqual(row['event'], 'gate')
        self.assertEqual(row['stack_version'], metrics.VERSION)
        self.assertGreaterEqual(row['ts'], before)
        self.assertEqual(row['gate'], 'checks')
        self.assertEqual(row['attempt'], 1)
        # enrichment pulled from the patched task dir
        self.assertEqual(row['task_key'], 'mytask')
        self.assertEqual(row['task_id'], 'task-abc')
        self.assertEqual(row['profile'], 'fast')
        self.assertEqual(row['task_type'], 'feature')

    def test_second_call_appends_and_does_not_overwrite(self):
        metrics.record_metric(self.state, 'gate', gate='checks')
        metrics.record_metric(self.state, 'pipeline', status='PR_READY')
        rows = [json.loads(x) for x in (self.state / 'metrics.jsonl').read_text().splitlines()]
        self.assertEqual([r['event'] for r in rows], ['gate', 'pipeline'])


class GateAttemptNumberTests(unittest.TestCase):
    def test_counts_only_matching_task_and_gate(self):
        """N prior gate rows for (task_key, gate) -> N+1; other tasks/gates/events ignored."""
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            (state / 'metrics.jsonl').write_text('\n'.join(json.dumps(r) for r in [
                {'event': 'gate', 'task_key': 'T', 'gate': 'checks'},
                {'event': 'gate', 'task_key': 'T', 'gate': 'checks'},
                {'event': 'gate', 'task_key': 'T', 'gate': 'checks'},
                {'event': 'gate', 'task_key': 'T', 'gate': 'cleanup'},    # other gate
                {'event': 'gate', 'task_key': 'OTHER', 'gate': 'checks'}, # other task
                {'event': 'plan', 'task_key': 'T', 'gate': 'checks'},     # not a gate event
                {'event': 'gate', 'task_key': 'T'},                       # no gate field
            ]) + '\n')
            self.assertEqual(metrics.gate_attempt_number(state, 'T', 'checks'), 4)
            self.assertEqual(metrics.gate_attempt_number(state, 'T', 'cleanup'), 2)
            self.assertEqual(metrics.gate_attempt_number(state, 'MISSING', 'checks'), 1)


class AppendEventAndRecordLabelTests(unittest.TestCase):
    def test_append_event_writes_exactly_the_given_row(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            metrics.append_event(state, {'event': 'custom', 'x': 1})
            lines = (state / 'metrics.jsonl').read_text().splitlines()
            self.assertEqual(json.loads(lines[0]), {'event': 'custom', 'x': 1})

    def test_record_label_does_not_touch_the_active_task(self):
        """record_label() must never resolve through task_state(): a label is very
        often filed against a task that is not the one currently active."""
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            with patch.object(metrics, 'task_state', side_effect=AssertionError('must not be called')):
                metrics.record_label(state, task_key='closed-task', gate='security', attempt=1,
                                     passed=False, label='false_positive', note='reviewed by hand')
            row = json.loads((state / 'metrics.jsonl').read_text().splitlines()[0])
            self.assertEqual(row['event'], 'gate_label')
            self.assertEqual(row['task_key'], 'closed-task')
            self.assertEqual(row['gate'], 'security')
            self.assertEqual(row['label'], 'false_positive')
            self.assertEqual(row['note'], 'reviewed by hand')
            self.assertIn('stack_version', row)

    def test_record_task_label_writes_a_task_label_event(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            with patch.object(metrics, 'task_state', side_effect=AssertionError('must not be called')):
                metrics.record_task_label(state, task_key='closed-task', label='incorrect',
                                          note='reviewer found an unhandled path')
            row = json.loads((state / 'metrics.jsonl').read_text().splitlines()[0])
            self.assertEqual(row['event'], 'task_label')
            self.assertEqual(row['task_key'], 'closed-task')
            self.assertEqual(row['label'], 'incorrect')
            self.assertEqual(row['note'], 'reviewer found an unhandled path')
            self.assertNotIn('gate', row)
            self.assertIn('stack_version', row)


class CmdMetricsLabelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        (self.state / 'metrics.jsonl').write_text('\n'.join(json.dumps(r) for r in [
            {'event': 'gate', 'task_key': 'T1', 'gate': 'checks', 'attempt': 1, 'passed': False},
            {'event': 'gate', 'task_key': 'T1', 'gate': 'checks', 'attempt': 2, 'passed': True},
        ]) + '\n')
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop('AI_GATE', None)
        os.environ.pop('AI_TASK_DIR', None)

    def _ns(self, **fields):
        defaults = dict(gate='checks', task_key='T1', attempt=None, true_positive=False,
                        false_positive=False, correct=False, incorrect=False, note=None)
        return argparse.Namespace(**{**defaults, **fields})

    def test_defaults_to_the_most_recent_attempt(self):
        # attempt 2 is the most recent for (T1, checks), but it PASSED -- refused.
        with self.assertRaises(SystemExit) as caught:
            metrics.cmd_metrics_label(self._ns(true_positive=True), self.state)
        self.assertIn('Only a FAILED gate attempt', str(caught.exception))

    def test_explicit_attempt_selects_the_failed_one(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            metrics.cmd_metrics_label(self._ns(attempt=1, false_positive=True), self.state)
        self.assertIn('checks attempt=1 -> false_positive', buf.getvalue())
        rows = [json.loads(x) for x in (self.state / 'metrics.jsonl').read_text().splitlines()]
        labels = [r for r in rows if r['event'] == 'gate_label']
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0]['attempt'], 1)

    def test_passed_attempt_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            metrics.cmd_metrics_label(self._ns(attempt=2, true_positive=True), self.state)
        self.assertIn('Only a FAILED gate attempt', str(caught.exception))

    def test_no_matching_attempt_is_a_clear_error(self):
        with self.assertRaises(SystemExit) as caught:
            metrics.cmd_metrics_label(self._ns(gate='regression', true_positive=True), self.state)
        self.assertIn("No recorded 'regression' gate attempt", str(caught.exception))

    def test_no_matching_attempt_number_is_a_clear_error(self):
        with self.assertRaises(SystemExit) as caught:
            metrics.cmd_metrics_label(self._ns(attempt=9, true_positive=True), self.state)
        self.assertIn('attempt 9', str(caught.exception))

    def test_requires_a_human_outside_a_gate(self):
        os.environ['AI_GATE'] = 'checks'
        try:
            with self.assertRaises(SystemExit):
                metrics.cmd_metrics_label(self._ns(attempt=1, false_positive=True), self.state)
        finally:
            del os.environ['AI_GATE']
        # Nothing was appended by the refused call.
        rows = [json.loads(x) for x in (self.state / 'metrics.jsonl').read_text().splitlines()]
        self.assertEqual([r for r in rows if r['event'] == 'gate_label'], [])

    def test_certification_label_records_a_task_label_event(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            metrics.cmd_metrics_label(self._ns(gate=None, incorrect=True,
                                               note='not actually ready'), self.state)
        self.assertIn('T1 certification -> incorrect', buf.getvalue())
        rows = [json.loads(x) for x in (self.state / 'metrics.jsonl').read_text().splitlines()]
        labels = [r for r in rows if r['event'] == 'task_label']
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0]['label'], 'incorrect')
        self.assertEqual(labels[0]['note'], 'not actually ready')

    def test_certification_label_rejects_a_gate_argument(self):
        with self.assertRaises(SystemExit) as caught:
            metrics.cmd_metrics_label(self._ns(gate='checks', incorrect=True), self.state)
        self.assertIn('drop the gate argument', str(caught.exception))

    def test_gate_verdict_without_a_gate_is_rejected(self):
        with self.assertRaises(SystemExit) as caught:
            metrics.cmd_metrics_label(self._ns(gate=None, false_positive=True), self.state)
        self.assertIn('name the gate', str(caught.exception))

    def test_certification_label_on_an_unknown_task_key_is_a_clear_error(self):
        with self.assertRaises(SystemExit) as caught:
            metrics.cmd_metrics_label(self._ns(gate=None, task_key='ghost', correct=True), self.state)
        self.assertIn("No recorded events for task 'ghost'", str(caught.exception))

    def test_certification_label_needs_a_human_outside_a_gate(self):
        os.environ['AI_GATE'] = 'checks'
        try:
            with self.assertRaises(SystemExit):
                metrics.cmd_metrics_label(self._ns(gate=None, correct=True), self.state)
        finally:
            del os.environ['AI_GATE']
        rows = [json.loads(x) for x in (self.state / 'metrics.jsonl').read_text().splitlines()]
        self.assertEqual([r for r in rows if r['event'] == 'task_label'], [])


if __name__ == '__main__':
    unittest.main()
