from __future__ import annotations
import csv, json, sys, time
from pathlib import Path
from core import VERSION, git_root, load_json, repo_state, require_human, task_state
from workflow import parse_window, summarize, usage_report


def load_metric_rows(state:Path)->tuple[list[dict],int]:
    p=state/'metrics.jsonl'; rows=[]; malformed=0
    for line in p.read_text().splitlines() if p.exists() else []:
        try:
            row=json.loads(line)
            if not isinstance(row,dict): raise ValueError()
            rows.append(row)
        except ValueError: malformed+=1
    return rows,malformed


def record_metric(state:Path,event:str,**data):
    task=task_state(state)
    identity=load_json(task/'task.json',{})
    plan=load_json(task/'state/current-plan.json',{})
    p=state/'metrics.jsonl'; row={"ts":time.time(),"event":event,"stack_version":VERSION,
        "task_key":task.name,"task_id":identity.get('id'),"profile":plan.get('profile'),
        "risk":(plan.get('risk') or {}).get('risk'),"task_type":plan.get('task_type'),**data}
    with p.open('a') as f: f.write(json.dumps(row,sort_keys=True)+"\n")


def gate_attempt_number(state:Path,task_key:str,gate:str)->int:
    """Count prior recorded attempts of this gate for this task, for a fresh 1-based number."""
    rows,_=load_metric_rows(state)
    return 1+sum(1 for row in rows if row.get('event')=='gate' and row.get('task_key')==task_key and row.get('gate')==gate)


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
        print(f'Pruned {removed} event(s); {len(kept)} remain.')
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
