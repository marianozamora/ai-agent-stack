"""Performance/budget category: pins exact enforce_budget() boundaries and
confirms per-profile context budgets actually scale end to end, not just in
context_caps()'s own table. Run only this file with:
    python3 -m unittest discover -s tests -p 'test_performance_budgets.py'
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import capabilities  # noqa: E402
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


class CapabilityDetectionBudgetTests(unittest.TestCase):
    """The capability registry (ai_stack/capabilities.py) must only ever shrink the
    prompt relative to detecting nothing, and both extremes must fit their budget --
    confirmed against a real `ai plan` subprocess, not just the renderer in isolation.

    Real dev/CI machines running this suite may genuinely have some of these
    binaries on PATH (this repository's own toolchain includes tools like these),
    so "nothing installed" cannot simply mean "this test process's real PATH" --
    it needs its own restricted allowlist directory, the same technique
    test_workflow.py's gh-free-PATH test already uses, containing only what `ai
    plan --profile fast` actually needs to run (git) and nothing capability-shaped.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cap-budget-test-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.repo = self.home / 'repo'
        self.repo.mkdir()

        git_path = shutil.which('git')
        self.assertIsNotNone(git_path, 'git must be on PATH for this test to mean anything')
        self.allowlist_dir = self.home / 'allowlist-bin'
        self.allowlist_dir.mkdir()
        (self.allowlist_dir / 'git').symlink_to(git_path)

        base_env = {k: v for k, v in os.environ.items() if k not in ('AI_GATE', 'AI_TASK_DIR')}
        self.env = dict(base_env, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / 'config'),
                        AI_TASK_ID='cap-budget', PATH=str(self.allowlist_dir))
        for args in [('init', '-q'), ('config', 'user.name', 'Test'), ('config', 'user.email', 'test@example.com')]:
            subprocess.check_output(['git', *args], cwd=self.repo, env=self.env, text=True)
        (self.repo / 'app.txt').write_text('initial\n')
        subprocess.check_output(['git', 'add', '.'], cwd=self.repo, env=self.env, text=True)
        subprocess.check_output(['git', 'commit', '-qm', 'initial'], cwd=self.repo, env=self.env, text=True)

    def ai(self, *args, env):
        result = subprocess.run([sys.executable, str(ROOT / 'ai_stack/cli.py'), *args],
                                cwd=self.repo, env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def env_with_fake_capabilities(self):
        """The same restricted PATH, with a stub executable added for every
        capability binary -- so exactly those five are newly detectable, on top
        of the same minimal (git-only) baseline the "nothing installed" case uses.

        --profile fast is used everywhere this is passed to, specifically so
        build_prompt's separate `crg_impact()` codepath (profile != 'fast' and
        crg_cmd()) never actually invokes these stubs -- capability *detection*
        only ever calls shutil.which(), never runs anything.
        """
        fake_bin = self.home / 'fake-bin'
        fake_bin.mkdir(exist_ok=True)
        for meta in capabilities.CAPABILITIES.values():
            stub = fake_bin / meta['binary']
            stub.write_text('#!/bin/sh\nexit 0\n')
            stub.chmod(0o755)
        return dict(self.env, PATH=str(fake_bin) + os.pathsep + str(self.allowlist_dir))

    def current_run_text(self, env):
        state = Path(self.ai('path', env=env).strip())
        return (state / 'state/current-run.md').read_text()

    def test_zero_capabilities_prompt_is_strictly_shorter_than_all_five(self):
        self.ai('plan', 'small change', '--profile', 'fast', '--base', 'HEAD', env=self.env)
        none_text = self.current_run_text(self.env)

        all_env = self.env_with_fake_capabilities()
        self.ai('plan', 'small change', '--profile', 'fast', '--base', 'HEAD', env=all_env)
        all_text = self.current_run_text(all_env)

        self.assertLess(len(none_text), len(all_text))
        for meta in capabilities.CAPABILITIES.values():
            self.assertNotIn(meta['label'], none_text)
            self.assertIn(meta['label'], all_text)

    def test_both_extremes_fit_every_profiles_budget(self):
        for profile in ('fast', 'standard', 'strict'):
            cap = core.context_caps(profile)['context_chars']
            for env in (self.env, self.env_with_fake_capabilities()):
                self.ai('plan', 'small change', '--profile', profile, '--base', 'HEAD', env=env)
                text = self.current_run_text(env)
                self.assertLessEqual(len(text), cap)


if __name__ == '__main__':
    unittest.main()
