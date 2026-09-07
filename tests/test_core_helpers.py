import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# core.py (and every other ai_stack/ module) uses bare same-directory imports
# (`from workflow import ...`), matching the script-execution model every real
# invocation path relies on — a direct load needs ai_stack/ on sys.path first,
# the same thing running the file as a script does implicitly.
sys.path.insert(0, str(ROOT / 'ai_stack'))
import core  # noqa: E402


class CoreHelpersTests(unittest.TestCase):
    def test_json_file_health_missing_ok_corrupt(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            missing = directory / 'missing.json'
            self.assertEqual(core.json_file_health(missing), 'missing')

            ok = directory / 'ok.json'
            ok.write_text('{"a": 1}')
            self.assertEqual(core.json_file_health(ok), 'ok')

            corrupt = directory / 'corrupt.json'
            corrupt.write_text('{"a": 1,')
            self.assertEqual(core.json_file_health(corrupt), 'corrupt')

    def test_load_json_stays_lenient_on_corruption(self):
        # load_json()'s ~40 callers all assume it never raises; json_file_health()
        # is the separate, explicit path for surfacing corruption (in `ai doctor`),
        # so this contract must not change.
        with tempfile.TemporaryDirectory() as d:
            corrupt = Path(d) / 'corrupt.json'
            corrupt.write_text('not json at all')
            self.assertEqual(core.load_json(corrupt, {'default': True}), {'default': True})

    def test_classify_risk_by_path_and_size(self):
        low = core.classify({'files': ['src/ui/copy.py'], 'file_count': 1, 'changed_lines': 5}, 'standard')
        self.assertEqual(low['risk'], 'LOW')

        high = core.classify({'files': ['src/auth/token.py'], 'file_count': 1, 'changed_lines': 5}, 'standard')
        self.assertEqual(high['risk'], 'HIGH')
        self.assertTrue(high['security'])

        big = core.classify({'files': ['a.py'] * 7, 'file_count': 7, 'changed_lines': 5}, 'standard')
        self.assertEqual(big['risk'], 'MEDIUM')

        strict_floor = core.classify({'files': ['a.py'], 'file_count': 1, 'changed_lines': 1}, 'strict')
        self.assertEqual(strict_floor['risk'], 'MEDIUM')

    def test_context_caps_scale_with_profile(self):
        fast, standard, strict = (core.context_caps(p) for p in ('fast', 'standard', 'strict'))
        self.assertLess(fast['skills'], standard['skills'])
        self.assertLess(standard['skills'], strict['skills'])
        self.assertLess(fast['usage_tokens'], standard['usage_tokens'])
        self.assertLess(standard['usage_tokens'], strict['usage_tokens'])


if __name__ == '__main__':
    unittest.main()
