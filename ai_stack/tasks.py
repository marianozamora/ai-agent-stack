"""Explicit task lifecycle: start, switch, close, current, list.

Before this, task identity fell back to the current branch, so two unrelated
tickets worked on one branch shared `contracts/current-pr.yml` — and
ensure_contract() only rewrites `objective:`, leaving the previous ticket's
acceptance criteria, must_not_change constraints and risk notes in place for the
next one. An explicit lifecycle makes the active task visible and makes contract
reuse a deliberate act (`ai start --resume`) instead of an accident.
"""
from __future__ import annotations
import contextlib, json, os, re, socket, time
from pathlib import Path
from core import (TASK_ID_PATTERN, active_task_id, git_root, load_json, repo_state,
                  resolve_base, save_json, set_active_task, shasum, task_state)
from metrics import record_metric
from workflow import analyze_ticket_text, ticket_snapshot


OPEN_STATES = ('active', 'paused')


def task_key(root:Path,identity:str)->str:
    return shasum(str(root.resolve())+'\0'+identity)[:24]


def task_dir(state:Path,root:Path,identity:str)->Path:
    return state/'tasks'/task_key(root,identity)


def read_task(state:Path,root:Path,identity:str)->dict:
    return load_json(task_dir(state,root,identity)/'task.json',{})


def write_task(state:Path,root:Path,identity:str,**fields):
    directory=task_dir(state,root,identity)
    directory.mkdir(parents=True,exist_ok=True)
    meta=load_json(directory/'task.json',{})
    if not isinstance(meta,dict): meta={}
    meta.update(fields)
    save_json(directory/'task.json',meta)
    return meta


def check_identity(identity:str)->str:
    if not re.fullmatch(TASK_ID_PATTERN,identity or ''):
        raise SystemExit('Invalid task ID: use letters, digits and . _ - / starting with an alphanumeric.')
    return identity


def all_tasks(state:Path,root:Path)->list[dict]:
    """Every task recorded for this checkout, newest first."""
    directory=state/'tasks'
    if not directory.is_dir(): return []
    here=str(root.resolve()); out=[]
    for meta_path in directory.glob('*/task.json'):
        meta=load_json(meta_path,{})
        if isinstance(meta,dict) and meta.get('root')==here: out.append(meta)
    return sorted(out,key=lambda m:m.get('created_at',0),reverse=True)


def require_open_task(state:Path)->tuple[Path,dict]:
    """The task directory for the current identity, refusing a closed one.

    Shared by `ai gate` and `ai pipeline`: recording new evidence against a closed
    task would silently reopen work whose evidence was already frozen for a PR.
    """
    task=task_state(state)
    meta=load_json(task/'task.json',{})
    if meta.get('status')=='closed':
        raise SystemExit(f"NEEDS_HUMAN: task {meta.get('id')!r} is closed "
                         f"({meta.get('close_reason') or 'no reason recorded'}). "
                         "Run `ai start <id>` for new work, or `ai switch <id>` to an open task.")
    return task,meta


LOCK_NAME = 'pipeline.lock'


def process_alive(pid:int)->bool:
    try: os.kill(pid,0)
    except ProcessLookupError: return False
    except PermissionError: return True   # exists, owned by another user
    except (OSError,TypeError,ValueError): return False
    return True


def lock_state(held:dict)->str:
    """'ours' | 'stale' | 'held' | 'foreign' for an existing lock file."""
    if not isinstance(held,dict) or not isinstance(held.get('pid'),int): return 'stale'
    if held['pid']==os.getpid() and held.get('host')==socket.gethostname(): return 'ours'
    # A pid from another machine says nothing about a process here, so it is never
    # assumed dead: a shared external state directory would silently double-run.
    if held.get('host')!=socket.gethostname(): return 'foreign'
    return 'held' if process_alive(held['pid']) else 'stale'


