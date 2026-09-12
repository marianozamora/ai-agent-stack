"""Generate a per-criterion completeness checklist from the PR contract.

Inspired by spec-kit's `/speckit.checklist`. `ai clarify` already refuses acceptance
criteria that are unverifiable as written; this is downstream of that, for criteria that
*passed* clarify but might still be underspecified in ways a pattern match can't catch --
a happy-path-only criterion with no boundary/error case ever considered, say. The checklist
does not judge sufficiency either: every item is a fixed, universal question a human (or the
builder, before calling it done) checks off by hand. Generating one is deterministic --
no model call -- exactly like `ai clarify`; answering it is not.
"""
from __future__ import annotations
import json, time
from core import git_root, repo_state, save_json, task_state
from workflow import contract_list_field

_list_field = contract_list_field

# One fixed set of prompts per criterion. Order matters: happy path first (the criterion's
# own words), then the angles a happy-path-only reading tends to miss.
_UNIVERSAL_ITEMS = (
    "A test exercises this criterion's stated happy path.",
    'A test exercises a boundary, empty, or zero-item case, if one plausibly applies.',
    'A test exercises the failure/error path, if one plausibly applies.',
    'The criterion is verifiable by reading its outcome, not by reading the implementation.',
    'Nothing in `must_not_change` contradicts this criterion.',
)


def generate_checklist(contract_text: str) -> dict:
    """Pure; no I/O."""
    acceptance = _list_field(contract_text, 'acceptance')
    must_not_change = _list_field(contract_text, 'must_not_change')
    items = [{'index': i, 'criterion': text,
              'checks': [{'text': prompt, 'checked': False} for prompt in _UNIVERSAL_ITEMS]}
             for i, text in enumerate(acceptance, 1)]
    return {'acceptance_count': len(acceptance), 'must_not_change_count': len(must_not_change),
            'items': items}


def render_markdown(report: dict) -> str:
    lines = [f"# Requirements checklist ({report['acceptance_count']} criteria)", '']
    if not report['items']:
        lines.append('(no acceptance criteria in the contract yet)')
    for item in report['items']:
        lines.append(f"## Criterion {item['index']}: {item['criterion']}")
        for check in item['checks']:
            lines.append(f"- [ ] {check['text']}")
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def cmd_checklist(args):
    state = repo_state(git_root()); task = task_state(state)
    contract = task / 'contracts' / 'current-pr.yml'
    if not contract.is_file():
        raise SystemExit('NEEDS_HUMAN: no PR contract for the active task. Run `ai start <id>` first.')
    report = generate_checklist(contract.read_text())
    payload = {'version': 1, 'generated_at': time.time(), 'contract': str(contract),
               'caveat': 'Universal completeness prompts, not a sufficiency judgement -- a human checks each off.',
               **report}
    save_json(task / 'state' / 'checklist.json', payload)
    markdown_path = task / 'state' / 'checklist.md'
    markdown_path.write_text(render_markdown(report))

    if getattr(args, 'json', False):
        print(json.dumps(payload, indent=2))
        return
    print('AI checklist')
    print('  contract:  ', contract)
    print('  acceptance:', f"{report['acceptance_count']} criteria")
    if not report['items']:
        print('\nNo acceptance criteria in the contract yet.')
    else:
        print()
        print(render_markdown(report))
    print('Report:  ', task / 'state' / 'checklist.json')
    print('Markdown:', markdown_path)
