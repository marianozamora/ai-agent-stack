from __future__ import annotations
import csv, hashlib, json, sqlite3, sys, time
from pathlib import Path
from typing import Any
from core import VERSION, context_caps, git_root, load_json, repo_state, require_human, task_state
from workflow import campaign_report, parse_window, summarize, usage_report


INDEX_VERSION='1'


def _metric_connection(state:Path)->sqlite3.Connection:
    """Open the disposable SQLite index; metrics.jsonl remains the source of truth."""
    connection=sqlite3.connect(state/'metrics.sqlite3')
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA synchronous=NORMAL')
    connection.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    connection.execute('''CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY, event TEXT, task_key TEXT, gate_name TEXT, profile TEXT,
        risk TEXT, task_type TEXT, ts REAL, payload TEXT NOT NULL)''')
    connection.execute('CREATE INDEX IF NOT EXISTS events_task_gate ON events(event, task_key, gate_name)')
    connection.execute('CREATE INDEX IF NOT EXISTS events_dimensions ON events(event, profile, risk, task_type)')
    return connection


def _meta(connection:sqlite3.Connection)->dict[str,str]:
    return dict(connection.execute('SELECT key, value FROM meta'))


def _digest_slice(path:Path,start:int,length:int)->str:
    if not path.exists() or length<=0: return hashlib.sha256(b'').hexdigest()
    with path.open('rb') as source:
        source.seek(max(0,start)); return hashlib.sha256(source.read(length)).hexdigest()


def _store_row(connection:sqlite3.Connection,row:dict[str,Any]):
    connection.execute('''INSERT INTO events(event,task_key,gate_name,profile,risk,task_type,ts,payload)
        VALUES(?,?,?,?,?,?,?,?)''',(row.get('event'),row.get('task_key'),row.get('gate'),row.get('profile'),
        row.get('risk'),row.get('task_type'),row.get('ts'),json.dumps(row,sort_keys=True)))


def _sync_metric_index(state:Path,*,force=False)->sqlite3.Connection:
    """Incrementally index appended JSONL rows; rebuild after rewrites or corruption."""
    path=state/'metrics.jsonl'; connection=_metric_connection(state); metadata=_meta(connection)
    stat=path.stat() if path.exists() else None
    size=stat.st_size if stat else 0
    indexed=int(metadata.get('source_size','0')) if metadata.get('version')==INDEX_VERSION else -1
    unchanged=(not force and indexed==size and metadata.get('source_mtime_ns')==str(stat.st_mtime_ns if stat else 0))
    if unchanged: return connection
    append_only=(not force and indexed>=0 and size>=indexed
        and metadata.get('head_hash')==_digest_slice(path,0,min(indexed,4096))
        and metadata.get('anchor_hash')==_digest_slice(path,max(0,indexed-4096),min(indexed,4096)))
    if not append_only:
        connection.execute('DELETE FROM events'); indexed=0; malformed=0
    else:
        malformed=int(metadata.get('malformed','0'))
    if path.exists():
        with path.open('rb') as source:
            source.seek(indexed)
            for raw in source:
                try:
                    row=json.loads(raw.decode())
                    if not isinstance(row,dict): raise ValueError()
                    _store_row(connection,row)
                except (UnicodeDecodeError,ValueError): malformed+=1
    values={'version':INDEX_VERSION,'source_size':str(size),'source_mtime_ns':str(stat.st_mtime_ns if stat else 0),
        'malformed':str(malformed),'head_hash':_digest_slice(path,0,min(size,4096)),
        'anchor_hash':_digest_slice(path,max(0,size-4096),min(size,4096))}
    connection.executemany('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)',values.items())
    connection.commit(); return connection


def rebuild_metric_index(state:Path):
    connection=_sync_metric_index(state,force=True); connection.close()


