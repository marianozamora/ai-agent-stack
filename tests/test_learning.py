import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# ai_stack/ modules use bare same-directory imports; putting ai_stack/ on
# sys.path makes `import learning` work, transitively pulling core/workflow.
sys.path.insert(0, str(ROOT / 'ai_stack'))
import learning  # noqa: E402


class ScopeMatchesTests(unittest.TestCase):
    def test_none_and_double_star_match_anything(self):
        self.assertTrue(learning.scope_matches(None, ['src/a.py']))
        self.assertTrue(learning.scope_matches('**', ['src/a.py']))
        # '**'/None short-circuit before the any(...) over files, so they match [] too
        self.assertTrue(learning.scope_matches(None, []))
        self.assertTrue(learning.scope_matches('**', []))

    def test_glob_matches_only_files_under_prefix(self):
        self.assertTrue(learning.scope_matches('src/**', ['src/a/b.py']))
        self.assertFalse(learning.scope_matches('src/**', ['lib/x.py']))
        self.assertFalse(learning.scope_matches('src/**', []))


class RenderLessonsTests(unittest.TestCase):
    def test_empty_renders_none(self):
        self.assertEqual(learning.render_lessons([], 1000), '(none)')

    def test_formats_scope_text_and_counts(self):
        line = learning.render_lessons(
            [{'id': 'x', 'scope': 'src/**', 'text': 'remove debug prints',
              'observations': 4, 'distinct_tasks': 2}], 1000)
        self.assertEqual(line, '- [src/**] remove debug prints (observed 4x across 2 task(s))')

    def test_defaults_when_fields_missing(self):
        line = learning.render_lessons([{'id': 'y', 'text': 't'}], 1000)
        self.assertEqual(line, '- [**] t (observed 1x across 1 task(s))')

    def test_truncates_with_budget_marker(self):
        lessons = [{'id': f'l{i}', 'scope': 'src/**', 'text': 'a long lesson body ' * 5,
                    'observations': 1, 'distinct_tasks': 1} for i in range(10)]
        out = learning.render_lessons(lessons, 60)
        self.assertTrue(out.endswith('\n[lesson context truncated by budget]'))
        self.assertTrue(out.startswith('- [src/**]'))


