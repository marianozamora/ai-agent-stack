import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import skills  # noqa: E402


class CuratedSkillRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='curated-skills-test-')
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)

    def test_specialized_skills_do_not_displace_ordinary_feature_routing(self):
        self.assertEqual(skills.select_skills(self.state, 'add a user preference field', 'standard'),
                         ['tdd', 'writing-for-agents'])

    def test_source_driven_skill_requires_and_wins_on_specific_triggers(self):
        selected = skills.select_skills(
            self.state, 'upgrade the SDK using official documentation for this API version', 'fast')
        self.assertEqual(selected, ['source-driven-development'])

    def test_observability_skill_routes_when_operational_signals_are_requested(self):
        selected = skills.select_skills(self.state, 'add OpenTelemetry tracing and SLO alerting', 'standard')
        self.assertEqual(selected[0], 'observability-and-instrumentation')

    def test_migration_skill_composes_with_wayfinder(self):
        selected = skills.select_skills(self.state, 'deprecate the legacy API and migrate callers', 'standard')
        self.assertEqual(selected, ['deprecation-and-migration', 'wayfinder'])


class UpstreamStatusTests(unittest.TestCase):
    PINNED = 'a' * 40
    CURRENT = 'b' * 40

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='upstream-status-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        folder = self.root / 'local-skill'
        folder.mkdir()
        (folder / 'prompt.md').write_text('adapted\n')
        (folder / 'skill.json').write_text(json.dumps({'provenance': {
            'source': 'example', 'path': 'skills/upstream/SKILL.md', 'adaptation': 'curated'}}))
        patcher = patch('skills.skill_root', return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def manifest(self):
        return {'version': 1, 'sources': {'example': {
            'repository': 'https://example.test/skills.git', 'ref': 'main',
            'commit': self.PINNED, 'license': 'MIT',
            'skills': {'local-skill': {'path': 'skills/upstream/SKILL.md', 'adaptation': 'curated',
                                      'prompt_sha256': skills.shasum('adapted\n')}},
        }}}

    def registry(self):
        return {'version': 1, 'skills': {'local-skill': {'provenance': {
            'source': 'example', 'path': 'skills/upstream/SKILL.md', 'adaptation': 'curated',
        }}}}

    def test_local_status_is_network_free_and_reports_the_pin(self):
        with patch('skills.upstream_registry', return_value=self.manifest()), \
                patch('skills.skill_registry', return_value=self.registry()), patch('skills.run') as run:
            rows = skills.upstream_status()
        run.assert_not_called()
        self.assertEqual(rows[0]['status'], 'PINNED')
        self.assertEqual(rows[0]['pinned_commit'], self.PINNED)

    def test_remote_check_reports_an_available_update(self):
        result = f'{self.CURRENT}\trefs/heads/main'
        with patch('skills.upstream_registry', return_value=self.manifest()), \
                patch('skills.skill_registry', return_value=self.registry()), \
                patch('skills.run', return_value=result) as run:
            rows = skills.upstream_status(check=True)
        run.assert_called_once_with([
            'git', 'ls-remote', 'https://example.test/skills.git', 'refs/heads/main'])
        self.assertEqual(rows[0]['status'], 'UPDATE_AVAILABLE')
        self.assertEqual(rows[0]['current_commit'], self.CURRENT)

    def test_provenance_mismatch_fails_closed(self):
        registry = self.registry()
        registry['skills']['local-skill']['provenance']['path'] = 'wrong/path.md'
        with patch('skills.upstream_registry', return_value=self.manifest()), \
                patch('skills.skill_registry', return_value=registry):
            with self.assertRaisesRegex(RuntimeError, 'provenance mismatch'):
                skills.upstream_status()

    def test_changed_curated_prompt_fails_closed(self):
        (self.root / 'local-skill' / 'prompt.md').write_text('changed\n')
        with patch('skills.upstream_registry', return_value=self.manifest()), \
                patch('skills.skill_registry', return_value=self.registry()):
            with self.assertRaisesRegex(RuntimeError, 'changed without updating provenance'):
                skills.upstream_status()

    def test_descriptor_provenance_mismatch_fails_closed(self):
        (self.root / 'local-skill' / 'skill.json').write_text('{}')
        with patch('skills.upstream_registry', return_value=self.manifest()), \
                patch('skills.skill_registry', return_value=self.registry()):
            with self.assertRaisesRegex(RuntimeError, 'descriptor provenance mismatch'):
                skills.upstream_status()

    def test_corrupt_manifest_fails_closed(self):
        (self.root / 'upstreams.json').write_text('{broken')
        with self.assertRaisesRegex(RuntimeError, 'Invalid upstream skill manifest'):
            skills.upstream_registry()

    def test_json_command_exposes_machine_readable_status(self):
        row = {'source': 'example', 'repository': 'https://example.test/skills.git', 'ref': 'main',
               'license': 'MIT', 'pinned_commit': self.PINNED, 'current_commit': None,
               'status': 'PINNED', 'skills': ['local-skill']}
        out = io.StringIO(); args = argparse.Namespace(skill_cmd='upstream', check=False, json=True)
        with patch('skills.upstream_status', return_value=[row]), contextlib.redirect_stdout(out):
            skills.cmd_skill(args)
        self.assertEqual(json.loads(out.getvalue())['sources'][0]['status'], 'PINNED')


if __name__ == '__main__':
    unittest.main()
