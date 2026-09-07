from __future__ import annotations
import json, time
from pathlib import Path
from core import STACK_ROOT, VERSION, git_root, load_json, repo_state, require_human, save_json, shasum
from metrics import load_metric_rows
from workflow import variant_stats
from validators import INSTRUCTIONS


def prompt_slot(name:str)->str:
    return f'validator.{name}'


def variant_text(name:str,slot:str,variant:str)->str:
    if variant=='a': return INSTRUCTIONS[name]
    path=STACK_ROOT/'templates/prompts'/slot/f'{variant}.md'
    if not path.is_file(): raise SystemExit(f'Unknown prompt variant: {slot}/{variant}')
    return path.read_text()


def variant_entry(name:str,slot:str,variant:str)->dict:
    return {'variant':variant,'sha':shasum(variant_text(name,slot,variant))[:12]}


def available_variants(slot:str)->list[str]:
    directory=STACK_ROOT/'templates/prompts'/slot
    variants=['a']
    if directory.is_dir(): variants+=sorted(p.stem for p in directory.glob('*.md') if p.stem!='a')
    return variants


def prompt_overrides(state:Path)->dict:
    return load_json(state/'prompt-overrides.json',{'version':1,'slots':{}})


def load_prompt_history(state:Path)->list[dict]:
    rows=[]
    for line in (state/'prompt-history.jsonl').read_text().splitlines() if (state/'prompt-history.jsonl').exists() else []:
        try:
            row=json.loads(line)
            if isinstance(row,dict): rows.append(row)
        except ValueError: continue
    return rows


def append_prompt_history(state:Path,entry:dict):
    """An append-only audit log of promote/reset/rollback decisions and the evidence behind them.

    Deliberately excluded from evidence_fingerprint(): it is a record of a decision,
    not an input any validator is told, so appending to it must never invalidate
    in-flight task evidence (the same reasoning patterns.json is kept out for).
    """
    with (state/'prompt-history.jsonl').open('a') as f:
        f.write(json.dumps(entry,sort_keys=True)+"\n")


def prompt_experiment(state:Path)->dict|None:
    return load_json(state/'prompt-experiments.json',{}).get('active')


def assign_prompt_variants(state:Path,cache_key:str)->dict:
    """Deterministic per-task variant assignment, snapshotted once at plan time.

    A promoted override always wins for its slot; otherwise the single active
    experiment (at most one per repo) is assigned by hash(cache_key+slot), so a
    given task keeps the same variant across --resume without a random draw.
    """
    assignment={}
    for slot,info in prompt_overrides(state).get('slots',{}).items():
        assignment[slot]=variant_entry(slot.split('.',1)[1],slot,info['variant'])
    experiment=prompt_experiment(state)
    if experiment and experiment['slot'] not in assignment:
        slot=experiment['slot']; name=slot.split('.',1)[1]; variants=experiment['variants']
        variant=variants[int(shasum(cache_key+slot),16)%len(variants)]
        assignment[slot]=variant_entry(name,slot,variant)
    return assignment


