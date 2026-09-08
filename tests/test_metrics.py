import json
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


if __name__ == '__main__':
    unittest.main()
