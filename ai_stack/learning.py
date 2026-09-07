from __future__ import annotations
import fnmatch, json, time
from pathlib import Path
from core import classify, collect_scope, context_caps, git_root, load_json, repo_state, require_human, required_gates, save_json, shasum, task_state
from metrics import load_metric_rows
from workflow import detect_patterns, outcome_stats


LESSON_TOP_K={'fast':0,'standard':3,'strict':5}


def load_lessons(state:Path)->list[dict]:
    return load_json(state/'lessons.json',[])


def save_lessons(state:Path,lessons:list[dict]):
    save_json(state/'lessons.json',lessons)


def find_lesson(lessons:list[dict],lesson_id:str)->dict:
    match=next((l for l in lessons if l['id']==lesson_id),None)
    if not match: raise SystemExit(f'Unknown lesson: {lesson_id}')
    return match


def derive_lessons(state:Path)->tuple[int,int]:
    """Create candidate lessons from failure patterns. Never mutates a confirmed/rejected/retired one."""
    report=rebuild_patterns(state)
    lessons=load_lessons(state)
    by_pattern={l.get('pattern_id'):l for l in lessons if l.get('pattern_id')}
    created=updated=0
    for pattern in report['patterns']:
        existing=by_pattern.get(pattern['id'])
        if existing is None:
            lessons.append({
                'id':f"les_{pattern['hash']}",'text':pattern['example'],
                'scope':pattern.get('scope_hint') or '**','gate':pattern['gate'],
                'origin':'pattern','pattern_id':pattern['id'],'status':'candidate',
                'observations':pattern['occurrences'],'distinct_tasks':pattern['distinct_tasks'],
                'first_seen':pattern['first_seen'],'last_seen':pattern['last_seen'],
                'evidence_refs':[],'created_at':time.time(),
                'confirmed_at':None,'confirmed_by':None,'injections':0,
            })
            created+=1
        elif existing.get('status')=='candidate':
            existing.update(text=pattern['example'],observations=pattern['occurrences'],
                             distinct_tasks=pattern['distinct_tasks'],last_seen=pattern['last_seen'],
                             scope=pattern.get('scope_hint') or existing.get('scope','**'))
            updated+=1
    save_lessons(state,lessons)
    return created,updated


def scope_matches(pattern:str|None,files:list[str])->bool:
    """Whether a stored scope glob (None/'**' = everywhere) touches any file in the change."""
    pattern=pattern or '**'
    return pattern=='**' or any(fnmatch.fnmatch(f,pattern) for f in files)


def select_lessons(state:Path,scope:dict,profile:str)->list[dict]:
    top_k=LESSON_TOP_K[profile]
    if top_k==0: return []
    files=scope.get('files',[])
    candidates=[l for l in load_lessons(state) if l.get('status')=='confirmed' and scope_matches(l.get('scope'),files)]
    candidates.sort(key=lambda l:(-l.get('observations',0),-(l.get('last_seen') or 0),l['id']))
    return candidates[:top_k]


def render_lessons(lessons:list[dict],max_chars:int)->str:
    if not lessons: return '(none)'
    text='\n'.join(f"- [{l.get('scope','**')}] {l['text']} "
                   f"(observed {l.get('observations',1)}x across {l.get('distinct_tasks',1)} task(s))" for l in lessons)
    if len(text)>max_chars: text=text[:max_chars].rstrip()+"\n[lesson context truncated by budget]"
    return text


def rebuild_patterns(state:Path)->dict:
    rows,_=load_metric_rows(state)
    report={'version':1,'generated_at':int(time.time()),'source_events':len(rows),
             'patterns':detect_patterns(rows)}
    save_json(state/'patterns.json',report)
    return report


def confidence_card(root:Path,state:Path,base:str,profile:str,*,scope:dict|None=None,risk:dict|None=None)->dict:
    """A forecast card: observed historical frequencies for this change's stratum, with n.

    Purely descriptive. Never consulted by classify(), required_gates() or cmd_ready() —
    confidence can only ever suggest more rigor (a higher profile), never relax gating.
    Pass an already-computed `scope`/`risk` (e.g. from a just-built plan) to skip a redundant
    collect_scope()/classify() recompute; `ai confidence` itself has none to pass, so it
    lets this derive them fresh.
    """
    if scope is None: scope=collect_scope(root,base)
    if risk is None: risk=classify(scope,profile)
    plan=load_json(task_state(state)/'state/current-plan.json',{})
    task_type=plan.get('task_type') if plan.get('scope',{}).get('base')==base and plan.get('profile')==profile else None
    rows,_=load_metric_rows(state)
    stats=outcome_stats(rows,profile=profile,risk=risk['risk'],task_type=task_type)
    patterns=detect_patterns(rows)
    matched_patterns=[p for p in patterns if p.get('scope_hint') and scope_matches(p['scope_hint'],scope['files'])]
    required=required_gates(root,{'scope':scope,'profile':profile,'risk':risk,'figma':plan.get('figma')})
    by_gate={s['gate']:s for s in stats}
    sufficient=[by_gate[g] for g in required if g in by_gate and by_gate[g]['sufficient']]
    weakest=min(sufficient,key=lambda s:s['pass_rate'],default=None)
    projected=sum(s.get('median_usage_tokens') or 0 for s in sufficient)
    budget=context_caps(profile)['usage_tokens']
    return {'risk':risk['risk'],'task_type':task_type,'profile':profile,'required_gates':required,
            'stats':stats,'weakest_gate':weakest,'matched_patterns':len(matched_patterns),
            'projected_usage_tokens':projected,'usage_budget':budget}


