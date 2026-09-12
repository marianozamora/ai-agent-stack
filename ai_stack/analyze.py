"""Cross-artifact coverage check: does a task breakdown actually cover every acceptance
criterion in the PR contract, with nothing left dangling in either direction?

Inspired by spec-kit's `/speckit.analyze` (github/spec-kit), which checks spec/plan/tasks
consistency before implementation starts. This stack has no separate plan/tasks artifacts
of its own -- `to-tickets` produces a breakdown as prose in a model's response, not a file
this CLI can read -- so this instead reads whatever tasks file the human saved (any Markdown
checklist), the same opt-in, pasted-document shape `--ticket-file` already uses for tickets.

Deliberately traceability, not comprehension: a task is "covers criterion N" only when its
line is explicitly tagged `(AC: N)` (comma-separated for several). This is more typing than
guessing from keyword overlap, but guessing is exactly the kind of unverified sufficiency
judgement `ai clarify`'s own docstring refuses to make -- an explicit tag is a claim a human
or agent chose to write, not an inference this tool invented.
"""
from __future__ import annotations
import json, re, time
from pathlib import Path
from core import git_root, repo_state, save_json, task_state
from workflow import contract_list_field

_list_field = contract_list_field

# `- [ ] Add the retry column (AC: 1, 3)` / `- [x] Wire up the endpoint` (no tag: untagged).
_TASK_LINE = re.compile(r'^\s*-\s*\[([ xX])\]\s*(.+?)(?:\s*\(AC:\s*([0-9,\s]+)\))?\s*$')


def parse_tasks_file(text: str) -> list[dict]:
    """Every checklist line in a Markdown tasks document. Lines that aren't a checklist
    item (headings, prose, blank lines) are silently skipped -- this reads a task
    breakdown, not a generic Markdown parser.
    """
    tasks = []
    for lineno, line in enumerate(text.splitlines(), 1):
        m = _TASK_LINE.match(line)
        if not m:
            continue
        checked, body, refs = m.group(1).lower() == 'x', m.group(2).strip(), m.group(3)
        ac_refs = sorted({int(x) for x in re.findall(r'\d+', refs or '')})
        tasks.append({'line': lineno, 'text': body, 'checked': checked, 'ac_refs': ac_refs})
    return tasks


def analyze_coverage(contract_text: str, tasks: list[dict]) -> dict:
    """Pure; no I/O. Every acceptance criterion must be claimed by at least one task's
    `(AC: N)` tag; every tag must point at a criterion that actually exists. A task with
    no tag at all is reported separately -- not an error (plenty of real work has no
    single criterion, e.g. groundwork or cleanup) but worth a human's eye.
    """
    acceptance = _list_field(contract_text, 'acceptance')
    n = len(acceptance)
    covered = {i for task in tasks for i in task['ac_refs'] if 1 <= i <= n}
    uncovered = [{'index': i, 'text': acceptance[i - 1]} for i in range(1, n + 1) if i not in covered]
    invalid_refs = [{'line': t['line'], 'text': t['text'], 'refs': [r for r in t['ac_refs'] if not (1 <= r <= n)]}
                    for t in tasks if any(not (1 <= r <= n) for r in t['ac_refs'])]
    untagged = [{'line': t['line'], 'text': t['text']} for t in tasks if not t['ac_refs']]
    blockers = []
    if n == 0:
        blockers.append('The contract has no acceptance criteria yet; run `ai clarify` first.')
    if not tasks:
        blockers.append('The tasks file has no checklist items (`- [ ] ...`) to analyze.')
    for item in uncovered:
        blockers.append(f"Acceptance criterion {item['index']} has no task tagged (AC: {item['index']}): "
                         f"{item['text']!r}")
    for item in invalid_refs:
        blockers.append(f"Task at line {item['line']} references nonexistent criteria {item['refs']}: "
                         f"{item['text']!r}")
    return {
        'acceptance_count': n, 'task_count': len(tasks),
        'covered': sorted(covered), 'uncovered': uncovered,
        'invalid_refs': invalid_refs, 'untagged': untagged,
        'blockers': blockers, 'status': 'NEEDS_HUMAN' if blockers else 'READY',
    }


def cmd_analyze(args):
    state = repo_state(git_root()); task = task_state(state)
    contract = task / 'contracts' / 'current-pr.yml'
    if not contract.is_file():
        raise SystemExit('NEEDS_HUMAN: no PR contract for the active task. Run `ai start <id>` first.')
    tasks_path = args.tasks_file
    if not tasks_path:
        raise SystemExit('--tasks-file is required: a Markdown checklist with `(AC: N)` tags per task.')
    tasks_file = Path(tasks_path)
    if not tasks_file.is_file():
        raise SystemExit(f'--tasks-file not found: {tasks_file}')
    tasks = parse_tasks_file(tasks_file.read_text())
    report = analyze_coverage(contract.read_text(), tasks)
    payload = {'version': 1, 'generated_at': time.time(), 'contract': str(contract),
               'tasks_file': str(tasks_file),
               'caveat': 'Deterministic traceability check (explicit (AC: N) tags only), not a sufficiency review.',
               **report}
    save_json(task / 'state' / 'analyze.json', payload)

    if getattr(args, 'json', False):
        print(json.dumps(payload, indent=2))
        if report['blockers']:
            raise SystemExit(f"NEEDS_HUMAN: {len(report['blockers'])} blocker(s).")
        return
    print('AI analyze')
    print('  contract:  ', contract)
    print('  tasks file:', tasks_file)
    print('  acceptance:', f"{report['acceptance_count']} criteria, {len(report['covered'])} covered")
    print('  tasks:     ', f"{report['task_count']} total, {len(report['untagged'])} untagged")
    if report['blockers']:
        print('\nBlockers (resolve before implementing):')
        for b in report['blockers']:
            print(f'  - {b}')
    if report['untagged']:
        print('\nUntagged tasks (not necessarily wrong; worth a human eye):')
        for item in report['untagged']:
            print(f"  - line {item['line']}: {item['text']!r}")
    if not report['blockers']:
        print('\nEvery acceptance criterion has at least one tagged task.')
    print('\nReport:', task / 'state' / 'analyze.json')
    if report['blockers']:
        raise SystemExit(f"NEEDS_HUMAN: {len(report['blockers'])} blocker(s); "
                         f"fix {tasks_file} or {contract} and re-run `ai analyze`.")