@contextlib.contextmanager
def task_lock(directory:Path,command:str,*,force:bool=False):
    """Serialize gate/pipeline runs for one task.

    Yields True when this call acquired the lock and False when the current process
    already holds it — `ai pipeline` invokes `ai gate` in-process, and a re-entrant
    acquisition must neither deadlock nor release the outer lock on the inner exit.
    """
    path=directory/LOCK_NAME
    payload={'pid':os.getpid(),'host':socket.gethostname(),'command':command,'started_at':time.time()}
    for _ in range(2):
        try:
            handle=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        except FileExistsError:
            held=load_json(path,{}); state=lock_state(held)
            if state=='ours':
                yield False
                return
            if state=='stale' and not force:
                print(f'Reclaiming a stale lock from pid {held.get("pid")} (no such process).')
            elif not force:
                where=f' on {held.get("host")}' if state=='foreign' else ''
                raise SystemExit(
                    f'NEEDS_HUMAN: {held.get("command","a pipeline")} is already running for this task '
                    f'(pid {held.get("pid")}{where}, since {time.strftime("%H:%M:%S",time.localtime(held.get("started_at",0)))}).\n'
                    f'Wait for it to finish, or pass --force-unlock if that process is gone.\nLock: {path}') from None
            path.unlink(missing_ok=True)
            continue
        try:
            with os.fdopen(handle,'w') as output: json.dump(payload,output)
            yield True
        finally:
            path.unlink(missing_ok=True)
        return
    raise SystemExit(f'NEEDS_HUMAN: could not acquire the task lock at {path}.')


def running_tasks(state:Path,root:Path)->list[dict]:
    """Tasks in this checkout that currently hold a pipeline lock."""
    out=[]
    for meta in all_tasks(state,root):
        path=task_dir(state,root,meta['id'])/LOCK_NAME
        if path.exists():
            held=load_json(path,{})
            out.append({'id':meta['id'],'pid':held.get('pid'),'host':held.get('host'),
                        'command':held.get('command'),'started_at':held.get('started_at'),
                        'state':lock_state(held)})
    return out


def contract_path(state:Path,root:Path,identity:str)->Path:
    return task_dir(state,root,identity)/'contracts'/'current-pr.yml'


def cmd_start(args):
    root=git_root(); state=repo_state(root); identity=check_identity(args.id)
    existing=read_task(state,root,identity)
    if existing and existing.get('status') in OPEN_STATES and not args.resume:
        raise SystemExit(f'Task {identity!r} already exists ({existing["status"]}). '
                         'Use `ai switch` to return to it, or `ai start --resume` to continue it.')
    if existing.get('status')=='closed' and not args.resume:
        raise SystemExit(f'Task {identity!r} is closed. Use `ai start --resume` to reopen it deliberately.')
    current=active_task_id(state,root)
    if current and current!=identity and not args.switch:
        raise SystemExit(f'Task {current!r} is still active here. Close it (`ai close`), '
                         'or pass --switch to pause it and start this one.')
    if current and current!=identity:
        write_task(state,root,current,status='paused',paused_at=int(time.time()))
    base=resolve_base(root,args.base,state)
    # Point at the new task *before* touching any contract, so ensure_contract() and
    # every later task_state() call resolve to this task's own directory.
    set_active_task(state,root,identity)
    task=task_state(state)
    fresh=not contract_path(state,root,identity).exists()
    title=args.title or existing.get('title') or ''
    write_task(state,root,identity,status='active',title=title,base=base,
               started_at=int(time.time()),closed_at=None,close_reason=None)
    # A brand-new task must never inherit the previous one's acceptance criteria, and
    # resuming must never rewrite an objective a human edited: ensure_contract() rewrites
    # `objective:` whenever it is given a non-empty one, so only pass one when the
    # contract is new or the caller explicitly supplied a title.
    from lifecycle import ensure_contract, populate_acceptance_if_empty
    ensure_contract(state, args.title or (title or identity if fresh else ''))
    detected=0; clarifications=[]
    if getattr(args,'ticket_file',None):
        analysis=analyze_ticket_text(Path(args.ticket_file).read_text())
        populate_acceptance_if_empty(task/'contracts/current-pr.yml',analysis['acceptance_items'])
        save_json(task/'state/ticket.json',ticket_snapshot(analysis))
        detected=len(analysis['acceptance_items']); clarifications=analysis['clarifications_needed']
    record_metric(state,'task_start',task_id=identity,resumed=bool(existing))
    print(f'Started task: {identity}')
    if title: print(f'  title:    {title}')
    print(f'  base:     {base}')
    print(f'  contract: {task/"contracts/current-pr.yml"}'+('' if fresh else ' (existing, resumed)'))
    if getattr(args,'ticket_file',None): print(f'  ticket:   {detected} acceptance item(s) detected')
    if clarifications:
        print(f'  clarify:  {len(clarifications)} unresolved marker(s) in the source; run `ai clarify`')