def load_metric_rows(state:Path,*,event:str|None=None,task_key:str|None=None)->tuple[list[dict],int]:
    try:
        connection=_sync_metric_index(state)
    except sqlite3.DatabaseError:
        (state/'metrics.sqlite3').unlink(missing_ok=True)
        connection=_sync_metric_index(state,force=True)
    clauses=[]; values=[]
    if event is not None: clauses.append('event=?'); values.append(event)
    if task_key is not None: clauses.append('task_key=?'); values.append(task_key)
    where=' WHERE '+' AND '.join(clauses) if clauses else ''
    rows=[json.loads(payload) for (payload,) in connection.execute('SELECT payload FROM events'+where+' ORDER BY id',values)]
    malformed=int(_meta(connection).get('malformed','0')); connection.close()
    return rows,malformed


def append_event(state:Path,row:dict):
    """Append one already-built row and refresh the index. The append-only primitive
    every recorder (record_metric, record_label) shares; never mutates a prior line."""
    p=state/'metrics.jsonl'
    with p.open('a') as f: f.write(json.dumps(row,sort_keys=True)+"\n")
    connection=_sync_metric_index(state); connection.close()


def record_metric(state:Path,event:str,**data):
    task=task_state(state)
    identity=load_json(task/'task.json',{})
    plan=load_json(task/'state/current-plan.json',{})
    row={"ts":time.time(),"event":event,"stack_version":VERSION,
        "task_key":task.name,"task_id":identity.get('id'),"profile":plan.get('profile'),
        "risk":(plan.get('risk') or {}).get('risk'),"task_type":plan.get('task_type'),**data}
    append_event(state,row)


def record_label(state:Path,*,task_key:str,gate:str,attempt:int|None,passed:bool|None,label:str,note:str):
    """A human's true/false-positive judgment on one recorded gate attempt.

    Deliberately not routed through record_metric(): that resolves the *active* task via
    task_state(), but a labeled attempt is very often on a task that has since been closed
    or is not the one currently active in this checkout.
    """
    row={"ts":time.time(),"event":"gate_label","stack_version":VERSION,
        "task_key":task_key,"gate":gate,"attempt":attempt,"passed":passed,"label":label,"note":note}
    append_event(state,row)


def record_task_label(state:Path,*,task_key:str,label:str,note:str):
    """A human's verdict on whether the stack's final readiness decision for one task
    was right -- 'correct' or 'incorrect'. Where a gate_label judges one gate attempt,
    this judges the certification itself, which is the claim `ai ready` actually makes.

    The false-PR_READY rate campaign_report() derives from these is the number that
    validates 'certifies whether a change is ready for human review'; nothing infers it
    from a PR being reverted or reworked later -- only this explicit label counts.
    Same rationale as record_label() for not going through record_metric().
    """
    row={"ts":time.time(),"event":"task_label","stack_version":VERSION,
        "task_key":task_key,"label":label,"note":note}
    append_event(state,row)


def gate_attempt_number(state:Path,task_key:str,gate:str)->int:
    """Count prior recorded attempts of this gate for this task, for a fresh 1-based number."""
    connection=_sync_metric_index(state)
    count=connection.execute('SELECT COUNT(*) FROM events WHERE event=? AND task_key=? AND gate_name=?',
                             ('gate',task_key,gate)).fetchone()[0]
    connection.close(); return 1+count


