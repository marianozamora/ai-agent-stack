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
# ai_stack/ modules use bare same-directory imports; putting ai_stack/ on
# sys.path makes `import learning` work, transitively pulling core/workflow.
sys.path.insert(0, str(ROOT / 'ai_stack'))
import core  # noqa: E402
import learning  # noqa: E402


def _sandbox(tc):
    """Isolated git repo + redirected external state, mirroring tests/test_crg.py::_sandbox."""
    tmp = tempfile.TemporaryDirectory(prefix='learning-test-')
    tc.addCleanup(tmp.cleanup)
    home = Path(tmp.name)
    repo = home / 'repo'
    repo.mkdir()
    cfg = home / 'config' / 'ai-agent-stack'

    env = {k: v for k, v in os.environ.items() if k not in ('AI_GATE', 'AI_TASK_DIR')}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'), AI_TASK_ID='learning-task')

    for args in (('init', '-q'), ('config', 'user.name', 'T'), ('config', 'user.email', 't@e.com')):
        subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True)
    (repo / 'app.txt').write_text('initial\n')
    subprocess.run(['git', 'add', '.'], cwd=repo, check=True, capture_output=True)
    subprocess.run(['git', 'commit', '-qm', 'init'], cwd=repo, check=True, capture_output=True)

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


class CmdLessonsAddTests(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _sandbox(self)

    def _args(self, text='keep functions small', scope=None, gate=None):
        return argparse.Namespace(lessons_cmd='add', text=text, scope=scope, gate=gate,
                                  status=None, json=False)

    def test_without_ai_gate_creates_a_confirmed_lesson_from_the_user(self):
        with contextlib.redirect_stdout(io.StringIO()):
            learning.cmd_lessons(self._args())
        lessons = learning.load_lessons(self.state)
        self.assertEqual(len(lessons), 1)
        self.assertEqual(lessons[0]['status'], 'confirmed')
        self.assertEqual(lessons[0]['origin'], 'user')
        self.assertEqual(lessons[0]['text'], 'keep functions small')

    def test_with_ai_gate_is_human_only_and_writes_nothing(self):
        with mock.patch.dict(os.environ, {'AI_GATE': 'checks'}):
            with self.assertRaises(SystemExit) as caught:
                learning.cmd_lessons(self._args())
        self.assertIn('human-only', str(caught.exception))
        self.assertEqual(learning.load_lessons(self.state), [])


class CmdLessonsPruneTests(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _sandbox(self)

    def _args(self, unseen_days=90):
        return argparse.Namespace(lessons_cmd='prune', unseen_days=unseen_days,
                                  status=None, json=False)

    def test_prunes_only_stale_candidates_and_keeps_confirmed_and_recent(self):
        now = 1_000_000.0
        old = now - 200 * 86400
        recent = now - 5 * 86400
        learning.save_lessons(self.state, [
            {'id': 'stale_candidate', 'status': 'candidate', 'last_seen': old, 'text': 't'},
            {'id': 'recent_candidate', 'status': 'candidate', 'last_seen': recent, 'text': 't'},
            {'id': 'stale_confirmed', 'status': 'confirmed', 'last_seen': old, 'text': 't'},
        ])
        with mock.patch.object(learning.time, 'time', return_value=now):
            with contextlib.redirect_stdout(io.StringIO()):
                learning.cmd_lessons(self._args(unseen_days=90))
        remaining = {l['id'] for l in learning.load_lessons(self.state)}
        self.assertEqual(remaining, {'recent_candidate', 'stale_confirmed'})


class CmdConfidenceTextTests(unittest.TestCase):
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

        prev_cwd = os.getcwd()
        self.addCleanup(os.chdir, prev_cwd)
        os.chdir(self.repo)

        gitp = mock.patch.object(learning, 'git_root', return_value=self.repo)
        gitp.start(); self.addCleanup(gitp.stop)
        statep = mock.patch.object(learning, 'repo_state', return_value=self.state)
        statep.start(); self.addCleanup(statep.stop)

    def _args(self, profile='fast'):
        return argparse.Namespace(base='HEAD', profile=profile, json=False)

    def test_low_evidence_line_with_few_samples(self):
        # Below outcome_stats' min_n (5), a gate's stats stay LOW_EVIDENCE rather
        # than reporting a rate computed on too few observations.
        rows = [{'event': 'gate', 'gate': 'checks', 'passed': True, 'attempt': 1,
                 'task_key': f't{i}', 'profile': 'fast', 'risk': 'LOW'} for i in range(2)]
        (self.state / 'metrics.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            learning.cmd_confidence(self._args())
        out = buf.getvalue()
        self.assertIn('LOW_EVIDENCE (n=2)', out)

    def test_exceeds_budget_note_when_projected_usage_over_budget(self):
        # `fast` gates the projection over its usage_tokens budget; a batch of
        # high-usage passing rows for every fast-profile required gate pushes
        # the projected sum over context_caps('fast')['usage_tokens'].
        from core import classify, collect_scope, context_caps, required_gates
        budget = context_caps('fast')['usage_tokens']
        scope = collect_scope(self.repo, 'HEAD')
        risk = classify(scope, 'fast')
        gates = required_gates(self.repo, {'scope': scope, 'profile': 'fast',
                                           'risk': risk, 'figma': None})
        self.assertTrue(gates)  # otherwise this test proves nothing
        per_gate_tokens = budget + 1000  # each required gate alone exceeds the whole budget
        rows = []
        for gate in gates:
            for i in range(6):
                rows.append({'event': 'gate', 'gate': gate, 'passed': True, 'attempt': 1,
                             'task_key': f'{gate}-{i}', 'profile': 'fast', 'risk': 'LOW',
                             'usage': {'input_tokens': per_gate_tokens, 'output_tokens': 0}})
        (self.state / 'metrics.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            learning.cmd_confidence(self._args())
        out = buf.getvalue()
        self.assertIn('Projected usage:', out)
        self.assertIn('(exceeds budget)', out)


if __name__ == '__main__':
    unittest.main()