def cmd_prompt(args):
    state=repo_state(git_root())
    if args.prompt_cmd=='list':
        overrides=prompt_overrides(state).get('slots',{}); experiment=prompt_experiment(state)
        for name in INSTRUCTIONS:
            slot=prompt_slot(name); variants=available_variants(slot); notes=[]
            if slot in overrides: notes.append(f"promoted={overrides[slot]['variant']}")
            if experiment and experiment['slot']==slot: notes.append('experiment active')
            print(f"{slot:24} variants={','.join(variants)}"+(f"  ({'; '.join(notes)})" if notes else ''))
        return
    if args.prompt_cmd=='show':
        print(variant_text(args.name,prompt_slot(args.name),args.variant)); return
    if args.prompt_cmd=='experiment':
        slot=prompt_slot(args.name) if args.experiment_cmd=='start' else None
        if args.experiment_cmd=='start':
            if prompt_experiment(state): raise SystemExit('An experiment is already active; stop it first.')
            variants=args.variants.split(',')
            if len(variants)<2: raise SystemExit('An experiment needs at least 2 variants.')
            available=available_variants(slot); unknown=[v for v in variants if v not in available]
            if unknown: raise SystemExit(f"Unknown variant(s) for {slot}: {', '.join(unknown)}. Available: {', '.join(available)}")
            save_json(state/'prompt-experiments.json',{'version':1,'active':{
                'slot':slot,'variants':variants,'started_at':int(time.time()),'min_samples_per_variant':args.min_samples}})
            print(f"Experiment started on {slot}: {', '.join(variants)} (min {args.min_samples} samples/variant).")
        elif args.experiment_cmd=='status':
            experiment=prompt_experiment(state)
            print(json.dumps(experiment,indent=2) if experiment else 'No active experiment.')
        else:
            save_json(state/'prompt-experiments.json',{'version':1,'active':None}); print('Experiment stopped.')
        return
    if args.prompt_cmd=='report':
        experiment=prompt_experiment(state)
        if not experiment: raise SystemExit('No active experiment; nothing to report.')
        rows,_=load_metric_rows(state)
        stats=variant_stats(rows,experiment['slot'],since=experiment['started_at'])
        if args.json: print(json.dumps({'slot':experiment['slot'],
            'min_samples_per_variant':experiment['min_samples_per_variant'],'stats':stats},indent=2)); return
        print(f"Prompt experiment: {experiment['slot']} (min {experiment['min_samples_per_variant']} samples/variant, "
              f"since this experiment started)")
        if len({s['sha'] for s in stats})>len({s['variant'] for s in stats}):
            print("Note: this slot's text changed mid-experiment; rows below are grouped by (variant, sha), not directly comparable across a change.")
        for s in stats:
            print(f"  {s['variant']} ({s['sha']}) n={s['n']} pass_rate={s['pass_rate']} "
                  f"first_attempt={s['first_attempt_pass_rate']} median_tokens={s['median_usage_tokens']}")
            print(f"    by_task_type={s['by_task_type']}  by_risk={s['by_risk']}  by_stack_version={s['by_stack_version']}")
        return
    if args.prompt_cmd=='promote':
        require_human('Prompt promotion')
        slot=prompt_slot(args.name)
        if args.variant not in available_variants(slot): raise SystemExit(f'Unknown variant: {slot}/{args.variant}')
        experiment=prompt_experiment(state)
        evidence=[]
        # Always show the comparison before applying, --confirm or not: promotion is a
        # decision a human makes from evidence, never a formula, so the evidence must be seen.
        if experiment and experiment['slot']==slot:
            rows,_=load_metric_rows(state)
            stats=variant_stats(rows,slot,since=experiment['started_at'])
            evidence=stats
            by_key={(s['variant'],s['sha']):s for s in stats}
            min_samples=experiment['min_samples_per_variant']
            print(f"Prompt promotion candidate: {slot} -> {args.variant} "
                  f"(evidence since this experiment started, min {min_samples} samples/variant)")
            short=[]
            for v in experiment['variants']:
                current_sha=variant_entry(args.name,slot,v)['sha']
                s=by_key.get((v,current_sha))
                n=s['n'] if s else 0
                stale=[k for k in by_key if k[0]==v and k[1]!=current_sha]
                note=' (stale text edited since collected; not counted)' if stale and not s else ''
                print(f"  {v} n={n}{note}"+('' if not s else
                      f" pass_rate={s['pass_rate']} first_attempt={s['first_attempt_pass_rate']} median_tokens={s['median_usage_tokens']}"))
                if n<min_samples: short.append(v)
            print('These are observed frequencies over small samples, not a controlled experiment.')
            if short: raise SystemExit(f"Not enough samples yet against the current text for {', '.join(short)} "
                                        f"(need >= {min_samples} each). Run `ai prompt report`.")
        else:
            print(f"Prompt promotion candidate: {slot} -> {args.variant} (no active experiment for this slot; no evidence to show).")
        if not args.confirm: raise SystemExit('Re-run with --confirm to apply.')
        data=prompt_overrides(state)
        previous=data['slots'].get(slot)
        data['slots'][slot]={**variant_entry(args.name,slot,args.variant),
                              'promoted_at':int(time.time()),'promoted_by':'user'}
        save_json(state/'prompt-overrides.json',data)
        started_at=experiment['started_at'] if experiment and experiment['slot']==slot else None
        if experiment and experiment['slot']==slot: save_json(state/'prompt-experiments.json',{'version':1,'active':None})
        append_prompt_history(state,{'ts':time.time(),'stack_version':VERSION,'action':'promote','slot':slot,
            'variant':args.variant,'sha':data['slots'][slot]['sha'],'previous':previous,'actor':'user',
            'experiment_started_at':started_at,'evidence':evidence})
        print(f'Promoted {slot} -> {args.variant}.'); return
    if args.prompt_cmd=='reset':
        require_human('Prompt reset')
        slot=prompt_slot(args.name)
        data=prompt_overrides(state); previous=data['slots'].pop(slot,None)
        save_json(state/'prompt-overrides.json',data)
        append_prompt_history(state,{'ts':time.time(),'stack_version':VERSION,'action':'reset','slot':slot,
            'variant':None,'sha':None,'previous':previous,'actor':'user',
            'experiment_started_at':None,'evidence':[]})
        print('Reset.'); return
    if args.prompt_cmd=='rollback':
        require_human('Prompt rollback')
        slot=prompt_slot(args.name)
        history=[h for h in load_prompt_history(state) if h.get('slot')==slot and h.get('action') in ('promote','rollback')]
        if not history: raise SystemExit(f'No promotion history for {slot}.')
        previous=history[-1].get('previous')
        if not previous: raise SystemExit(f'{slot} has no earlier promoted variant to roll back to.')
        print(f"Rollback candidate: {slot} -> {previous['variant']} (undoing {history[-1]['action']} to {history[-1]['variant']}).")
        if not args.confirm: raise SystemExit('Re-run with --confirm to apply.')
        data=prompt_overrides(state); current=data['slots'].get(slot)
        data['slots'][slot]={**previous,'promoted_at':int(time.time()),'promoted_by':'user'}
        save_json(state/'prompt-overrides.json',data)
        append_prompt_history(state,{'ts':time.time(),'stack_version':VERSION,'action':'rollback','slot':slot,
            'variant':previous['variant'],'sha':previous['sha'],'previous':current,'actor':'user',
            'experiment_started_at':None,'evidence':[]})
        print(f"Rolled back {slot} -> {previous['variant']}."); return
    if args.prompt_cmd=='history':
        history=load_prompt_history(state)
        if args.slot: history=[h for h in history if h.get('slot')==prompt_slot(args.slot)]
        if args.json: print(json.dumps(history,indent=2)); return
        if not history: print('No prompt promotion history yet.'); return
        for h in history:
            when=time.strftime('%Y-%m-%d %H:%M',time.gmtime(h['ts']))
            prev=h.get('previous')
            was=f" (was {prev['variant']})" if prev else ' (was unset)'
            print(f"{when}  {h['action']:8} {h['slot']} -> {h.get('variant')}{was}")
        return
