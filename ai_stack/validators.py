"""Bundled read-only semantic validators and their strict response protocol."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


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


SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['status', 'evidence', 'findings', 'summary_markdown'],
    'properties': {
        'status': {'type': 'string', 'enum': ['PASS', 'FAIL', 'NEEDS_HUMAN']},
        'evidence': {'type': 'array', 'items': {'type': 'string'}},
        'findings': {'type': 'array', 'items': {'type': 'string'}},
        'summary_markdown': {'type': 'string'},
    },
}


def check_verdict(value, name):
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


def run_codex_json(executable, root, review_dir, name, prompt, schema, checker, timeout=None):
    """Invoke Codex read-only/ephemeral with a required output schema; never mutates the checkout.

    Shared by the gate validator protocol and any other repository-analysis prompt that
    needs the same safety guarantees (no writes, no approvals, bounded/validated output).
    A gate caller relies on the enclosing `ai gate` process-group timeout instead of passing
    one here; a caller with no enclosing gate (e.g. `ai profile --deep`) must pass one.
    """
    with tempfile.TemporaryDirectory(prefix='codex-', dir=review_dir) as directory:
        directory = Path(directory)
        schema_path = directory/'schema.json'
        final = directory/'final.json'
        schema_path.write_text(json.dumps(schema))
        events = directory/'events.jsonl'
        diagnostics = review_dir/f'{name}-events.jsonl'
        with events.open('w') as output:
            try:
                result = subprocess.run([executable, 'exec', '-s', 'read-only',
                    '-c', 'approval_policy="never"', '--ephemeral', '--json',
                    '--output-schema', str(schema_path), '--output-last-message', str(final), '-'],
                    input=prompt, text=True, cwd=root, stdout=output, stderr=subprocess.STDOUT, timeout=timeout)
            except subprocess.TimeoutExpired:
                diagnostics.write_bytes(events.read_bytes())
                raise ValueError(f'Reviewer timed out after {timeout}s; diagnostics: {diagnostics}')
        # Keep process diagnostics externally for failures, including missing final output.
        diagnostics.write_bytes(events.read_bytes())
        if result.returncode:
            raise ValueError(f'Reviewer exited {result.returncode}; diagnostics: {diagnostics}')
        if not final.is_file() or final.stat().st_size > 64000:
            raise ValueError(f'Missing or oversized reviewer output; diagnostics: {diagnostics}')
        value = checker(json.loads(final.read_text()))
        usage = {}
        for line in events.read_text(errors='replace').splitlines():
            try:
                event = json.loads(line)
                if isinstance(event, dict) and event.get('type') == 'turn.completed':
                    reported = event.get('usage', {})
                    if isinstance(reported, dict):
                        for key in ('input_tokens', 'output_tokens'):
                            reported_value = reported.get(key)
                            if type(reported_value) is int and reported_value >= 0:
                                usage[key] = usage.get(key, 0) + reported_value
            except ValueError:
                continue
        if usage:
            value['usage'] = usage
        return value


def model_verdict(executable, root, task, name, prompt):
    # Inherit the gate process group: its timeout kills the reviewer and child tools.
    # The public entry point requires ai gate, which owns execution and freshness.
    return run_codex_json(executable, root, task/'review', name, prompt, SCHEMA,
                           lambda value: check_verdict(value, name))
