"""Direct unit coverage for ai_stack/capabilities.py.

The orchestration prompt used to route through Code Review Graph, Graphify,
CodeGraph, Context7 and RTK unconditionally -- with no check that any of them
were actually installed, and with RTK and CodeGraph never once verified by a
single shutil.which() anywhere in this codebase. The controlling invariant this
file exists to pin is silence: a capability that is not detected (absent binary,
or explicitly disabled) must appear NOWHERE in anything build_prompt renders --
not a status line, not a context-order entry, not a budget cap. See
ai_stack/capabilities.py's module docstring for the full rationale.
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
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
# ai_stack/ modules use bare same-directory imports; putting ai_stack/ on
# sys.path makes `import capabilities` work, transitively pulling core/skills.
sys.path.insert(0, str(ROOT / 'ai_stack'))
import capabilities  # noqa: E402
import core  # noqa: E402


FAKE_CAPS = {'docs_queries': 3, 'graph_queries': 4}


class DetectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cap-detect-')
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)

    def which(self, found):
        """A shutil.which stand-in that reports only the given binaries as present."""
        return lambda name: f'/usr/bin/{name}' if name in found else None

    def test_nothing_found_detects_nothing(self):
        with mock.patch.object(capabilities.shutil, 'which', self.which(())):
            self.assertEqual(capabilities.detect(self.state), [])

    def test_everything_found_preserves_registry_routing_order(self):
        all_binaries = [meta['binary'] for meta in capabilities.CAPABILITIES.values()]
        with mock.patch.object(capabilities.shutil, 'which', self.which(all_binaries)):
            self.assertEqual(capabilities.detect(self.state), list(capabilities.CAPABILITIES))

    def test_a_subset_is_detected_in_routing_order_regardless_of_which_order_theyre_found(self):
        # rtk and context7 found "out of order" relative to the registry; detect()
        # must still return them in registry (routing-priority) order: context7 before rtk.
        with mock.patch.object(capabilities.shutil, 'which', self.which(('rtk', 'ctx7'))):
            self.assertEqual(capabilities.detect(self.state), ['context7', 'rtk'])

    def test_disabled_capability_is_excluded_even_when_its_binary_is_present(self):
        (self.state / 'capability-overrides.json').write_text(json.dumps({'crg': False}))
        with mock.patch.object(capabilities.shutil, 'which', self.which(('code-review-graph', 'ctx7'))):
            detected = capabilities.detect(self.state)
        self.assertNotIn('crg', detected)
        self.assertIn('context7', detected)

    def test_no_overrides_file_means_everything_is_enabled_by_default(self):
        with mock.patch.object(capabilities.shutil, 'which', self.which(('rtk',))):
            self.assertEqual(capabilities.detect(self.state), ['rtk'])


class RenderContextOrderTests(unittest.TestCase):
    def test_nothing_detected_is_exactly_the_two_fixed_endpoints(self):
        out = capabilities.render_context_order([])
        self.assertEqual(out, '1. PR/design contract\n'
                              '2. raw source reads only when needed to prove/implement something')

    def test_numbering_is_contiguous_for_a_single_capability(self):
        out = capabilities.render_context_order(['graphify'])
        lines = out.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith('1. PR/design contract'))
        self.assertTrue(lines[1].startswith('2. Graphify'))
        self.assertTrue(lines[2].startswith('3. raw source reads'))

    def test_numbering_is_contiguous_for_every_capability_present(self):
        out = capabilities.render_context_order(list(capabilities.CAPABILITIES))
        lines = out.splitlines()
        self.assertEqual(len(lines), len(capabilities.CAPABILITIES) + 2)
        for i, line in enumerate(lines, 1):
            self.assertTrue(line.startswith(f'{i}. '), line)

    def test_order_follows_registry_priority_not_the_caller_supplied_order(self):
        # Pass the detected set in a scrambled order -- the rendered list must still
        # follow CAPABILITIES' fixed routing priority (crg, graphify, codegraph,
        # context7, rtk), never whatever order the caller happened to pass.
        out = capabilities.render_context_order(['rtk', 'crg', 'context7'])
        lines = out.splitlines()
        self.assertIn('Code Review Graph', lines[1])
        self.assertIn('Context7', lines[2])
        self.assertIn('RTK', lines[3])

    def test_exact_verbatim_text_for_each_capability_matches_the_pre_registry_prompt(self):
        # Provably-behaviour-preserving check: the all-installed routing list must
        # read identically to the hardcoded block this registry replaced.
        out = capabilities.render_context_order(list(capabilities.CAPABILITIES))
        self.assertEqual(out, (
            '1. PR/design contract\n'
            '2. Code Review Graph for diff impact, blast radius, affected flows, tests and minimal review context\n'
            '3. Graphify for macro architecture/routes/communities when CRG cannot answer the architecture question\n'
            '4. CodeGraph for exact symbol navigation when materially better than CRG\n'
            '5. Context7 ONLY for external library/framework/API documentation; prefer exact installed version and cached library IDs\n'
            '6. RTK for git/tests/lint/search output\n'
            '7. raw source reads only when needed to prove/implement something'
        ))


class RenderBudgetLinesTests(unittest.TestCase):
    def test_nothing_detected_is_the_empty_string(self):
        self.assertEqual(capabilities.render_budget_lines([], FAKE_CAPS), '')

    def test_context7_absent_omits_its_docs_queries_line(self):
        out = capabilities.render_budget_lines(['graphify'], FAKE_CAPS)
        self.assertNotIn('docs queries', out)
        self.assertIn('Graphify structural queries <= 4', out)

    def test_graphify_absent_omits_its_structural_queries_line(self):
        out = capabilities.render_budget_lines(['context7'], FAKE_CAPS)
        self.assertNotIn('structural queries', out)
        self.assertIn('Context7 docs queries <= 3', out)

    def test_a_capability_with_no_cap_contributes_no_line(self):
        # crg/codegraph/rtk have cap=None; detecting them must not add budget lines.
        out = capabilities.render_budget_lines(['crg', 'codegraph', 'rtk'], FAKE_CAPS)
        self.assertEqual(out, '')

    def test_context7_then_graphify_order_matches_the_pre_registry_prompt(self):
        # The original hardcoded block listed Context7 before Graphify -- the reverse
        # of their routing priority in Context order. Preserved verbatim, not "fixed".
        out = capabilities.render_budget_lines(['graphify', 'context7'], FAKE_CAPS)
        self.assertEqual(out, '- Context7 docs queries <= 3\n- Graphify structural queries <= 4\n')

    def test_each_line_is_newline_terminated_for_direct_splicing(self):
        out = capabilities.render_budget_lines(['context7'], FAKE_CAPS)
        self.assertTrue(out.endswith('\n'))
        self.assertEqual(out.count('\n'), 1)


class CapabilityBlockSilenceTests(unittest.TestCase):
    """The invariant this whole module exists to hold: an absent capability's
    label appears nowhere in the rendered block, for every capability in turn."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cap-block-')
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)

    def test_zero_capabilities_mentions_no_label_and_carries_no_skill_text(self):
        block = capabilities.capability_block(self.state, [])
        for meta in capabilities.CAPABILITIES.values():
            self.assertNotIn(meta['label'], block)
        self.assertNotIn('External docs policy', block)
        # No repo/bundled tool-routing skill folder exists at this tmp path, and
        # even if it did the skill must not load when nothing was detected.
        self.assertEqual(block, 'Context order:\n1. PR/design contract\n'
                                 '2. raw source reads only when needed to prove/implement something')

    def test_each_capability_absent_in_turn_never_appears(self):
        all_names = list(capabilities.CAPABILITIES)
        for absent in all_names:
            detected = [n for n in all_names if n != absent]
            block = capabilities.capability_block(self.state, detected)
            label = capabilities.CAPABILITIES[absent]['label']
            self.assertNotIn(label, block, f'{label} leaked into the block when absent')

    def test_context7_present_appends_external_docs_policy(self):
        block = capabilities.capability_block(self.state, ['context7'])
        self.assertIn('External docs policy:', block)
        self.assertIn('Never rely on memory for version-sensitive library APIs', block)

    def test_context7_absent_omits_external_docs_policy_entirely(self):
        block = capabilities.capability_block(self.state, ['crg', 'graphify'])
        self.assertNotIn('External docs policy', block)

    def test_tool_routing_skill_loads_only_when_at_least_one_capability_present(self):
        # capability_block() imports resolve_skill_file from skills.py lazily (avoiding
        # a module-load-order dependency); patch skills.skill_root, the thing that
        # function ultimately reads from, rather than anything in capabilities itself.
        stack_root = Path(self.tmp.name) / 'stack-skills'
        stack_root.mkdir()
        import skills
        with mock.patch.object(skills, 'skill_root', return_value=stack_root):
            skill_dir = stack_root / 'tool-routing'
            skill_dir.mkdir()
            (skill_dir / 'prompt.md').write_text('ROUTING GUIDANCE MARKER')

            empty_block = capabilities.capability_block(self.state, [])
            self.assertNotIn('ROUTING GUIDANCE MARKER', empty_block)

            present_block = capabilities.capability_block(self.state, ['crg'])
            self.assertIn('ROUTING GUIDANCE MARKER', present_block)

    def test_tool_routing_skill_missing_on_disk_does_not_crash(self):
        # No skills/tool-routing directory exists at all under this tmp state/stack
        # root -- capability_block must degrade gracefully, not raise.
        import skills
        with mock.patch.object(skills, 'skill_root', return_value=Path(self.tmp.name) / 'no-such-skills-dir'):
            block = capabilities.capability_block(self.state, ['crg'])
        self.assertIn('Code Review Graph', block)


class EnabledCapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cap-enabled-')
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)

    def test_everything_enabled_by_default(self):
        reg = capabilities.enabled_capabilities(self.state)
        self.assertTrue(all(meta['enabled'] for meta in reg.values()))
        self.assertEqual(set(reg), set(capabilities.CAPABILITIES))

    def test_override_disables_one_without_affecting_others(self):
        (self.state / 'capability-overrides.json').write_text(json.dumps({'rtk': False}))
        reg = capabilities.enabled_capabilities(self.state)
        self.assertFalse(reg['rtk']['enabled'])
        self.assertTrue(reg['crg']['enabled'])


class CmdCapabilitiesTests(unittest.TestCase):
    """cmd_capabilities(args) via a real git repo + redirected external state,
    matching the pattern in tests/test_providers.py::CmdProvidersTests."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='capabilities-cmd-')
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        self.repo = home / 'repo'
        self.repo.mkdir()
        self.cfg = home / 'config' / 'ai-agent-stack'

        env = {k: v for k, v in os.environ.items() if k not in ('AI_GATE', 'AI_TASK_DIR')}
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'), AI_TASK_ID='capabilities-task')

        for args in (('init', '-q'), ('config', 'user.name', 'T'), ('config', 'user.email', 't@e.com')):
            subprocess.run(['git', *args], cwd=self.repo, check=True, capture_output=True)
        (self.repo / 'main.py').write_text('print("hi")\n')
        subprocess.run(['git', 'add', '.'], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-qm', 'init'], cwd=self.repo, check=True, capture_output=True)

        envp = mock.patch.dict(os.environ, env, clear=True)
        envp.start()
        self.addCleanup(envp.stop)
        cfgp = mock.patch.object(core, 'CONFIG_ROOT', self.cfg)
        cfgp.start()
        self.addCleanup(cfgp.stop)

        old_cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, old_cwd)

    def state(self):
        return core.repo_state(core.git_root())

    def _run(self, args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            capabilities.cmd_capabilities(args)
        return buf.getvalue()

    def _ns(self, capabilities_cmd, **fields):
        defaults = dict(name=None)
        return argparse.Namespace(capabilities_cmd=capabilities_cmd, **{**defaults, **fields})

    def test_list_shows_every_registered_capability(self):
        out = self._run(self._ns(None))
        for name in capabilities.CAPABILITIES:
            self.assertIn(name, out)

    def test_list_reports_missing_when_binary_absent(self):
        with mock.patch.object(capabilities.shutil, 'which', return_value=None):
            out = self._run(self._ns('list'))
        for line in out.splitlines()[1:]:
            self.assertIn('missing', line)

    def test_disable_then_list_reports_disabled_even_if_binary_present(self):
        with mock.patch.object(capabilities.shutil, 'which', return_value='/usr/bin/x'):
            self._run(self._ns('disable', name='rtk'))
            out = self._run(self._ns('list'))
        rtk_line = next(line for line in out.splitlines() if line.strip().startswith('·') and ' rtk ' in line)
        self.assertIn('disabled', rtk_line)

    def test_disable_persists_to_capability_overrides_json(self):
        self._run(self._ns('disable', name='graphify'))
        stored = json.loads((self.state() / 'capability-overrides.json').read_text())
        self.assertEqual(stored, {'graphify': False})

    def test_enable_after_disable_restores_it(self):
        self._run(self._ns('disable', name='graphify'))
        self._run(self._ns('enable', name='graphify'))
        stored = json.loads((self.state() / 'capability-overrides.json').read_text())
        self.assertEqual(stored, {'graphify': True})

    def test_unknown_capability_name_is_rejected(self):
        with self.assertRaises(SystemExit):
            self._run(self._ns('disable', name='not-a-real-capability'))

    def test_disabling_one_capability_changes_the_evidence_fingerprint(self):
        import gates
        plan = {'scope': {'base': 'HEAD'}, 'profile': 'fast'}
        root = core.git_root()
        state = self.state()
        before = gates.evidence_fingerprint(root, state, plan)
        self._run(self._ns('disable', name='rtk'))
        after = gates.evidence_fingerprint(root, state, plan)
        self.assertNotEqual(before, after)


if __name__ == '__main__':
    unittest.main()
