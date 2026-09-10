"""Performance/budget category: pins exact enforce_budget() boundaries and
confirms per-profile context budgets actually scale end to end, not just in
context_caps()'s own table. Run only this file with:
    python3 -m unittest discover -s tests -p 'test_performance_budgets.py'
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import core  # noqa: E402
import skills  # noqa: E402


class EnforceBudgetBoundaryTests(unittest.TestCase):
    def test_text_at_exact_limit_passes(self):
        core.enforce_budget('x' * 100, 100, 'label')  # must not raise

    def test_text_one_over_limit_fails(self):
        with self.assertRaises(SystemExit) as ctx:
            core.enforce_budget('x' * 101, 100, 'label')
        self.assertIn('exceeds budget', str(ctx.exception))
        self.assertIn('101 > 100', str(ctx.exception))


NO_REPO = None  # no repository in play: bundled skills only


class LoadSkillContextBudgetTests(unittest.TestCase):
    def test_no_names_returns_placeholder(self):
        self.assertEqual(skills.load_skill_context(None, [], 5000), '(none)')

    def test_body_within_budget_is_kept_whole(self):
        out = skills.load_skill_context(NO_REPO, ['tdd'], 100000)
        self.assertIn('## Skill: tdd', out)
        self.assertNotIn('[skill context truncated by budget]', out)

    def test_body_over_budget_is_truncated_with_marker(self):
        full = skills.load_skill_context(NO_REPO, ['tdd'], 100000)
        body_len = len(full) - len('## Skill: tdd\n')
        out = skills.load_skill_context(NO_REPO, ['tdd'], body_len // 2)
        self.assertIn('[skill context truncated by budget]', out)
        self.assertLess(len(out), len(full))


class ProfileBudgetScalingIntegrationTests(unittest.TestCase):
    """Confirms the *real* orchestration prompt (not just context_caps()'s raw
    numbers) is rejected under a smaller profile and accepted under a larger
    one at the same task size - i.e. the scaling in core.context_caps actually
    reaches enforce_budget() through build_prompt(), not just in isolation."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='budget-test-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.repo = self.home / 'repo'
        self.repo.mkdir()
        base_env = {k: v for k, v in os.environ.items() if k not in ('AI_GATE', 'AI_TASK_DIR')}
        self.env = dict(base_env, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / 'config'), AI_TASK_ID='budget')
        for args in [('init', '-q'), ('config', 'user.name', 'Test'), ('config', 'user.email', 'test@example.com')]:
            subprocess.check_output(['git', *args], cwd=self.repo, env=self.env, text=True)
        (self.repo / 'app.txt').write_text('initial\n')
        subprocess.check_output(['git', 'add', '.'], cwd=self.repo, env=self.env, text=True)
        subprocess.check_output(['git', 'commit', '-qm', 'initial'], cwd=self.repo, env=self.env, text=True)

    def ai(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(ROOT / 'ai_stack/cli.py'), *args], cwd=self.repo, env=self.env, text=True, capture_output=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def test_budget_enforcement_scales_with_profile(self):
        # Measure the fixed prompt overhead empirically (skills/lessons/policy text
        # that isn't the task string) instead of hardcoding it, so this doesn't
        # silently rot if the orchestration template grows or shrinks.
        baseline_task = 'baseline sizing task'
        self.ai('plan', baseline_task, '--profile', 'strict', '--base', 'HEAD')
        state = Path(self.ai('path').strip())
        overhead = len((state / 'state/current-run.md').read_text()) - len(baseline_task)

        fast_cap = core.context_caps('fast')['context_chars']
        standard_cap = core.context_caps('standard')['context_chars']
        self.assertLess(fast_cap, standard_cap)  # sanity: the profiles actually differ

        # A task sized to land clearly between the two caps once overhead is added.
        margin = 500
        task_len = fast_cap + margin - overhead
        self.assertGreater(task_len, 0, 'fast cap is smaller than fixed prompt overhead; adjust margin')
        task = 'x' * task_len

        self.assertIn('exceeds budget', self.ai('plan', task, '--profile', 'fast', '--base', 'HEAD', ok=False))
        self.assertIn('AI plan', self.ai('plan', task, '--profile', 'standard', '--base', 'HEAD'))


if __name__ == '__main__':
    unittest.main()