def cmd_metrics(args):
    state=repo_state(git_root())
    if getattr(args,'metrics_cmd',None)=='prune':
        require_human('Metrics retention pruning')
        cutoff=parse_window(args.older_than)
        rows,malformed=load_metric_rows(state)
        kept=[row for row in rows if row.get('ts',0)>=cutoff]
        removed=len(rows)-len(kept)
        print(f"Would remove {removed} of {len(rows)} recorded event(s) older than {args.older_than} "
              f"({malformed} already-malformed line(s) are dropped either way).")
        if not args.confirm: raise SystemExit('Re-run with --confirm to apply.')
        with (state/'metrics.jsonl').open('w') as f:
            for row in kept: f.write(json.dumps(row,sort_keys=True)+"\n")
        rebuild_metric_index(state)
        print(f'Pruned {removed} event(s); {len(kept)} remain.')
        return
    if getattr(args,'metrics_cmd',None)=='label':
        cmd_metrics_label(args,state)
        return
    if getattr(args,'campaign',False):
        rows,malformed=load_metric_rows(state)
        since=parse_window(args.since) if args.since else None
        budgets={name:context_caps(name)['usage_tokens'] for name in ('fast','standard','strict')}
        report=campaign_report(rows,since=since,usage_budgets=budgets)
        report['malformed_events_skipped']=malformed
        if args.json: print(json.dumps(report,indent=2)); return
        print_campaign_report(report)
        return
    rows,malformed=load_metric_rows(state)
    if not args.all_tasks:
        key=task_state(state).name
        rows=[row for row in rows if row.get('task_key')==key]
    if args.by:
        since=parse_window(args.since) if args.since else None
        report=usage_report(rows,group_by=args.by,since=since,top=args.top)
        if args.format=='csv':
            writer=csv.DictWriter(sys.stdout,fieldnames=['key','gate_attempts','gate_passes','gate_failures',
                'pipeline_runs','pipeline_successes','input_tokens','output_tokens','total_tokens',
                'reported_attempts','unreported_attempts','cost_usd','cost_reported_attempts'])
            writer.writeheader()
            for row in report: writer.writerow({k:row.get(k) for k in writer.fieldnames})
            if malformed: print(f'# {malformed} malformed event(s) skipped',file=sys.stderr)
            return
        if args.format=='json' or args.json: print(json.dumps(report,indent=2)); return
        maximum=max((row['total_tokens'] or 0 for row in report),default=0)
        for row in report:
            reported=f"({row['reported_attempts']}/{row['gate_attempts']} reported)"
            total=row['total_tokens']
            bar='  '+'#'*int(20*(total or 0)/maximum) if maximum else ''
            print(f"{str(row['key']):12} {total if total is not None else 'unreported':>8} tokens  {reported}{bar}")
        if args.budget:
            spent=sum(row['total_tokens'] or 0 for row in report)
            note=' (exceeds budget)' if spent>args.budget else ''
            since_label=f' since {args.since}' if args.since else ''
            print(f"Spent {spent} / {args.budget} tokens{since_label}{note}")
        return
    report=summarize(rows)
    report['scope']='repository' if args.all_tasks else 'task'
    report['malformed_events_skipped']=malformed
    if args.json: print(json.dumps(report,indent=2)); return
    print(f"Metrics ({report['scope']}): {report['gate_attempts']} gate attempts, "
          f"{report['gate_passes']} passed, {report['gate_failures']} failed")
    print(f"Gate time: {report['gate_duration_seconds']}s; repeated attempts: {report['repeated_gate_attempts']}")
    print(f"Pipelines ready: {report['pipeline_successes']}/{report['pipeline_runs']} "
          f"(budget exceeded: {report['pipeline_budget_exceeded']})")
    for name,value in report['usage'].items():
        total=value['reported_total']
        print(f"{name}: {total if total is not None else 'unreported'} ({value['reported_attempts']} reporting attempts)")
    for name,value in report['pipeline_usage'].items():
        total=value['reported_total']
        print(f"pipeline {name}: {total if total is not None else 'unreported'} ({value['reported_runs']} reporting runs)")