def cmd_failures(args):
    state=repo_state(git_root()); report=rebuild_patterns(state)
    if args.failures_cmd=='rebuild':
        print(f"Rebuilt {len(report['patterns'])} pattern(s) from {report['source_events']} recorded events.")
        return
    patterns=report['patterns']
    if args.failures_cmd=='show':
        match=next((p for p in patterns if p['id']==args.pattern_id),None)
        if not match: raise SystemExit(f'Unknown pattern: {args.pattern_id}')
        print(json.dumps(match,indent=2)); return
    if args.failures_cmd=='export':
        anonymized=[{'gate':p['gate'],'hash':p['hash'],'occurrences':p['occurrences']} for p in patterns]
        print(json.dumps(anonymized,indent=2)); return
    # default: list
    if args.gate: patterns=[p for p in patterns if p['gate']==args.gate]
    patterns=[p for p in patterns if p['occurrences']>=args.min]
    if args.json: print(json.dumps({**report,'patterns':patterns},indent=2)); return
    if not patterns: print('No recurring failure patterns recorded yet.'); return
    for p in patterns:
        hint=f" ({p['scope_hint']})" if p.get('scope_hint') else ''
        print(f"{p['id']}  {p['gate']:10} x{p['occurrences']:<3} across {p['distinct_tasks']} task(s){hint}")
        print(f"    {p['example']}")


def cmd_lessons(args):
    state=repo_state(git_root())
    if args.lessons_cmd in ('confirm','reject','retire','promote','add'): require_human('Lesson curation')
    lessons=load_lessons(state)
    if args.lessons_cmd=='derive':
        created,updated=derive_lessons(state)
        print(f'Derived {created} new candidate lesson(s), refreshed {updated} existing candidate(s).')
        return
    if args.lessons_cmd=='add':
        now=time.time()
        entry={'id':f"les_user_{shasum(args.text+str(now))[:12]}",'text':args.text,
               'scope':args.scope or '**','gate':args.gate,'origin':'user','pattern_id':None,
               'status':'confirmed','observations':1,'distinct_tasks':1,
               'first_seen':now,'last_seen':now,'evidence_refs':[],
               'created_at':now,'confirmed_at':now,'confirmed_by':'user','injections':0}
        lessons.append(entry); save_lessons(state,lessons); print('Lesson added:',entry['id']); return
    if args.lessons_cmd in ('confirm','reject','retire'):
        match=find_lesson(lessons,args.lesson_id)
        if args.lessons_cmd=='confirm': match['status']='confirmed'; match['confirmed_at']=time.time(); match['confirmed_by']='user'
        elif args.lessons_cmd=='reject': match['status']='rejected'
        else: match['status']='retired'
        save_lessons(state,lessons); print(f"{args.lesson_id}: {match['status']}"); return
    if args.lessons_cmd=='promote':
        match=find_lesson(lessons,args.lesson_id)
        if match.get('status')!='confirmed': raise SystemExit('Only a confirmed lesson can be promoted.')
        rules=load_json(state/'rules.json',[])
        rules.append({'rule':match['text'],'scope':match.get('scope','**'),'source':'lesson',
                      'confidence':1.0,'created_at':int(time.time())})
        save_json(state/'rules.json',rules)
        match['status']='retired'; save_lessons(state,lessons)
        print('Promoted to rules.json; lesson retired:',args.lesson_id); return
    if args.lessons_cmd=='prune':
        threshold=time.time()-args.unseen_days*86400
        before=len(lessons)
        lessons=[l for l in lessons if not (l.get('status')=='candidate' and (l.get('last_seen') or 0)<threshold)]
        save_lessons(state,lessons); print(f'Pruned {before-len(lessons)} stale candidate lesson(s).'); return
    # default: list
    statuses=(args.status,) if args.status else ('candidate','confirmed')
    shown=[l for l in lessons if l.get('status') in statuses]
    if args.json: print(json.dumps(shown,indent=2)); return
    if not shown: print('No lessons recorded yet.'); return
    for l in shown:
        print(f"{l['id']}  [{l['status']}] {l.get('scope','**')}  {l['text']}")


def cmd_confidence(args):
    root=git_root(); state=repo_state(root)
    card=confidence_card(root,state,args.base,args.profile)
    if args.json: print(json.dumps(card,indent=2)); return
    print(f"Confidence card ({card['profile']}, risk {card['risk']}"
          +(f", {card['task_type']}" if card['task_type'] else '')+')')
    print('Required gates:',', '.join(card['required_gates']))
    print(f"Matched failure patterns for this change's scope: {card['matched_patterns']}")
    if card['usage_budget']:
        note=' (exceeds budget)' if card['projected_usage_tokens']>card['usage_budget'] else ''
        print(f"Projected usage: {card['projected_usage_tokens']} / {card['usage_budget']} tokens{note}")
    print()
    for s in card['stats']:
        if not s['sufficient']:
            print(f"  {s['gate']:10} LOW_EVIDENCE (n={s['n']})"); continue
        print(f"  {s['gate']:10} pass_rate={s['pass_rate']} first_attempt={s['first_attempt_pass_rate']} "
              f"median_attempts={s['median_attempts']} (n={s['n']})")