def cmd_switch(args):
    root=git_root(); state=repo_state(root); identity=check_identity(args.id)
    meta=read_task(state,root,identity)
    if not meta: raise SystemExit(f'No task {identity!r} in this checkout. Use `ai start {identity}`.')
    if meta.get('status')=='closed':
        raise SystemExit(f'Task {identity!r} is closed. Use `ai start {identity} --resume` to reopen it.')
    current=active_task_id(state,root)
    if current and current!=identity:
        write_task(state,root,current,status='paused',paused_at=int(time.time()))
    set_active_task(state,root,identity)
    write_task(state,root,identity,status='active')
    record_metric(state,'task_switch',task_id=identity,previous=current or None)
    print(f'Active task: {identity}'+(f' (paused {current})' if current and current!=identity else ''))


def cmd_close(args):
    root=git_root(); state=repo_state(root)
    identity=check_identity(args.id) if args.id else active_task_id(state,root)
    if not identity: raise SystemExit('No active task to close. Name one: `ai close <id>`.')
    meta=read_task(state,root,identity)
    if not meta: raise SystemExit(f'No task {identity!r} in this checkout.')
    if meta.get('status')=='closed':
        print(f'Task {identity!r} is already closed.'); return
    lock=task_dir(state,root,identity)/'pipeline.lock'
    if lock.exists():
        # A lock left behind by a process that no longer exists must not block close
        # forever -- task_lock() already reclaims exactly this case for a fresh
        # acquisition, so closing gets the same recovery instead of a permanent wedge.
        if lock_state(load_json(lock,{}))=='stale':
            held=load_json(lock,{})
            print(f'Reclaiming a stale lock from pid {held.get("pid")} (no such process).')
            lock.unlink(missing_ok=True)
        else:
            raise SystemExit(f'A pipeline is still running for {identity!r} ({lock}). '
                             'Wait for it, or remove the lock if the process is gone.')
    write_task(state,root,identity,status='closed',closed_at=int(time.time()),
               close_reason=args.reason or '')
    readiness=load_json(task_dir(state,root,identity)/'state/readiness.json',{}).get('status','none')
    # Record before clearing the pointer: record_metric() resolves the task through
    # task_state(), which once the pointer is gone has no active task to resolve and
    # would refuse rather than file the close event against the right task.
    record_metric(state,'task_close',task_id=identity,readiness=readiness,reason=args.reason or '')
    if active_task_id(state,root)==identity: set_active_task(state,root,None)
    print(f'Closed task: {identity}')
    print(f'  readiness at close: {readiness}')
    print(f'  evidence retained:  {task_dir(state,root,identity)}')


def cmd_work(args):
    """`ai work` — plan/launch for the active task, taking its title and base from it.

    Composition only: it builds the same namespace `ai run`/`ai plan` receive and
    hands off to cmd_planrun, so there is one prompt-building path, not two.
    """
    import argparse
    from lifecycle import cmd_planrun
    root=git_root(); state=repo_state(root)
    identity=active_task_id(state,root)
    if not identity: raise SystemExit('No active task. Run `ai start <id>` first.')
    _,meta=require_open_task(state)
    cmd_planrun(argparse.Namespace(
        task=args.task or meta.get('title') or identity,
        profile=args.profile,base=args.base,figma=args.figma,no_figma=args.no_figma,
        skill=args.skill,ticket_file=args.ticket_file),launch=not args.plan_only)