def print_campaign_report(report):
    print(f"Campaign: {report['tasks']} task(s), {report['reached_pr_ready']} reached PR_READY")
    if report.get('malformed_events_skipped'):
        print(f"  ({report['malformed_events_skipped']} malformed event(s) skipped)")
    print('\nBy task type:')
    for task_type,stats in sorted(report['by_task_type'].items(),key=lambda kv:str(kv[0])):
        median=stats['median_time_to_ready_seconds']; p90=stats['p90_time_to_ready_seconds']
        tokens=stats['median_tokens']
        print(f"  {str(task_type or 'unclassified'):14} n={stats['n']:<3} ready={stats['reached_pr_ready']}/{stats['n']} "
              f"median={median if median is not None else '-'}s p90={p90 if p90 is not None else '-'}s "
              f"retries(median/max)={stats['median_retries']}/{stats['max_retries']} "
              f"tokens(median)={tokens if tokens is not None else 'unreported'}")
    fpr=report.get('false_pr_ready') or {}
    if fpr.get('labeled_certifications'):
        rate=fpr['false_pr_ready_rate']
        print(f"\nFalse PR_READY rate (human-labeled): "
              f"{f'{rate:.0%}' if rate is not None else 'n/a'} "
              f"({fpr['incorrect']}/{fpr['labeled_certifications']} certified tasks judged not actually ready)")
    else:
        print('\nNo task certifications labeled yet. After a human reviews a PR_READY task:')
        print('  ai metrics label --task-key <key> --correct|--incorrect')
    if report['gate_labels']:
        print('\nGate false-positive rates (human-labeled, via `ai metrics label`):')
        for gate,stats in sorted(report['gate_labels'].items()):
            rate=stats['false_positive_rate']
            print(f"  {gate:12} labeled={stats['labeled']:<3} "
                  f"false_positive_rate={f'{rate:.0%}' if rate is not None else 'n/a'}")
    else:
        print('\nNo gate attempts labeled yet. Label some with:')
        print('  ai metrics label <gate> --task-key <key> --true-positive|--false-positive')
    print(f"\nfindings_raised: {report['findings_raised_caveat']}")
    if report['recommendations']:
        print('\nRecommendations:')
        for line in report['recommendations']: print(f'  - {line}')


def cmd_metrics_label(args,state):
    require_human('Metrics labeling')
    gate_verdict=args.true_positive or args.false_positive
    task_verdict=args.correct or args.incorrect
    if args.gate and task_verdict:
        raise SystemExit('--correct/--incorrect label the task certification, not a gate; drop the gate argument.')
    if not args.gate and gate_verdict:
        raise SystemExit('--true-positive/--false-positive label one gate attempt; name the gate, '
                         'or use --correct/--incorrect to label the task certification.')
    if not args.gate:
        return _label_task_certification(args,state)
    rows,_=load_metric_rows(state,event='gate',task_key=args.task_key)
    matches=[row for row in rows if row.get('gate')==args.gate]
    if args.attempt is not None: matches=[row for row in matches if row.get('attempt')==args.attempt]
    if not matches:
        where=f' attempt {args.attempt}' if args.attempt is not None else ''
        raise SystemExit(f'No recorded {args.gate!r} gate attempt{where} for task {args.task_key!r}. '
                         'Find the right task_key and attempt with `ai metrics --all-tasks --by task --json`.')
    target=matches[-1]
    if target.get('passed'):
        raise SystemExit('Only a FAILED gate attempt can be labeled true/false positive '
                         '(a PASS is required to carry no unresolved findings).')
    label='false_positive' if args.false_positive else 'true_positive'
    record_label(state,task_key=args.task_key,gate=args.gate,attempt=target.get('attempt'),
                passed=target.get('passed'),label=label,note=args.note or '')
    print(f"Labeled: task={args.task_key} gate={args.gate} attempt={target.get('attempt')} -> {label}")


def _label_task_certification(args,state):
    if not (args.correct or args.incorrect):
        raise SystemExit('Labeling a task certification needs --correct or --incorrect.')
    # The task must be one this metrics store actually knows about; labeling a typo'd
    # key would sit in the log forever contributing to nothing.
    rows,_=load_metric_rows(state,task_key=args.task_key)
    if not rows:
        raise SystemExit(f'No recorded events for task {args.task_key!r}. '
                         'Find the right task_key with `ai metrics --all-tasks --by task --json`.')
    label='correct' if args.correct else 'incorrect'
    record_task_label(state,task_key=args.task_key,label=label,note=args.note or '')
    print(f"Labeled: task={args.task_key} certification -> {label}")
