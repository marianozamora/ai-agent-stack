"""A spec-kit `spec.md` must be readable by `ai start --ticket-file` with no converter.

github/spec-kit's /specify step writes a spec with numbered "Acceptance Scenarios" and
bulleted "Functional Requirements" -- neither of which is the "## Acceptance Criteria"
plus bullets shape a hand-written ticket uses. Reading only the ticket shape reported a
perfectly complete spec as having zero acceptance criteria, which is the one input the
whole contract/evidence chain is measured against.
"""
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import workflow  # noqa: E402


SPEC_KIT_SPEC = textwrap.dedent('''\
    # Feature Specification: Login rate limiting

    ## User Scenarios & Testing

    ### Acceptance Scenarios
    1. **Given** 5 failed attempts in 60s, **When** a 6th arrives, **Then** the API returns 429.
    2. **Given** a 429 was returned, **When** 60s elapse, **Then** the counter resets.

    ## Requirements

    ### Functional Requirements
    - **FR-001**: System MUST count failed attempts per account, not per IP.
    - **FR-002**: System MUST expose the retry-after window in a header.
    - **FR-003**: System MUST [NEEDS CLARIFICATION: does this apply to SSO logins?]
    ''')


TICKET = textwrap.dedent('''\
    Add login rate limiting.

    ## Acceptance Criteria
    - Returns 429 after the 6th attempt in 60s
    - [ ] Counter resets after 60s

    Blocked by: PLAT-88 redis rollout
    ''')


class SpecKitSpecTests(unittest.TestCase):
    def setUp(self):
        self.analysis = workflow.analyze_ticket_text(SPEC_KIT_SPEC)

    def test_numbered_acceptance_scenarios_are_read(self):
        items = self.analysis['acceptance_items']
        self.assertTrue(any('a 6th arrives' in i for i in items), items)
        self.assertTrue(any('the counter resets' in i for i in items), items)

    def test_functional_requirements_are_read_too(self):
        items = self.analysis['acceptance_items']
        self.assertTrue(any('FR-001' in i for i in items), items)
        self.assertTrue(any('FR-002' in i for i in items), items)

    def test_has_acceptance_is_true(self):
        self.assertTrue(self.analysis['has_acceptance'])

    def test_clarification_markers_are_surfaced_not_swallowed(self):
        self.assertEqual(self.analysis['clarifications_needed'],
                         ['does this apply to SSO logins?'])

    def test_prose_outside_the_headings_is_not_mistaken_for_a_criterion(self):
        self.assertFalse(any('Feature Specification' in i for i in self.analysis['acceptance_items']))


class PlainTicketRegressionTests(unittest.TestCase):
    """The widened patterns must not change how an ordinary ticket already parsed."""

    def setUp(self):
        self.analysis = workflow.analyze_ticket_text(TICKET)

    def test_bullets_and_checkboxes_still_read(self):
        self.assertEqual(self.analysis['acceptance_items'],
                         ['Counter resets after 60s', 'Returns 429 after the 6th attempt in 60s'])

    def test_blockers_still_read(self):
        self.assertEqual(self.analysis['blockers_mentioned'], ['PLAT-88 redis rollout'])

    def test_no_clarification_markers_is_an_empty_list(self):
        self.assertEqual(self.analysis['clarifications_needed'], [])


class UnlabelledMarkerTests(unittest.TestCase):
    def test_bare_marker_is_still_reported(self):
        analysis = workflow.analyze_ticket_text('- Something [NEEDS CLARIFICATION]\n')
        self.assertEqual(analysis['clarifications_needed'], ['(unlabelled)'])


if __name__ == '__main__':
    unittest.main()
