import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
# ai_stack/ modules use bare same-directory imports; put it on sys.path first,
# exactly as running the file as a script would do implicitly.
sys.path.insert(0, str(ROOT / 'ai_stack'))
import benchmark  # noqa: E402


def run_script(script, cwd):
    """Execute a _bench_gate_script source string; return (returncode, parsed stdout JSON)."""
    result = subprocess.run([sys.executable, '-c', script], cwd=cwd, text=True, capture_output=True)
    return result, json.loads(result.stdout)


class CorpusDigestTests(unittest.TestCase):
    def test_same_list_same_digest(self):
        # corpus_digest is a plain sha over json.dumps(sort_keys=True): stable per serialisation.
        scenarios = [{'id': 'a', 'task': 'one'}, {'id': 'b', 'task': 'two'}]
        digest = benchmark.corpus_digest(scenarios)
        self.assertEqual(digest, benchmark.corpus_digest(list(scenarios)))
        self.assertEqual(len(digest), 16)
        int(digest, 16)  # 16-char hex

    def test_changed_field_changes_digest(self):
        base = [{'id': 'a', 'task': 'one'}, {'id': 'b', 'task': 'two'}]
        changed = [{'id': 'a', 'task': 'ONE'}, {'id': 'b', 'task': 'two'}]
        self.assertNotEqual(benchmark.corpus_digest(base), benchmark.corpus_digest(changed))

    def test_reorder_changes_digest_not_order_independent(self):
        # Order independence is explicitly NOT a property: a reordered list differs.
        base = [{'id': 'a'}, {'id': 'b'}, {'id': 'c'}]
        self.assertNotEqual(benchmark.corpus_digest(base), benchmark.corpus_digest(list(reversed(base))))


class LoadPipelineCorpusTests(unittest.TestCase):
    def test_reads_and_sorts_by_filename(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = Path(d)
            (corpus / 'b.json').write_text(json.dumps({'id': 'second', 'task': 't2'}))
            (corpus / 'a.json').write_text(json.dumps({'id': 'first', 'task': 't1'}))
            (corpus / 'notes.txt').write_text('ignored')  # non-json is skipped
            loaded = benchmark.load_pipeline_corpus(corpus)
            self.assertEqual([s['id'] for s in loaded], ['first', 'second'])

    def test_shipped_corpus_is_well_formed(self):
        scenarios = benchmark.load_pipeline_corpus(benchmark.BENCHMARK_CORPUS_DIR)
        self.assertTrue(scenarios)
        for s in scenarios:
            self.assertIn('id', s)
            self.assertIn('task', s)


class BenchGateScriptTests(unittest.TestCase):
    def test_default_spec_passes(self):
        with tempfile.TemporaryDirectory() as d:
            result, verdict = run_script(benchmark._bench_gate_script({}), d)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(verdict['status'], 'PASS')
            self.assertTrue(verdict['evidence'])

    def test_fail_spec_exits_nonzero_with_findings(self):
        with tempfile.TemporaryDirectory() as d:
            result, verdict = run_script(benchmark._bench_gate_script({'status': 'FAIL', 'findings': ['x']}), d)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(verdict['status'], 'FAIL')
            self.assertEqual(verdict['findings'], ['x'])

    def test_usage_is_carried_through(self):
        with tempfile.TemporaryDirectory() as d:
            _, verdict = run_script(benchmark._bench_gate_script({'usage': {'input_tokens': 5}}), d)
            self.assertEqual(verdict['usage'], {'input_tokens': 5})

    def test_mutate_writes_a_file_in_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            run_script(benchmark._bench_gate_script({'mutate': True}), d)
            self.assertTrue((Path(d) / 'bench-mutation.txt').is_file())

    def test_summary_markdown_is_carried_through(self):
        with tempfile.TemporaryDirectory() as d:
            _, verdict = run_script(benchmark._bench_gate_script({'summary_markdown': '# hi'}), d)
            self.assertEqual(verdict['summary_markdown'], '# hi')


class CheckBenchmarkExpectationsTests(unittest.TestCase):
    def result(self, **over):
        base = {'risk': 'LOW', 'required_gates': ['review'], 'readiness': 'PR_READY',
                'failed_gates': [], 'pipeline_output': 'all good'}
        base.update(over)
        return base

    def test_empty_expectation_is_no_violation(self):
        self.assertEqual(benchmark.check_benchmark_expectations(self.result(), {}), [])

    def test_risk_mismatch(self):
        v = benchmark.check_benchmark_expectations(self.result(risk='LOW'), {'risk': 'HIGH'})
        self.assertEqual(len(v), 1)
        self.assertIn('risk', v[0])

    def test_required_gates_missing(self):
        v = benchmark.check_benchmark_expectations(
            self.result(required_gates=['review']), {'required_gates_include': ['security', 'review']})
        self.assertEqual(len(v), 1)
        self.assertIn("['security']", v[0])

    def test_readiness_mismatch(self):
        v = benchmark.check_benchmark_expectations(
            self.result(readiness='NEEDS_HUMAN'), {'readiness': 'PR_READY'})
        self.assertEqual(len(v), 1)
        self.assertIn('readiness', v[0])

    def test_failed_gates_mismatch_and_order_insensitive(self):
        v = benchmark.check_benchmark_expectations(
            self.result(failed_gates=['cleanup']), {'failed_gates': ['checks']})
        self.assertEqual(len(v), 1)
        self.assertIn('failed_gates', v[0])
        # order-insensitive compare: ['a','b'] vs ['b','a'] is not a violation
        self.assertEqual(benchmark.check_benchmark_expectations(
            self.result(failed_gates=['a', 'b']), {'failed_gates': ['b', 'a']}), [])

    def test_pipeline_message_contains(self):
        v = benchmark.check_benchmark_expectations(
            self.result(pipeline_output='all good'), {'pipeline_message_contains': 'budget'})
        self.assertEqual(len(v), 1)
        self.assertEqual(benchmark.check_benchmark_expectations(
            self.result(pipeline_output='over budget now'), {'pipeline_message_contains': 'budget'}), [])

    def test_all_pass_case(self):
        expect = {'risk': 'LOW', 'required_gates_include': ['review'], 'readiness': 'PR_READY',
                  'failed_gates': [], 'pipeline_message_contains': 'good'}
        self.assertEqual(benchmark.check_benchmark_expectations(self.result(), expect), [])


class RunBenchmarkTests(unittest.TestCase):
    def test_shape_of_routing_report(self):
        # select_skills is patched out (documented alternative): run_benchmark's remaining
        # work — classify / classify_task / context_caps — is pure, so a bare tmp Path suffices.
        with tempfile.TemporaryDirectory() as d, \
                patch('benchmark.select_skills', return_value=['skill-x']):
            report = benchmark.run_benchmark(Path(d))
        self.assertEqual(report['fixtures'], len(benchmark.BENCHMARK_TASKS))
        self.assertEqual(len(report['results']), len(benchmark.BENCHMARK_TASKS))
        for row in report['results']:
            self.assertIn('id', row)
            self.assertIn('task', row)
            self.assertEqual(set(row['profiles']), {'fast', 'standard', 'strict'})
            for data in row['profiles'].values():
                for key in ('risk', 'security', 'task_type', 'skills', 'context_chars', 'usage_tokens'):
                    self.assertIn(key, data)
                self.assertEqual(data['skills'], ['skill-x'])


if __name__ == '__main__':
    unittest.main()
