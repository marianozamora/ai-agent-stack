"""Skill routing against the bundled registry: whole-word triggers, Spanish tasks,
data-driven bonuses, and the registry being the single source of routing metadata."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import core  # noqa: E402
import skills  # noqa: E402


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skill-routing-test-')
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)

    def select(self, task, profile='standard'):
        return skills.select_skills(self.state, task, profile)

    def test_triggers_match_whole_words_with_inflections(self):
        self.assertEqual(skills.trigger_hits(['test'], 'bump to the latest version'), [])
        self.assertEqual(skills.trigger_hits(['test'], 'add tests for checkout'), ['test'])
        self.assertEqual(skills.trigger_hits(['slo'], 'fix the slow query'), [])
        self.assertEqual(skills.trigger_hits(['slo'], 'define SLOs for checkout'), ['slo'])
        self.assertEqual(skills.trigger_hits(['deprecat'], 'deprecated endpoints'), ['deprecat'])

    def test_accents_are_optional_on_either_side(self):
        self.assertEqual(skills.trigger_hits(['migración'], 'plan de migracion'), ['migración'])
        self.assertEqual(skills.trigger_hits(['migracion'], 'plan de migración'), ['migracion'])

    def test_a_substring_no_longer_steals_fasts_only_slot(self):
        self.assertEqual(self.select('fix the slow query on the orders page', 'fast'), ['diagnosing-bugs'])

    def test_writing_for_agents_needs_its_own_triggers(self):
        self.assertNotIn('writing-for-agents', self.select('add a discount code feature'))
        self.assertIn('writing-for-agents', self.select('tighten the agent prompt'))

    def test_spanish_tasks_route_like_english_ones(self):
        self.assertEqual(self.select('migra el módulo de pagos fuera del SDK obsoleto'),
                         ['deprecation-and-migration', 'wayfinder'])
        self.assertEqual(self.select('agrega trazas y métricas al checkout')[0], 'observability-and-instrumentation')
        self.assertEqual(self.select('arregla el fallo del login', 'fast'), ['diagnosing-bugs'])

    def test_classify_task_understands_spanish_and_fix(self):
        for task, kind in (('fix the slow query', 'bug'), ('arregla el login', 'bug'),
                           ('migracion del modulo de pagos', 'architecture'),
                           ('prueba de concepto de pagos', 'prototype'), ('desglosa la épica', 'planning')):
            with self.subTest(task=task):
                self.assertEqual(core.classify_task(task), kind)

    def test_type_bonuses_come_from_the_registry_not_the_skill_name(self):
        rows = {row['name']: row for row in skills.score_skills(self.state, 'build a prototype for search')}
        self.assertIn('primary for prototype', rows['prototype']['reasons'])

    def test_scores_explain_themselves(self):
        top = skills.score_skills(self.state, 'migra el módulo de pagos fuera del SDK obsoleto')[0]
        self.assertEqual(top['name'], 'deprecation-and-migration')
        self.assertIn('task type architecture', top['reasons'])
        self.assertIn('triggers: obsoleto, migra', top['reasons'])

    def test_an_explicitly_requested_disabled_skill_is_reported(self):
        (self.state / 'skill-overrides.json').write_text(json.dumps({'tdd': False}))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(skills.select_skills(self.state, 'x', 'standard', explicit=['tdd']), [])
        self.assertIn('Skipping disabled skill(s): tdd', err.getvalue())


class RegistryIsTheSourceOfTruthTests(unittest.TestCase):
    def test_every_skill_json_matches_its_registry_entry(self):
        registry = json.loads((ROOT / 'skills/registry.json').read_text())['skills']
        for name, meta in registry.items():
            with self.subTest(skill=name):
                descriptor = json.loads((ROOT / 'skills' / name / 'skill.json').read_text())
                self.assertEqual({k: v for k, v in descriptor.items() if k not in ('name', 'version')},
                                 {k: v for k, v in meta.items() if k != 'enabled'})


if __name__ == '__main__':
    unittest.main()
