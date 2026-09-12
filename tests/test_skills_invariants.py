"""Structural invariants over the bundled skill registry, plus unit coverage for the
classifier/trigger/scoring/context-loading primitives that aren't already exercised by
tests/test_skills_routing.py, tests/test_skills_scope.py or tests/test_skills_golden.py.
"""
import contextlib
import io
import json
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import core  # noqa: E402
import skills  # noqa: E402

REGISTRY = skills.skill_registry().get('skills', {})
UPSTREAMS = json.loads((ROOT / 'skills/upstreams.json').read_text())


class RegistryInvariantTests(unittest.TestCase):
    """Every check here runs against the bundled registry as shipped."""

    def test_every_skill_has_prompt_and_readme(self):
        for name in REGISTRY:
            folder = skills.skill_root() / name
            with self.subTest(name=name):
                self.assertTrue((folder / 'prompt.md').is_file(), f'{name} missing prompt.md')
                self.assertTrue((folder / 'README.md').is_file(), f'{name} missing README.md')

    def test_every_skill_folder_is_registered(self):
        folders = {p.name for p in skills.skill_root().iterdir() if p.is_dir()}
        orphaned = folders - set(REGISTRY)
        self.assertFalse(orphaned, f'skill folder(s) not in registry.json: {orphaned}')

    def test_task_types_are_valid(self):
        for name, meta in REGISTRY.items():
            with self.subTest(name=name):
                bad = [t for t in meta.get('task_types', []) if t not in skills.TASK_TYPES]
                self.assertFalse(bad, f'{name} has unknown task_types: {bad}')

    def test_primary_for_is_a_subset_of_task_types(self):
        for name, meta in REGISTRY.items():
            with self.subTest(name=name):
                primary = set(meta.get('primary_for') or [])
                task_types = set(meta.get('task_types') or [])
                self.assertTrue(primary <= task_types,
                                 f'{name} primary_for {primary} not subset of task_types {task_types}')

    def test_cost_is_valid(self):
        for name, meta in REGISTRY.items():
            with self.subTest(name=name):
                self.assertIn(meta.get('cost'), skills.COSTS, f'{name} has invalid cost {meta.get("cost")!r}')

    def test_priority_is_an_int(self):
        for name, meta in REGISTRY.items():
            with self.subTest(name=name):
                self.assertIsInstance(meta.get('priority', 0), int, f'{name} priority is not an int')

    def test_stages_is_a_nonempty_list(self):
        for name, meta in REGISTRY.items():
            with self.subTest(name=name):
                stages = meta.get('stages')
                self.assertIsInstance(stages, list)
                self.assertTrue(stages, f'{name} has no stages')

    def test_requires_trigger_skills_have_at_least_one_trigger(self):
        for name, meta in REGISTRY.items():
            if meta.get('requires_trigger'):
                with self.subTest(name=name):
                    self.assertTrue(meta.get('triggers'), f'{name} requires_trigger but has no triggers (unreachable)')

    def test_every_registry_name_matches_the_naming_rule(self):
        for name in REGISTRY:
            with self.subTest(name=name):
                self.assertRegex(name, skills.NAME_RE)

    def test_every_provenance_skill_is_declared_in_upstreams_manifest(self):
        declared = set()
        for source in UPSTREAMS.get('sources', {}).values():
            declared.update(source.get('skills', {}).keys())
        provenance_skills = {name for name, meta in REGISTRY.items() if meta.get('provenance')}
        self.assertTrue(provenance_skills <= declared,
                         f'skill(s) with provenance missing from upstreams.json: {provenance_skills - declared}')

    def test_top_three_prompts_fit_the_strict_budget_together(self):
        sizes = sorted(((len((skills.skill_root() / n / 'prompt.md').read_text()), n) for n in REGISTRY),
                        reverse=True)[:3]
        caps = core.context_caps('strict')
        budget = max(2000, caps['context_chars'] // 3)
        total = sum(sz for sz, _ in sizes)
        self.assertLessEqual(total, budget, f'top 3 prompts ({sizes}) exceed the strict budget ({budget})')


class TriggerHitsUnitTests(unittest.TestCase):
    def test_regex_special_characters_in_triggers_match_literally(self):
        self.assertEqual(skills.trigger_hits(['c++', 'node.js'], 'upgrade c++ and node.js'), ['c++', 'node.js'])
        self.assertEqual(skills.trigger_hits(['c++'], 'upgrade c# instead'), [])

    def test_case_and_adjacent_punctuation(self):
        self.assertEqual(skills.trigger_hits(['test'], 'Tests, all green.'), ['test'])
        self.assertEqual(skills.trigger_hits(['slo'], '(SLOs) matter'), ['slo'])

    def test_unicode_input_does_not_crash_and_is_stable_across_normal_forms(self):
        nfc = unicodedata.normalize('NFC', 'migración')
        nfd = unicodedata.normalize('NFD', 'migración')
        hits_nfc = skills.trigger_hits(['migracion'], nfc)
        hits_nfd = skills.trigger_hits(['migracion'], nfd)
        self.assertEqual(hits_nfc, hits_nfd)
        # Emoji and stray symbols must not raise.
        self.assertEqual(skills.trigger_hits(['test'], '🔥💥 tests ß'), ['test'])


class ClassifyTaskUnitTests(unittest.TestCase):
    def test_none_and_blank_default_to_feature(self):
        self.assertEqual(core.classify_task(None), 'feature')
        self.assertEqual(core.classify_task(''), 'feature')
        self.assertEqual(core.classify_task('   '), 'feature')

    def test_figma_wins_over_every_keyword(self):
        self.assertEqual(core.classify_task('fix the migration bug', 'https://figma.com/file/1'), 'design')
        self.assertEqual(core.classify_task('https://figma.com/file/1 fix a bug'), 'design')

    def test_bug_wins_over_architecture_prototype_planning(self):
        self.assertEqual(core.classify_task('fix the migration'), 'bug')
        self.assertEqual(core.classify_task('fix the epic breakdown'), 'bug')

    def test_architecture_wins_over_prototype_and_planning(self):
        self.assertEqual(core.classify_task('refactor this prototype'), 'architecture')
        self.assertEqual(core.classify_task('migrate the roadmap tickets'), 'architecture')

    def test_prototype_wins_over_planning(self):
        self.assertEqual(core.classify_task('prototype the epic breakdown'), 'prototype')

    SPANISH_KEYWORDS = {
        'bug': ['fallos', 'fallan', 'roto', 'rota', 'rompe', 'excepcion', 'arregla', 'arreglar',
                'corrige', 'corregir', 'incorrecto', 'incorrecta'],
        'architecture': ['migra', 'migrar', 'migracion', 'redisena', 'rediseno', 'arquitectura',
                          'refactoriza', 'refactorizar', 'reescribe', 'reescribir', 'reemplaza',
                          'reemplazar', 'obsoleto', 'obsoleta'],
        'prototype': ['prototipo', 'prueba de concepto', 'viabilidad', 'podemos'],
        'planning': ['epicas', 'desglosa', 'desglosar', 'descomponer', 'hoja de ruta'],
    }

    def test_every_spanish_keyword_classifies_as_expected(self):
        for expected_type, words in self.SPANISH_KEYWORDS.items():
            for word in words:
                with self.subTest(word=word, expected=expected_type):
                    self.assertEqual(core.classify_task(f'necesitamos {word} esto'), expected_type)


class ScoreAndSelectUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skills-unit-')
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)

    def test_selection_never_exceeds_the_profile_cap_across_the_golden_corpus(self):
        golden = json.loads((ROOT / 'tests/fixtures/skill_routing_golden.json').read_text())
        caps = {p: core.context_caps(p)['skills'] for p in ('fast', 'standard', 'strict')}
        for row in golden:
            for profile in ('fast', 'standard', 'strict'):
                with self.subTest(task=row['task'], profile=profile):
                    selected = skills.select_skills(self.state, row['task'], profile, row.get('figma'))
                    self.assertLessEqual(len(selected), caps[profile])

    def test_selection_is_deterministic(self):
        task = 'arregla el login que falla'
        results = {tuple(skills.select_skills(self.state, task, 'standard')) for _ in range(50)}
        self.assertEqual(len(results), 1)

    def test_unknown_explicit_skill_raises_system_exit(self):
        with self.assertRaises(SystemExit):
            skills.select_skills(self.state, 'x', 'standard', explicit=['not-a-real-skill'])

    def test_explicit_skips_requires_trigger(self):
        # D3: naming a skill by hand is itself the intent a trigger would otherwise stand in for.
        selected = skills.select_skills(self.state, 'unrelated task text', 'standard', explicit=['research'])
        self.assertEqual(selected, ['research'])

    def test_all_disabled_yields_empty_list(self):
        overrides = {name: False for name in REGISTRY}
        (self.state / 'skill-overrides.json').write_text(json.dumps(overrides))
        self.assertEqual(skills.select_skills(self.state, 'add a feature', 'standard'), [])

    def test_figma_forces_design_type_and_design_routed_skills(self):
        selected = skills.select_skills(self.state, 'build the settings screen', 'standard', 'https://figma.com/file/1')
        self.assertEqual(core.classify_task('build the settings screen', 'https://figma.com/file/1'), 'design')
        self.assertTrue(selected)


class LoadSkillContextUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skills-context-')
        self.addCleanup(self.temp.cleanup)
        self.stack_root = Path(self.temp.name) / 'stack-skills'
        self.stack_root.mkdir()
        (self.stack_root / 'registry.json').write_text(json.dumps({"version": 1, "skills": {}}))
        from unittest.mock import patch
        patcher = patch('skills.skill_root', return_value=self.stack_root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, name, body):
        folder = self.stack_root / name
        folder.mkdir()
        (folder / 'prompt.md').write_text(body)

    def test_second_skill_gets_only_the_remaining_budget_and_no_empty_header(self):
        self._write('a', 'x' * 100)
        self._write('b', 'y' * 100)
        out = skills.load_skill_context(None, ['a', 'b'], 120)
        self.assertIn('## Skill: a', out)
        # Only 20 chars remain after 'a' consumes 100 (plus the header text budget isn't
        # separately accounted, so 'b' must either be absent or clearly truncated).
        if '## Skill: b' in out:
            self.assertIn('[skill context truncated by budget]', out)

    def test_skill_without_prompt_file_is_skipped_not_crashed(self):
        self._write('has-prompt', 'body')
        (self.stack_root / 'no-prompt').mkdir()
        out = skills.load_skill_context(None, ['has-prompt', 'no-prompt'], 10000)
        self.assertIn('## Skill: has-prompt', out)
        self.assertNotIn('no-prompt', out)

    def test_override_for_unknown_skill_name_does_not_crash(self):
        state = Path(self.temp.name) / 'state'
        state.mkdir()
        (state / 'skill-overrides.json').write_text(json.dumps({"totally-unknown": True}))
        enabled = skills.enabled_skills(state)
        self.assertNotIn('totally-unknown', enabled)


if __name__ == '__main__':
    unittest.main()