def cmd_finish(args):
    """`ai finish` — run the required gates for the active task and certify readiness.

    Composition only: preflight, then cmd_pipeline, which already ends in cmd_ready.
    The preflight exists to turn "configure validators for checks, regression" into
    an instruction the operator can act on directly.
    """
    import argparse
    from core import required_gates
    from gates import cmd_pipeline, validator_config
    root=git_root(); state=repo_state(root)
    task,meta=require_open_task(state)
    plan=load_json(task/'state/current-plan.json',{})
    if not plan: raise SystemExit('NEEDS_HUMAN: this task has no plan yet. Run `ai work` first.')
    configured=validator_config(state)['validators']
    missing=[name for name in required_gates(root,plan) if name not in configured]
    if missing:
        print('NEEDS_HUMAN: no validator configured for: '+', '.join(missing))
        deterministic=[name for name in missing if name in ('checks','regression')]
        if deterministic: print('  ai validators propose --apply   # configures '+', '.join(deterministic))
        if [name for name in missing if name not in ('checks','regression')]:
            print('  ai validators install           # configures the bundled semantic gates')
        raise SystemExit(1)
    print(f'Finishing task: {meta.get("id")}')
    cmd_pipeline(argparse.Namespace(dry_run=False,resume=not args.no_resume,
                                    allow_overrun=getattr(args,'allow_overrun',False),
                                    force_unlock=getattr(args,'force_unlock',False)))


def cmd_current(args):
    root=git_root(); state=repo_state(root); identity=active_task_id(state,root)
    if not identity:
        idle={'active':None,'hint':'No active task. Run `ai start <id>`.'}
        print(json.dumps(idle,indent=2) if args.json else idle['hint'])
        return
    directory=task_dir(state,root,identity)
    meta=read_task(state,root,identity)
    plan=load_json(directory/'state/current-plan.json',{})
    readiness=load_json(directory/'state/readiness.json',{})
    gates={p.stem:bool(load_json(p,{}).get('passed')) for p in sorted((directory/'gates').glob('*.json'))}
    run=load_json(directory/'state/pipeline-run.json',{})
    lock=load_json(directory/LOCK_NAME,{}) if (directory/LOCK_NAME).exists() else {}
    payload:dict[str,object]={'id':identity,'status':meta.get('status'),'title':meta.get('title') or None,
             'base':meta.get('base') or plan.get('scope',{}).get('base'),
             'profile':plan.get('profile'),'risk':plan.get('risk',{}).get('risk'),
             'task_type':plan.get('task_type'),'directory':str(directory),
             'contract':str(directory/'contracts/current-pr.yml'),
             'readiness':readiness.get('status','none'),'gates':gates,
             'last_run':run.get('status'),'overrun_gate':run.get('overrun_gate'),
             'running':bool(lock) and lock_state(lock)!='stale'}
    if args.json: print(json.dumps(payload,indent=2)); return
    print(f'Active task: {identity}'+(f' — {meta["title"]}' if meta.get('title') else ''))
    print(f'  status:    {payload["status"]}')
    print(f'  base:      {payload["base"] or "not planned yet"}')
    print(f'  profile:   {payload["profile"] or "not planned yet"}')
    print(f'  risk:      {payload["risk"] or "not planned yet"}')
    print(f'  contract:  {payload["contract"]}')
    print(f'  readiness: {payload["readiness"]}')
    print('  gates:     '+(', '.join(f'{n}={"PASS" if ok else "FAIL"}' for n,ok in gates.items()) or 'none recorded'))
    if run:
        detail=f' (crossed at {run["overrun_gate"]})' if run.get('status')=='BUDGET_EXCEEDED' else ''
        print(f'  last run:  {run.get("status")}{detail}')
        if run.get('remaining'): print(f'  not run:   {", ".join(run["remaining"])}')
    if payload['running']:
        print(f'  RUNNING:   {lock.get("command")} (pid {lock.get("pid")} on {lock.get("host")})')


def cmd_tasks(args):
    root=git_root(); state=repo_state(root)
    rows=[m for m in all_tasks(state,root) if not args.status or m.get('status')==args.status]
    if args.json: print(json.dumps(rows,indent=2)); return
    if not rows: print('No tasks recorded for this checkout.'); return
    active=active_task_id(state,root)
    for meta in rows:
        marker='*' if meta.get('id')==active else ' '
        title=f' — {meta["title"]}' if meta.get('title') else ''
        print(f'{marker} {meta.get("status","?"):8} {meta.get("id")}{title}')
