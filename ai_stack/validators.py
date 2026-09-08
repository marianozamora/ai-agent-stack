"""Bundled read-only semantic validators and their strict response protocol."""
import hashlib
from pathlib import Path
from typing import Any

from providers import run_codex_json  # noqa: F401  (re-exported: tests/callers import it from here)


INSTRUCTIONS = {
    'cleanup': 'Inspect the final diff for debug residue, unused imports/helpers, commented-out code, scratch notes and unnecessary scaffolding. Respect project tooling and useful comments. Report fixes; never apply them. Do not require later gates to exist.',
    'contract': 'Read the PR contract. Require a meaningful objective and nonempty, concrete acceptance criteria. Map EVERY criterion and must_not_change constraint to source locations and fresh test evidence. Empty, ambiguous or unverifiable requirements must return NEEDS_HUMAN. Do not invent acceptance criteria.',
    'review': 'Review correctness and regression risk in the diff and affected callers. Cite concrete failure paths and locations for blockers. Check error handling, edge cases and test coverage. Do not block for personal style preferences.',
    'security': 'Review relevant trust boundaries: authorization, tenant isolation, injection, untrusted paths, secrets, sensitive logs and insecure defaults. Cite concrete source evidence or exploit paths. Do not claim security based solely on passing tests.',
    'ponytail': 'Review maintainability against explicit repository rules, tooling and neighboring conventions: responsibility boundaries, duplication, error semantics, resource lifetime and meaningful tests. Block only material issues. Cite locations and established conventions.',
    'design': 'Read the design contract and actual implementation. Require populated components, states, responsive and material fidelity requirements as applicable. Verify every material requirement with source or existing visual evidence. Missing design evidence or an empty contract requires NEEDS_HUMAN; never fabricate visual verification.',
    'summary': 'Produce a concise PR draft in summary_markdown: title, concrete changes and motivation, actual checks with outcomes, risks and rollout notes. Use only fresh evidence; do not invent tests or results. The wrapper saves the draft outside the checkout. Do not add internal assistant attribution. Legitimate product/integration names are permitted.',
    'provenance': 'Inspect added/modified deliverables, untracked files, commit messages in the base..HEAD range and the generated PR summary. Reject accidental generated-by/co-author attribution and conversation/scratch residue. Distinguish legitimate product names, integrations, documentation and test fixtures from accidental attribution. Cite each blocker and why it is accidental. Never rewrite history.',
}


SCHEMA: dict[str, Any] = {
    'type': 'object', 'additionalProperties': False,
    'required': ['status', 'evidence', 'findings', 'summary_markdown'],
    'properties': {
        'status': {'type': 'string', 'enum': ['PASS', 'FAIL', 'NEEDS_HUMAN']},
        'evidence': {'type': 'array', 'items': {'type': 'string'}},
        'findings': {'type': 'array', 'items': {'type': 'string'}},
        'summary_markdown': {'type': 'string'},
    },
}


def check_verdict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(SCHEMA['required']):
        raise ValueError('Validator response does not match the required fields.')
    if value['status'] not in ('PASS', 'FAIL', 'NEEDS_HUMAN'):
        raise ValueError('Invalid validator status.')
    for key in ('evidence', 'findings'):
        if not isinstance(value[key], list) or not all(isinstance(x, str) and x.strip() for x in value[key]):
            raise ValueError(f'Invalid {key}.')
    if not isinstance(value['summary_markdown'], str):
        raise ValueError('Invalid summary_markdown.')
    if value['status'] == 'PASS':
        if not value['evidence'] or value['findings']:
            raise ValueError('PASS requires evidence and no unresolved findings.')
        if name == 'summary' and not value['summary_markdown'].strip():
            raise ValueError('Summary validator must return a PR draft.')
    return value


def intact_record(record, fingerprint):
    if not isinstance(record, dict) or record.get('fingerprint') != fingerprint:
        return False
    log = Path(record.get('log') or '')
    try:
        if not log.is_file() or hashlib.sha256(log.read_bytes()).hexdigest() != record.get('log_hash'):
            return False
        for artifact in record.get('artifacts', []):
            path = Path(artifact['path'])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != artifact['sha256']:
                return False
    except (OSError, KeyError, TypeError):
        return False
    return True


