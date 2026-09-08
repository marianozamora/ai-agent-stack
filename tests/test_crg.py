import sys, unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import crg  # noqa: E402


class ParseCrgRiskTests(unittest.TestCase):
    def test_labelled_forms_yield_uppercased_severity(self):
        # "risk: X" / "risk level = X" / "risk score - X" -> bare severity, uppercased
        self.assertEqual(crg.parse_crg_risk('risk: HIGH'), 'HIGH')
        self.assertEqual(crg.parse_crg_risk('Risk Level = medium'), 'MEDIUM')
        self.assertEqual(crg.parse_crg_risk('risk score - low'), 'LOW')

    def test_adjective_forms_match(self):
        # "<severity> risk" phrasing is the second accepted pattern
        self.assertEqual(crg.parse_crg_risk('HIGH risk detected'), 'HIGH')
        self.assertEqual(crg.parse_crg_risk('this is LOW risk'), 'LOW')

    def test_no_risk_mention_returns_none(self):
        self.assertIsNone(crg.parse_crg_risk('all clear, nothing notable to report'))

    def test_word_boundary_prevents_false_match(self):
        # "brisket" contains the letters "risk" but not as a word
        self.assertIsNone(crg.parse_crg_risk('brisket for dinner, HIGH hopes'))


class ElevateRiskTests(unittest.TestCase):
    def test_none_crg_risk_returns_base_untouched(self):
        # code path: `if not crg_risk: return base_risk` (same object)
        base = {'risk': 'LOW', 'reason': 'heuristic'}
        self.assertIs(crg.elevate_risk(base, None), base)

    def test_crg_elevates_low_to_high_and_returns_a_copy(self):
        base = {'risk': 'LOW', 'reason': 'heuristic'}
        out = crg.elevate_risk(base, 'HIGH')
        self.assertEqual(out['risk'], 'HIGH')
        self.assertTrue(out['crg_elevated'])
        self.assertIn('CRG', out['reason'])
        # input dict is not mutated - a fresh copy is returned
        self.assertEqual(base, {'risk': 'LOW', 'reason': 'heuristic'})
        self.assertIsNot(out, base)

    def test_lower_crg_risk_never_downgrades_base(self):
        out = crg.elevate_risk({'risk': 'HIGH', 'reason': 'auth path'}, 'LOW')
        self.assertEqual(out['risk'], 'HIGH')
        self.assertFalse(out['crg_elevated'])

    def test_equal_rank_is_not_an_elevation(self):
        out = crg.elevate_risk({'risk': 'MEDIUM', 'reason': 'size'}, 'MEDIUM')
        self.assertEqual(out['risk'], 'MEDIUM')
        self.assertFalse(out['crg_elevated'])


class CrgCmdTests(unittest.TestCase):
    def test_missing_binary_returns_none(self):
        with patch('crg.shutil.which', return_value=None):
            self.assertIsNone(crg.crg_cmd())

    def test_present_binary_returns_argv_list(self):
        with patch('crg.shutil.which', return_value='/usr/bin/code-review-graph'):
            self.assertEqual(crg.crg_cmd(), ['/usr/bin/code-review-graph'])


class CrgEnvTests(unittest.TestCase):
    def test_env_points_at_external_state_dirs_and_keeps_ambient(self):
        state = Path('/tmp/some-repo-state')
        env = crg.crg_env(state)
        self.assertEqual(env['CRG_DATA_DIR'], str(state / 'code-review-graph'))
        self.assertEqual(env['GRAPHIFY_OUT'], str(state / 'graphify'))
        self.assertEqual(env['AI_REPO_STATE'], str(state))
        # ambient environment is preserved (copied, not replaced)
        self.assertIn('PATH', env)


class CrgExecTests(unittest.TestCase):
    def test_missing_binary_soft_fails_with_127(self):
        with patch('crg.shutil.which', return_value=None):
            self.assertEqual(
                crg.crg_exec(Path('.'), Path('/tmp/s'), ['status'], check=False), (127, ''))

    def test_missing_binary_raises_when_checked(self):
        with patch('crg.shutil.which', return_value=None):
            with self.assertRaises(RuntimeError):
                crg.crg_exec(Path('.'), Path('/tmp/s'), ['status'], check=True)


class CrgImpactTests(unittest.TestCase):
    def test_impact_is_empty_without_the_binary(self):
        # crg_impact short-circuits before touching any state when crg_cmd() is None
        with patch('crg.shutil.which', return_value=None):
            self.assertEqual(crg.crg_impact(Path('.'), Path('/tmp/s'), 'HEAD'), '')


if __name__ == '__main__':
    unittest.main()
