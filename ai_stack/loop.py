"""`ai loop` -- the unattended builder <-> gates cycle for the active task.

Each round plans the prompt (open gate findings included), runs one non-interactive
builder session with a repository-derived allowlist, then runs the pipeline. It stops on
PR_READY, on anything that needs a human (a gate's round limit, a moved base, a budget,
unchanged code after a FAIL, a builder error), or after `--max-rounds`.

On the validation campaign this cycle was driven by hand for every task: `ai work
--plan-only`, `claude -p` with a hand-kept allowlist, `ai finish`, read the findings,
repeat. Every step here already existed; this only removes the operator from the loop.
"""
from __future__ import annotations
import argparse, os, time
from pathlib import Path
from core import git_root, load_json, repo_state, resolve_base, task_state
from metrics import record_metric
from preflight import preflight_failure

# Appended to the orchestration prompt: the headless builder must leave committed work
# behind, and a human's scope decision for this run reaches every round.
LOOP_SUFFIX = 'Commit the change when the repository\'s checks pass.'
# The contract lives in external state, outside the checkout, where a headless session
# may not read (danssme #103: every round's `cat` of it was denied). Inlined instead of
# granting the directory, which acceptEdits would also make writable.
CONTRACT_CHARS = 12000


def save_operator_note(task:Path, note:str|None)->str:
    """Record `--note` as the task's standing operator direction and return the one in force.

    Kept with the task, not just this run's prompt: the gates read it too, so a scope
    decision stops a reviewer re-raising what a human ruled out (danssme #103). A new
    `--note` replaces it; `--note ''` clears it.
    """
    from gates import operator_note
    path = task/'state/operator-note.md'
    if note is not None:
        if note.strip(): path.write_text(note.strip()+'\n')
        else: path.unlink(missing_ok=True)
    elif path.is_file():
        print(f'Operator direction from an earlier run is still in force ({path}); `--note \'\'` clears it.')
    return operator_note(task)


def _pipeline_outcome(task:Path, message:str|None)->tuple[str,str]:
    """(status, reason) of the pipeline run that just finished."""
    if message and 'NEEDS_HUMAN' in message:
        return 'NEEDS_HUMAN', message.strip()
    run = load_json(task/'state/pipeline-run.json', {})
    status = run.get('status') or ('FAILED' if message else 'PR_READY')
    return status, (message or '').strip()


def cmd_loop(args):
    from gates import cmd_pipeline, validator_config
    from lifecycle import cmd_planrun
    from providers import builder as get_builder, builder_effort, builder_model, builder_tools
    from tasks import require_open_task

    root = git_root(); state = repo_state(root)
    _, meta = require_open_task(state)
    task = task_state(state)
    active_builder = get_builder(state)
    if not hasattr(active_builder, 'run_headless'):
        raise SystemExit(f'NEEDS_HUMAN: the {active_builder.name} builder cannot run unattended; use `ai work`.')

    # The task carries its own base (`ai start --base`); fall back to the repository default.
    base = meta.get('base') or resolve_base(root, None, state)
    note = save_operator_note(task, args.note)
    rounds = args.max_rounds
    for number in range(1, rounds + 1):
        # Before each builder round: a round spent against a stopped service is paid for
        # and then judged as a failure of the change (danssme #95: three rounds, Docker down).
        failure = preflight_failure(root, state)
        if failure: raise SystemExit(failure)
        print(f'== round {number}/{rounds}: builder')
        # Same prompt path as `ai work`: the title only seeds a new contract's objective.
        cmd_planrun(argparse.Namespace(
            task=meta.get('title') or meta.get('id') or '', profile=args.profile, base=base, figma=None,
            no_figma=True, skill=None, ticket_file=None, keep_objective=True), launch=False)
        plan = load_json(task/'state/current-plan.json', {})
        prompt = (task/'state/current-run.md').read_text()
        contract = task/'contracts/current-pr.yml'
        if contract.is_file():
            prompt += '\n\nPR contract (its file is outside the checkout):\n' + contract.read_text(errors='replace')[:CONTRACT_CHARS]
        prompt += '\n\n' + LOOP_SUFFIX + (f'\n\nOperator direction: {note}' if note else '')
        tools = builder_tools(state, validator_config(state)['validators'], plan.get('capabilities') or [])
        tools += [t for t in (args.allow or []) if t not in tools]
        env = dict(os.environ, AI_TASK_ID=load_json(task/'task.json', {}).get('id', ''))
        started = time.monotonic()
        report = active_builder.run_headless(prompt, root, env, model=builder_model(state, plan.get('profile')),
                                             effort=builder_effort(state, plan.get('profile')),
                                             tools=tools, timeout=args.timeout)
        record_metric(state, 'builder_round', round=number, ok=report['ok'], cost_usd=report.get('cost_usd'),
                      turns=report.get('turns'), denied=len(report.get('denied') or []),
                      duration_seconds=round(time.monotonic() - started, 1))
        cost = report.get('cost_usd')
        print(f"   builder: {'ok' if report['ok'] else 'FAILED'}, {report.get('turns')} turns"
              + (f', ${cost:.2f}' if isinstance(cost, (int, float)) else ''))
        for command in (report.get('denied') or [])[:5]:
            print(f'   denied: {command[:120]}  (allow it with --allow or `ai providers set --builder-allow`)')
        if not report['ok']:
            raise SystemExit(f"NEEDS_HUMAN: the builder session did not finish: {report.get('result', '')[:400]}")

        print(f'== round {number}/{rounds}: gates')
        message = None
        try:
            cmd_pipeline(argparse.Namespace(dry_run=False, resume=True, allow_overrun=False, force_unlock=False))
        except SystemExit as exc:
            message = str(exc.code) if exc.code not in (None, 0, 1) else None
            if message is None: message = 'FAILED'
        status, reason = _pipeline_outcome(task, None if message == 'FAILED' else message)
        if message == 'FAILED' and status == 'PR_READY': status = 'FAILED'
        print(f'   gates: {status}')
        if status == 'PR_READY':
            print(f'PR_READY after {number} round(s).'); return
        if status != 'FAILED':
            raise SystemExit(reason if reason.startswith('NEEDS_HUMAN') else f'NEEDS_HUMAN: pipeline stopped ({status}). {reason}')
        # FAILED: a gate found something; the next round's prompt lists its findings.
    raise SystemExit(f'NEEDS_HUMAN: not PR_READY after {rounds} round(s); read the open findings with `ai work --plan-only`.')
