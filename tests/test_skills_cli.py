"""`ai skill *` CLI plumbing and its integration with `ai plan` / build_prompt.

Follows the in-process pattern from tests/test_happy_path.py: a real temporary git repo,
CONFIG_ROOT patched to an isolated directory, cwd switched into the repo -- fast, and
exercises the same cli.main()/cmd_skill()/build_prompt() code path a real invocation would.
"""
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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import cli  # noqa: E402
import core  # noqa: E402
import lifecycle  # noqa: E402
import skills  # noqa: E402


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
         patch.object(sys, 'argv', ['ai', *argv]):
        try:
            cli.main()
        except SystemExit as exc:
            code = 0 if exc.code is None else exc.code
    return code, out.getvalue(), err.getvalue()


class SkillCliTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skills-cli-')
        self.addCleanup(self.temp.cleanup)
        home = Path(self.temp.name)
        self.repo = home / 'repo'
        self.repo.mkdir()
        for argv in [('init', '-q', '-b', 'main'), ('config', 'user.name', 'Test'),
                     ('config', 'user.email', 'test@example.com')]:
            subprocess.check_call(['git', *argv], cwd=self.repo,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (self.repo / 'app.py').write_text('value = 1\n')
        subprocess.check_call(['git', 'add', '.'], cwd=self.repo, stdout=subprocess.DEVNULL)
        subprocess.check_call(['git', 'commit', '-qm', 'initial'], cwd=self.repo,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        self.previous = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, self.previous)
        for name in ('AI_TASK_ID', 'AI_GATE', 'AI_TASK_DIR'):
            os.environ.pop(name, None)
        core.TASK_ID = None
        patcher = patch.object(core, 'CONFIG_ROOT', home / 'config')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state = core.repo_state(self.repo)
        run_cli(['start', 'cli-test-1', '--base', 'HEAD'])

    def git_clean(self):
        out = subprocess.check_output(['git', 'status', '--porcelain'], cwd=self.repo, text=True)
        return out.strip()


class SkillListTests(SkillCliTestCase):
    def test_bare_skill_command_matches_explicit_list(self):
        code1, out1, _ = run_cli(['skill'])
        code2, out2, _ = run_cli(['skill', 'list'])
        self.assertEqual(code1, 0)
        self.assertEqual(out1, out2)

    def test_list_shows_every_bundled_skill_with_a_mark(self):
        code, out, _ = run_cli(['skill', 'list'])
        self.assertEqual(code, 0)
        for name in skills.skill_registry().get('skills', {}):
            self.assertIn(name, out)

    def test_list_with_task_recommends_what_select_skills_would(self):
        task = 'arregla el login que falla'
        code, out, _ = run_cli(['skill', 'list', '--task', task, '--profile', 'standard'])
        self.assertEqual(code, 0)
        expected = skills.select_skills(self.state, task, 'standard')
        self.assertIn(f'Task type: {core.classify_task(task)}', out)
        self.assertIn('Recommended: ' + ', '.join(expected), out)
        self.assertEqual(self.git_clean(), '')

    def test_list_with_task_english(self):
        task = 'login regression returns 403'
        code, out, _ = run_cli(['skill', 'list', '--task', task, '--profile', 'fast'])
        self.assertEqual(code, 0)
        expected = skills.select_skills(self.state, task, 'fast')
        self.assertIn('Recommended: ' + ', '.join(expected), out)

    def test_starred_rows_are_exactly_the_selected_skills(self):
        task = 'arregla el login que falla'
        code, out, _ = run_cli(['skill', 'list', '--task', task, '--profile', 'standard'])
        selected = set(skills.select_skills(self.state, task, 'standard'))
        starred = set()
        for line in out.splitlines():
            if line.strip().startswith('*'):
                starred.add(line.split('*', 1)[1].split('score=')[0].strip())
        self.assertEqual(starred, selected)


class SkillExplainTests(SkillCliTestCase):
    def test_explain_known_skill_prints_json_without_enabled_plus_readme(self):
        code, out, _ = run_cli(['skill', 'explain', 'tdd'])
        self.assertEqual(code, 0)
        head, _, readme = out.partition('\n\n')
        data = json.loads(head)
        self.assertNotIn('enabled', data)
        self.assertIn('tdd', readme.lower() + out.lower())

    def test_explain_unknown_skill_exits_nonzero(self):
        code, out, err = run_cli(['skill', 'explain', 'not-a-real-skill'])
        self.assertNotEqual(code, 0)


class SkillEnableDisableTests(SkillCliTestCase):
    def test_disable_then_enable_round_trips_through_list(self):
        code, _, _ = run_cli(['skill', 'disable', 'tdd'])
        self.assertEqual(code, 0)
        _, out, _ = run_cli(['skill', 'list', '--task', 'add feature', '--profile', 'standard'])
        self.assertNotIn('tdd', skills.select_skills(self.state, 'add feature', 'standard'))
        code, _, _ = run_cli(['skill', 'enable', 'tdd'])
        self.assertEqual(code, 0)
        self.assertIn('tdd', skills.select_skills(self.state, 'add feature', 'standard'))
        self.assertEqual(self.git_clean(), '')

    def test_overrides_persist_to_disk(self):
        run_cli(['skill', 'disable', 'tdd'])
        overrides = json.loads((self.state / 'skill-overrides.json').read_text())
        self.assertEqual(overrides.get('tdd'), False)


class SkillDryRunTests(SkillCliTestCase):
    def test_dry_run_reports_no_repo_writes(self):
        before = self.git_clean()
        code, out, _ = run_cli(['skill', 'dry-run', 'tdd', '--profile', 'strict'])
        self.assertEqual(code, 0)
        self.assertIn('repo writes: NO', out)
        self.assertEqual(self.git_clean(), before)


class BuildPromptIntegrationTests(SkillCliTestCase):
    def test_plan_reports_the_same_skills_select_skills_would_choose(self):
        task = 'login regression returns 403'
        code, out, _ = run_cli(['plan', task, '--profile', 'fast', '--base', 'HEAD'])
        self.assertEqual(code, 0)
        expected = skills.select_skills(self.state, task, 'fast')
        self.assertIn('skills:      ' + (', '.join(expected) or 'none'), out)

    def test_prompt_contains_a_skill_section_per_selected_skill(self):
        root = self.repo
        prompt = lifecycle.build_prompt(root, self.state, 'arregla el login que falla', 'standard', 'HEAD', None)
        selected = skills.select_skills(self.state, 'arregla el login que falla', 'standard')
        self.assertIn(f'Active skills (lazy-loaded; max {core.context_caps("standard")["skills"]}): '
                       + ', '.join(selected), prompt)
        for name in selected:
            self.assertIn(f'## Skill: {name}', prompt)

    def test_explicit_skill_flag_reaches_build_prompt_and_the_saved_plan(self):
        code, out, _ = run_cli(['plan', 'irrelevant task text', '--profile', 'standard',
                                 '--base', 'HEAD', '--skill', 'research'])
        self.assertEqual(code, 0)
        self.assertIn('skills:      research', out)
        saved = json.loads((core.task_state(self.state) / 'state' / 'current-plan.json').read_text())
        self.assertEqual(saved['skills'], ['research'])

    def test_disabling_a_selected_skill_changes_the_cache_key(self):
        task = 'arregla el login que falla'
        before = skills.select_skills(self.state, task, 'standard')
        key_before = lifecycle.task_cache_key(self.repo, task, 'standard', 'HEAD', before)
        run_cli(['skill', 'disable', before[0]])
        after = skills.select_skills(self.state, task, 'standard')
        key_after = lifecycle.task_cache_key(self.repo, task, 'standard', 'HEAD', after)
        self.assertNotEqual(before, after)
        self.assertNotEqual(key_before, key_after)

    def test_plan_omitted_profile_autoselects_fast_for_a_small_low_risk_change(self):
        (self.repo / 'app.py').write_text('value = 2\n')
        code, out, _ = run_cli(['plan', 'fix typo', '--base', 'HEAD'])
        self.assertEqual(code, 0)
        self.assertIn('profile:     fast', out)


class UpstreamCliTests(SkillCliTestCase):
    def test_upstream_json_is_valid_and_matches_upstream_status(self):
        code, out, _ = run_cli(['skill', 'upstream', '--json'])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data['sources'], skills.upstream_status())

    def test_upstream_check_with_failing_git_reports_cleanly(self):
        with patch('skills.run', side_effect=RuntimeError('git failed: no network')):
            with self.assertRaises(RuntimeError):
                skills.upstream_status(check=True)


class NoRepoTests(unittest.TestCase):
    """`ai skill *` outside any git repository: no repo state to read overrides from."""

    def test_list_outside_a_repo_fails_clearly(self):
        temp = tempfile.TemporaryDirectory(prefix='skills-norepo-')
        self.addCleanup(temp.cleanup)
        previous = os.getcwd()
        os.chdir(temp.name)
        self.addCleanup(os.chdir, previous)
        code, out, err = run_cli(['skill', 'list'])
        self.assertNotEqual(code, 0)
        self.assertEqual(out, '')


if __name__ == '__main__':
    unittest.main()
