"""Turn a task breakdown (or, absent one, the PR contract's acceptance criteria) into
real GitHub issues.

Inspired by spec-kit's `/speckit.taskstoissues`. Reuses `analyze.parse_tasks_file()`'s
`(AC: N)` tagging so a tasks file already written for `ai analyze` needs no rewriting to
also become issues. Dry-run by default, the same shape as `ai deploy run`: prints exactly
what would be created and creates nothing until `--execute`, which is also the one thing
here `require_human()` guards -- a model running inside a gate must not be able to file
real, externally-visible issues on its own initiative.
"""
from __future__ import annotations
import json, shutil
from pathlib import Path
from core import git_root, repo_state, require_human, run, task_state
from workflow import contract_list_field

_list_field = contract_list_field


def planned_issues(contract_text: str, tasks: list[dict] | None) -> list[dict]:
    """Pure; no I/O. One issue per checked-off-eligible task (unchecked only -- a
    completed task has nothing left to track), or one per acceptance criterion when no
    tasks file was given.
    """
    if tasks:
        return [{'title': t['text'], 'body': f"Acceptance criteria: {', '.join(f'AC {i}' for i in t['ac_refs'])}"
                                              if t['ac_refs'] else '(no acceptance criteria tagged)'}
                for t in tasks if not t['checked']]
    acceptance = _list_field(contract_text, 'acceptance')
    return [{'title': f'AC {i}: {text}', 'body': f'Acceptance criterion {i} from the current PR contract.'}
            for i, text in enumerate(acceptance, 1)]


def cmd_tasks_to_issues(args):
    root = git_root(); state = repo_state(root); task = task_state(state)
    contract = task / 'contracts' / 'current-pr.yml'
    if not contract.is_file():
        raise SystemExit('NEEDS_HUMAN: no PR contract for the active task. Run `ai start <id>` first.')
    tasks = None
    if args.tasks_file:
        from analyze import parse_tasks_file
        tasks_path = Path(args.tasks_file)
        if not tasks_path.is_file():
            raise SystemExit(f'--tasks-file not found: {tasks_path}')
        tasks = parse_tasks_file(tasks_path.read_text())
    issues = planned_issues(contract.read_text(), tasks)
    if not issues:
        print('Nothing to file: no unchecked tasks and no acceptance criteria.'); return

    if not args.execute:
        print(f'DRY RUN: {len(issues)} issue(s) would be created (pass --execute to actually create them):')
        for issue in issues:
            print(f"  - {issue['title']}")
        if getattr(args, 'json', False):
            print(json.dumps(issues, indent=2))
        return

    require_human('Filing GitHub issues')
    if not shutil.which('gh'):
        raise SystemExit('`gh` is not installed; required to create issues (see `ai doctor`).')
    created = []
    for issue in issues:
        url = run(['gh', 'issue', 'create', '--title', issue['title'], '--body', issue['body']], cwd=root)
        created.append({'title': issue['title'], 'url': url})
        print(f"Created: {url}  ({issue['title']})")
    if getattr(args, 'json', False):
        print(json.dumps(created, indent=2))
