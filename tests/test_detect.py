"""Tooling detection and the `checks`/`regression` validator proposal."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import detect  # noqa: E402
import workflow  # noqa: E402


def everything_installed(name):
    return '/usr/bin/' + name


def nothing_installed(name):
    return None


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='detect-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, text=''):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def propose(self, configured=None, which=everything_installed):
        with patch.object(detect.shutil, 'which', which):
            return detect.proposal(self.root, configured or {})

    def row(self, report, gate):
        return next(r for r in report['rows'] if r['gate'] == gate)

    # --- per-ecosystem detection ------------------------------------------

    def test_python_ruff_mypy_and_pytest(self):
        self.write('pyproject.toml', '[tool.ruff]\n[tool.mypy]\n[tool.pytest.ini_options]\n')
        report = self.propose()
        checks = self.row(report, 'checks')
        self.assertEqual(checks['detected_from'], ['ruff', 'mypy'])
        self.assertEqual(checks['command'], ['sh', '-c', 'ruff check . && mypy .'])
        self.assertEqual(self.row(report, 'regression')['command'], ['pytest', '-q'])

    def test_python_tests_detected_from_a_tests_directory_alone(self):
        self.write('tests/test_thing.py', 'def test_x(): pass\n')
        self.assertEqual(self.row(self.propose(), 'regression')['command'], ['pytest', '-q'])

    def test_node_uses_the_lockfile_package_manager(self):
        self.write('package.json', '{"scripts": {"lint": "eslint .", "test": "vitest run"}}')
        self.write('pnpm-lock.yaml')
        self.write('tsconfig.json')
        report = self.propose()
        self.assertEqual(self.row(report, 'checks')['command'],
                         ['sh', '-c', 'pnpm run lint && pnpm exec tsc --noEmit'])
        self.assertEqual(self.row(report, 'regression')['command'], ['pnpm', 'test'])

    def test_node_npm_lockfile_uses_npx_for_local_binaries(self):
        self.write('package.json', '{"scripts": {}}')
        self.write('package-lock.json')
        self.write('tsconfig.json')
        self.assertEqual(self.row(self.propose(), 'checks')['command'],
                         ['npx', 'tsc', '--noEmit'])

    def test_a_typecheck_script_wins_over_a_raw_tsc_invocation(self):
        self.write('package.json', '{"scripts": {"typecheck": "tsc -p ."}}')
        self.write('tsconfig.json')
        checks = self.row(self.propose(), 'checks')
        self.assertEqual(checks['detected_from'], ['typecheck-script'])

    def test_rust(self):
        self.write('Cargo.toml', '[package]\n')
        report = self.propose()
        self.assertIn('clippy', self.row(report, 'checks')['command'])
        self.assertEqual(self.row(report, 'regression')['command'], ['cargo', 'test'])

    def test_go_prefers_golangci_lint_when_installed(self):
        self.write('go.mod', 'module x\n')
        self.write('.golangci.yml')
        checks = self.row(self.propose(), 'checks')
        self.assertEqual(checks['detected_from'], ['golangci-lint'])

    def test_go_falls_back_to_vet_when_golangci_lint_is_absent(self):
        # Superseding a tool that is not installed would leave the gate with no
        # check at all, which is worse than the weaker check.
        self.write('go.mod', 'module x\n')
        self.write('.golangci.yml')
        report = self.propose(which=lambda name: None if name == 'golangci-lint' else '/usr/bin/' + name)
        checks = self.row(report, 'checks')
        self.assertEqual(checks['detected_from'], ['go-vet'])
        self.assertIn('golangci-lint', checks['unavailable'])

    def test_repository_with_no_tooling_proposes_nothing(self):
        self.write('README.md', 'hello\n')
        report = self.propose()
        self.assertEqual(self.row(report, 'checks')['action'], 'none')
        self.assertEqual(self.row(report, 'regression')['action'], 'none')

    # --- proposal semantics -----------------------------------------------

    def test_a_configured_gate_is_kept_never_overwritten(self):
        self.write('pyproject.toml', '[tool.ruff]\n')
        existing = {'checks': {'command': ['make', 'check'], 'adapter': 'exit-code',
                               'timeout': 600, 'evidence': 'make check passed'}}
        checks = self.row(self.propose(configured=existing), 'checks')
        self.assertEqual(checks['action'], 'keep')
        self.assertEqual(checks['command'], ['make', 'check'])

    def test_uninstalled_tools_are_reported_but_never_proposed(self):
        self.write('pyproject.toml', '[tool.ruff]\n[tool.mypy]\n')
        report = self.propose(which=nothing_installed)
        checks = self.row(report, 'checks')
        self.assertEqual(checks['action'], 'none')
        self.assertEqual(checks['unavailable'], ['ruff', 'mypy'])

    def test_every_proposed_validator_passes_config_validation(self):
        # A proposal that validate_config() would reject is unusable, so this is
        # the contract that matters most for --apply.
        self.write('pyproject.toml', '[tool.ruff]\n[tool.mypy]\n[tool.pytest.ini_options]\n')
        report = self.propose()
        config = {'version': 1, 'validators': {r['gate']: r['validator']
                                               for r in report['rows'] if r['action'] == 'add'}}
        workflow.validate_config(config)
        for item in config['validators'].values():
            self.assertEqual(item['adapter'], 'exit-code')
            self.assertTrue(item['evidence'].strip())

    def test_render_is_shell_accurate_for_combined_commands(self):
        self.write('pyproject.toml', '[tool.ruff]\n[tool.mypy]\n')
        lines = '\n'.join(detect.render(self.propose()))
        self.assertIn("sh -c 'ruff check . && mypy .'", lines)

    def test_malformed_package_json_does_not_crash_detection(self):
        self.write('package.json', '{not json')
        self.assertEqual(detect.package_json(self.root), {})
        self.row(self.propose(), 'checks')  # must not raise


if __name__ == '__main__':
    unittest.main()