class LoadSaveLessonsTests(unittest.TestCase):
    def test_missing_file_is_empty_list(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(learning.load_lessons(Path(d)), [])

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            data = [{'id': 'les_1', 'status': 'confirmed', 'text': 'x'}]
            learning.save_lessons(state, data)
            self.assertEqual(learning.load_lessons(state), data)


class FindLessonTests(unittest.TestCase):
    def test_found_returns_dict(self):
        lessons = [{'id': 'a'}, {'id': 'b'}]
        self.assertIs(learning.find_lesson(lessons, 'b'), lessons[1])

    def test_missing_raises_systemexit(self):
        with self.assertRaises(SystemExit):
            learning.find_lesson([{'id': 'a'}], 'nope')


class SelectLessonsTests(unittest.TestCase):
    def test_fast_profile_returns_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(learning.select_lessons(Path(d), {'files': ['src/a.py']}, 'fast'), [])

    def test_only_confirmed_scope_matching_sorted_and_capped(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            learning.save_lessons(state, [
                {'id': 'l_low', 'status': 'confirmed', 'scope': 'src/**', 'text': 't',
                 'observations': 5, 'last_seen': 100},
                {'id': 'l_high', 'status': 'confirmed', 'scope': 'src/**', 'text': 't',
                 'observations': 5, 'last_seen': 200},   # same observations, newer -> sorts first
                {'id': 'l_star', 'status': 'confirmed', 'scope': '**', 'text': 't',
                 'observations': 1, 'last_seen': 999},
                {'id': 'l_extra', 'status': 'confirmed', 'scope': 'src/**', 'text': 't',
                 'observations': 2, 'last_seen': 50},
                {'id': 'l_candidate', 'status': 'candidate', 'scope': 'src/**', 'text': 't',
                 'observations': 9, 'last_seen': 999},    # excluded: not confirmed
                {'id': 'l_offscope', 'status': 'confirmed', 'scope': 'lib/**', 'text': 't',
                 'observations': 9, 'last_seen': 999},    # excluded: scope misses the files
            ])
            scope = {'files': ['src/a.py']}
            picked = learning.select_lessons(state, scope, 'standard')  # top_k = 3
            self.assertEqual([l['id'] for l in picked], ['l_high', 'l_low', 'l_extra'])

            picked_strict = learning.select_lessons(state, scope, 'strict')  # top_k = 5
            self.assertEqual([l['id'] for l in picked_strict],
                             ['l_high', 'l_low', 'l_extra', 'l_star'])


def _failure_rows():
    """Two gate-failure attempts on the same finding hash across two distinct tasks:
    detect_patterns() turns this into exactly one pattern (see test_workflow_helpers)."""
    finding = {'hash': 'abc123456789', 'text': 'leftover debug in src/app.py:12'}
    return [
        {'event': 'gate', 'task_key': 't1', 'gate': 'cleanup', 'attempt': 1, 'passed': False,
         'profile': 'fast', 'risk': 'LOW', 'task_type': 'feature', 'ts': 1, 'findings': [finding]},
        {'event': 'gate', 'task_key': 't2', 'gate': 'cleanup', 'attempt': 1, 'passed': False,
         'profile': 'standard', 'risk': 'MEDIUM', 'task_type': 'bug', 'ts': 3, 'findings': [finding]},
    ]


class RebuildPatternsTests(unittest.TestCase):
    def test_returns_report_and_writes_file(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            rows = _failure_rows()
            (state / 'metrics.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
            report = learning.rebuild_patterns(state)
            self.assertEqual(report['version'], 1)
            self.assertEqual(report['source_events'], len(rows))
            self.assertEqual(len(report['patterns']), 1)
            on_disk = json.loads((state / 'patterns.json').read_text())
            self.assertEqual(on_disk['patterns'], report['patterns'])


class DeriveLessonsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        (self.state / 'metrics.jsonl').write_text(
            '\n'.join(json.dumps(r) for r in _failure_rows()) + '\n')

    def test_first_call_creates_candidate_lesson(self):
        created, updated = learning.derive_lessons(self.state)
        self.assertGreaterEqual(created, 1)
        self.assertEqual(updated, 0)
        lessons = learning.load_lessons(self.state)
        self.assertEqual(len(lessons), 1)
        self.assertEqual(lessons[0]['status'], 'candidate')
        self.assertEqual(lessons[0]['origin'], 'pattern')
        self.assertEqual(lessons[0]['id'], 'les_abc123456789')

    def test_idempotent_and_never_touches_a_confirmed_lesson(self):
        learning.derive_lessons(self.state)
        created, _ = learning.derive_lessons(self.state)
        self.assertEqual(created, 0)
        self.assertEqual(len(learning.load_lessons(self.state)), 1)  # no duplicate

        lessons = learning.load_lessons(self.state)
        lessons[0]['status'] = 'confirmed'
        original_text = lessons[0]['text']
        learning.save_lessons(self.state, lessons)

        created, updated = learning.derive_lessons(self.state)
        self.assertEqual(created, 0)
        after = learning.load_lessons(self.state)
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]['status'], 'confirmed')
        self.assertEqual(after[0]['text'], original_text)


class ConfidenceCardTests(unittest.TestCase):
    """confidence_card() calls collect_scope()/git_root(), so it needs a real repo."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / 'repo'
        self.repo.mkdir()

        state_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(state_tmp.cleanup)
        self.state = Path(state_tmp.name)

        home = Path(self.tmp.name) / 'home'
        home.mkdir()
        base_env = {k: v for k, v in os.environ.items() if k not in ('AI_GATE', 'AI_TASK_DIR')}
        self.env = dict(base_env, HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'))
        for args in [('init', '-q'), ('config', 'user.name', 'Test'),
                     ('config', 'user.email', 'test@example.com'),
                     ('config', 'commit.gpgsign', 'false')]:
            subprocess.check_call(['git', *args], cwd=self.repo, env=self.env)
        (self.repo / 'app.txt').write_text('initial\n')
        subprocess.check_call(['git', 'add', '.'], cwd=self.repo, env=self.env)
        subprocess.check_call(['git', 'commit', '-qm', 'initial'], cwd=self.repo, env=self.env)
        (self.repo / 'app.txt').write_text('a small working-tree change\n')

        prev_cwd = os.getcwd()
        self.addCleanup(os.chdir, prev_cwd)
        os.chdir(self.repo)

    def test_card_shape_with_no_history(self):
        card = learning.confidence_card(self.repo, self.state, 'HEAD', 'fast')
        for key in ('risk', 'required_gates', 'stats', 'weakest_gate',
                    'projected_usage_tokens', 'usage_budget'):
            self.assertIn(key, card)
        self.assertIsInstance(card['required_gates'], list)
        self.assertIsInstance(card['stats'], list)
        self.assertIsNone(card['weakest_gate'])            # no metrics -> no history
        self.assertTrue(all(not s['sufficient'] for s in card['stats']))
        self.assertEqual(card['projected_usage_tokens'], 0)

    def test_weakest_gate_populates_once_a_required_gate_has_enough_samples(self):
        rows = [{'event': 'gate', 'gate': 'checks', 'passed': True, 'attempt': 1,
                 'task_key': f't{i}', 'profile': 'fast', 'risk': 'LOW'} for i in range(6)]
        (self.state / 'metrics.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
        card = learning.confidence_card(self.repo, self.state, 'HEAD', 'fast')
        self.assertIn('checks', card['required_gates'])
        self.assertIsNotNone(card['weakest_gate'])
        self.assertEqual(card['weakest_gate']['gate'], 'checks')
        self.assertEqual(card['weakest_gate']['n'], 6)
        self.assertEqual(card['weakest_gate']['pass_rate'], 1.0)


if __name__ == '__main__':
    unittest.main()
