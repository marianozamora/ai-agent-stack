from __future__ import annotations
import hashlib, json, re, sys, textwrap, time, uuid
from pathlib import Path
from core import classify, classify_task, collect_scope, context_caps, enforce_budget, git_root, load_json, profile_repo, repo_state, safe_head, save_json, shasum, task_state
from crg import crg_cmd, crg_env, crg_impact, elevate_risk, parse_crg_risk
from learning import confidence_card, render_lessons, select_lessons
from metrics import record_metric
from prompts import assign_prompt_variants
from providers import builder as get_builder
from skills import load_skill_context, select_skills
from tools import ctx7_cmd
from workflow import analyze_ticket_text


def semantic_fingerprint(root:Path)->dict:
    files=['package.json','tsconfig.json','pyproject.toml','go.mod','Cargo.toml','eslint.config.js','eslint.config.mjs','eslint.config.ts','biome.json','biome.jsonc']
    out={}
    for rel in files:
        p=root/rel
        if p.exists() and p.is_file():
            try: out[rel]=hashlib.sha256(p.read_bytes()).hexdigest()[:16]
            except Exception: pass
    return out


def task_cache_key(root:Path,task:str,profile:str,base:str,skills:list[str])->str:
    payload=json.dumps({"task":task,"profile":profile,"base":base,"skills":skills,"fingerprint":semantic_fingerprint(root)},sort_keys=True)
    return shasum(payload)[:24]


def rules_text(state:Path)->str:
    rules=load_json(state/'rules.json',[])
    if not rules:return "(none)"
    return '\n'.join(f"- [{r.get('scope','**')}] {r['rule']}" for r in rules)


def ensure_contract(state:Path,task:str,figma:str|None=None):
    p=task_state(state)/'contracts'/'current-pr.yml'
    if not p.exists():
        p.write_text(textwrap.dedent(f'''\
        objective: {json.dumps(task)}
        acceptance: []
        must_not_change: []
        risk_notes: []
        design:
          enabled: {str(bool(figma)).lower()}
        '''))
    elif task:
        s=p.read_text(); s=re.sub(r'^objective:.*$',f'objective: {json.dumps(task)}',s,count=1,flags=re.M); p.write_text(s)
    if figma:
        d=task_state(state)/'contracts'/'current-design.yml'
        d.write_text(textwrap.dedent(f'''\
        source: figma
        url: {json.dumps(figma)}
        node: ""
        components: []
        variants: []
        tokens: []
        states: []
        responsive: []
        reuse_required: []
        material_fidelity_checks: []
        '''))


def populate_acceptance_if_empty(contract_path:Path,items:list[str])->bool:
    """Fill an empty acceptance list from detected ticket content; never touch a human's own list.

    Uses the exact regex the contract gate's own emptiness check uses (cmd_validate),
    so "is this list empty" is one predicate shared by the writer and the gate — this
    can never overwrite a human-authored acceptance list, only fill a blank one.
    """
    if not items: return False
    text=contract_path.read_text()
    if not re.search(r'^acceptance:\s*\[\s*\]\s*$',text,re.M): return False
    contract_path.write_text(re.sub(r'^acceptance:\s*\[\s*\]\s*$','acceptance: '+json.dumps(items),text,flags=re.M))
    return True


def build_prompt(root:Path,state:Path,task:str,profile:str,base:str,figma:str|None,explicit_skills:list[str]|None=None)->str:
    scope=collect_scope(root,base); risk=classify(scope,profile); caps=context_caps(profile)
    selected_skills=select_skills(state,task,profile,figma,explicit_skills)
    skill_context=load_skill_context(selected_skills, max(2000,caps['context_chars']//3))
    selected_lessons=select_lessons(state,scope,profile)
    lessons_block=render_lessons(selected_lessons,max(1,caps['context_chars']//10))
    crg_text=''
    if profile!='fast' and crg_cmd():
        crg_text=crg_impact(root,state,base,refresh=False,build_if_missing=False)
        risk=elevate_risk(risk,parse_crg_risk(crg_text))
    graph=str(state/'graphify'/'graph.json')
    prompt=f'''# AI Agent Stack orchestration

Task: {task}
Task type: {classify_task(task,figma)}
Profile: {profile}
Base: {base}
Risk: {risk['risk']} ({risk['reason']})
Security boundary: {risk['security']}

External state (NEVER commit these files): {state}
PR Contract: {task_state(state)/'contracts/current-pr.yml'}
Design Contract: {(task_state(state)/'contracts/current-design.yml') if figma else 'off'}
Graphify graph: {graph if Path(graph).exists() else 'not built'}
Code Review Graph: {'ready' if crg_text else ('installed/not-ready' if crg_cmd() else 'missing')}
Deep repository profile (AI-generated interpretation; verify before relying on it): {(state/'project-deep-profile.json') if (state/'project-deep-profile.json').exists() else 'not generated — run `ai profile --deep`'}
Ticket content snapshot (pasted, regex-analyzed, advisory only): {(task_state(state)/'state/ticket.json') if (task_state(state)/'state/ticket.json').exists() else 'none — pass --ticket-file to ai plan/ai run'}

Repository rules:
{rules_text(state)}

Observed failure history (advisory prior observations, never evidence for a PASS):
{lessons_block}

Active skills (lazy-loaded; max {caps['skills']}): {', '.join(selected_skills) if selected_skills else 'none'}

Skill instructions:
{skill_context}

Token Efficiency Policy:
- Classify first; do not explore broadly before routing.
- Progressive disclosure: metadata -> compact graph evidence -> snippets -> raw files.
- Prefer one tool per question; do not call Graphify, CRG, CodeGraph and Context7 for the same need.
- Reuse external repo state/cache; never rediscover stable project facts in every task.
- Tests/static evidence arbitrate disagreements; do not create model-to-model debate loops.
- PASS outputs must be minimal. Findings must be capped and actionable.
- Escalate model/context only on concrete failed evidence, security/data risk, or unresolved high-risk ambiguity.

Context budget:
- raw files <= {caps['raw_files']} unless evidence requires escalation
- review files <= {caps['review_files']}
- agent calls <= {caps['agent_calls']}
- review rounds <= {caps['reviews']}
- Context7 docs queries <= {caps['docs_queries']}
- Graphify structural queries <= {caps['graph_queries']}
- active skills <= {caps['skills']}
- findings <= {caps['findings']}
- retries per failing approach <= {caps['retries']}
- injected context target <= {caps['context_chars']} characters before evidence-driven escalation

Context order:
1. PR/design contract
2. Code Review Graph for diff impact, blast radius, affected flows, tests and minimal review context
3. Graphify for macro architecture/routes/communities when CRG cannot answer the architecture question
4. CodeGraph for exact symbol navigation when materially better than CRG
5. Context7 ONLY for external library/framework/API documentation; prefer exact installed version and cached library IDs
6. RTK for git/tests/lint/search output
7. raw source reads only when needed to prove/implement something

External docs policy:
- Never rely on memory for version-sensitive library APIs when Context7 can verify them.
- Query only the library/topic needed for the current implementation.
- Do not dump broad documentation into context.
- Repository code and tests remain the source of truth for project-specific behavior.

Correctness pipeline:
Builder -> deterministic checks -> regression check -> Codex adversarial review only when risk/profile warrants -> confirmed fixes -> Cleanup -> checks -> provenance gate -> Ponytail -> PR summary.
Ponytail judges project-specific quality; it does not impose SOLID or FP contrary to repo conventions.
Cleanup removes AI provenance/references and unnecessary comments without changing behavior.
If a gate passes, return only its compact PASS contract unless more detail is required by a failure.

Figma: {'ACTIVE: ingest via Figma MCP into the compact Design Contract, then discard raw design context.' if figma else 'off'}

Zero-footprint invariant: DO NOT create or modify AI framework/config/state files in the working repository. Do not modify .gitignore for this framework.

Record final gates with `ai gate NAME -- COMMAND ...`: checks, regression, contract, cleanup, provenance, ponytail, summary; review for standard/strict or elevated risk; security for security boundaries; design for Figma. Except checks/regression, validators must finish with single-line JSON containing status PASS and a nonempty evidence list. Run cleanup before recording final checks. Only `ai ready` may certify PR_READY from fresh recorded evidence. Return NEEDS_HUMAN or FAILED when evidence is missing.
If reusable validators are configured (`ai validators show`), use `ai pipeline --dry-run` to inspect the required sequence and `ai pipeline --resume` to execute it using fresh evidence where available. Inspect task outcomes with `ai metrics`.
'''
    enforce_budget(prompt,caps['context_chars'],'orchestration context')
    ensure_contract(state,task,figma)
    (task_state(state)/'state'/'current-run.md').write_text(prompt)
    snapshot=[{'id':l['id'],'text':l['text'],'scope':l.get('scope','**')} for l in selected_lessons]
    save_json(task_state(state)/'state'/'lessons.json',
              {'digest':shasum(json.dumps(snapshot,sort_keys=True))[:16],'lessons':snapshot})
    cache_key=task_cache_key(root,task,profile,base,selected_skills)
    save_json(task_state(state)/'state'/'prompt-assignment.json',assign_prompt_variants(state,cache_key))
    save_json(task_state(state)/'state'/'current-plan.json',{"task":task,"task_type":classify_task(task,figma),"profile":profile,"scope":scope,"risk":risk,"caps":caps,"figma":figma,"skills":selected_skills,"cache_key":cache_key,"fingerprint":semantic_fingerprint(root),"crg":{"available":bool(crg_cmd()),"risk":parse_crg_risk(crg_text),"impact_cached":bool(crg_text)}})
    record_metric(state,'plan',profile=profile,task_type=classify_task(task,figma),risk=risk['risk'],skills=selected_skills,file_count=scope['file_count'],changed_lines=scope['changed_lines'])
    return prompt


def cmd_planrun(args,launch:bool):
    root=git_root(); state=repo_state(root)
    if not (state/'project-profile.json').exists(): profile_repo(root,state)
    figma=args.figma
    if not args.no_figma and not figma:
        m=re.search(r'https?://\S*figma\.com/\S+',args.task or '')
        figma=m.group(0) if m else None
    if args.no_figma: figma=None
    ticket_analysis=None
    if getattr(args,'ticket_file',None):
        ticket_analysis=analyze_ticket_text(Path(args.ticket_file).read_text())
        if not figma and ticket_analysis['figma_url']: figma=ticket_analysis['figma_url']
    prompt=build_prompt(root,state,args.task or 'Implement the current working task.',args.profile,args.base,figma,getattr(args,'skill',None))
    plan=load_json(task_state(state)/'state'/'current-plan.json',{})
    if ticket_analysis is not None:
        task=task_state(state)
        populate_acceptance_if_empty(task/'contracts/current-pr.yml',ticket_analysis['acceptance_items'])
        save_json(task/'state/ticket.json',{'version':1,'fetched_at':time.time(),'source':'pasted',
            'length':ticket_analysis['length'],'figma_url':ticket_analysis['figma_url'],
            'acceptance_items':ticket_analysis['acceptance_items'],'blockers_mentioned':ticket_analysis['blockers_mentioned'],
            'has_acceptance':ticket_analysis['has_acceptance'],
            'caveat':'Deterministic regex read of pasted content; not a verified analysis of ticket sufficiency.'})
    print('AI plan')
    print('  repo state: ',state)
    print('  profile:    ',args.profile)
    print('  risk:       ',plan.get('risk',{}).get('risk'))
    print('  files:      ',plan.get('scope',{}).get('file_count'))
    print('  figma:      ',figma or 'off')
    print('  task type:  ',plan.get('task_type'))
    print('  skills:     ', ', '.join(plan.get('skills',[])) or 'none')
    print('  cache key:  ',plan.get('cache_key'))
    print('  Context7:   ','ready' if ctx7_cmd() else 'missing')
    print('  Graphify:   ','ready' if (state/'graphify'/'graph.json').exists() else 'not built')
    print('  CRG:        ', 'ready' if plan.get('crg',{}).get('impact_cached') else ('installed/not-ready' if crg_cmd() else 'missing'))
    print('  prompt:     ',task_state(state)/'state'/'current-run.md')
    card=confidence_card(root,state,args.base,args.profile,scope=plan.get('scope'),risk=plan.get('risk'))
    weakest=card['weakest_gate']
    print('  confidence: ','no sufficient history yet' if not weakest else
          f"weakest gate {weakest['gate']} pass_rate={weakest['pass_rate']} (n={weakest['n']})")
    print('  patterns:   ',f"{card['matched_patterns']} matched for this change's scope")
    if card['usage_budget']:
        note=' (exceeds budget)' if card['projected_usage_tokens']>card['usage_budget'] else ''
        print('  projected:  ',f"{card['projected_usage_tokens']} / {card['usage_budget']} tokens{note}")
    if ticket_analysis is not None:
        print('  ticket:     ',f"{len(ticket_analysis['acceptance_items'])} acceptance item(s) detected"
              +(', figma link found' if ticket_analysis['figma_url'] else ''))
        if ticket_analysis['blockers_mentioned']:
            print('  blockers:   ','; '.join(ticket_analysis['blockers_mentioned'])+' (advisory; not verified)')
    if launch:
        active_builder=get_builder(state)
        env=crg_env(state)
        env['AI_TASK_ID']=load_json(task_state(state)/'task.json',{})['id']
        active_builder.launch(prompt,root,env)  # replaces this process; never returns on success


def cmd_ticket(args):
    if args.file: text=Path(args.file).read_text()
    elif args.text is not None: text=args.text
    else: text=sys.stdin.read()
    if not text.strip(): raise SystemExit('No ticket content provided (use --file, --text, or pipe via stdin).')
    state=repo_state(git_root()); task=task_state(state)
    analysis=analyze_ticket_text(text)
    snapshot={'version':1,'fetched_at':time.time(),'source':'pasted','length':analysis['length'],
              'figma_url':analysis['figma_url'],'acceptance_items':analysis['acceptance_items'],
              'blockers_mentioned':analysis['blockers_mentioned'],'has_acceptance':analysis['has_acceptance'],
              'caveat':'Deterministic regex read of pasted content; not a verified analysis of ticket sufficiency.'}
    save_json(task/'state/ticket.json',snapshot)
    if args.json: print(json.dumps(snapshot,indent=2)); return
    print(f"Ticket content: {analysis['length']} chars")
    print(f"Acceptance criteria detected: {len(analysis['acceptance_items'])}")
    for item in analysis['acceptance_items']: print(f"  - {item}")
    print(f"Figma link: {analysis['figma_url'] or 'none'}")
    if analysis['blockers_mentioned']:
        print('Blockers/dependencies mentioned (advisory; not verified against any live source):')
        for b in analysis['blockers_mentioned']: print(f"  - {b}")


def cmd_handoff(args):
    root=git_root(); state=repo_state(root); caps=context_caps(args.profile)
    plan=load_json(task_state(state)/'state'/'current-plan.json',{})
    scope=collect_scope(root,args.base)
    payload={
      'task': args.task or plan.get('task','Current task'),
      'state': args.state or 'in_progress',
      'profile': args.profile,
      'risk': plan.get('risk',{}).get('risk'),
      'skills': plan.get('skills',[]),
      'changed_files': scope['files'][:caps['review_files']],
      'evidence': args.evidence or [],
      'next_action': args.next or '',
      'head': safe_head(root),
      'created_at': int(time.time())
    }
    text=json.dumps(payload,indent=2)
    enforce_budget(text+'\n',caps['handoff_chars'],'handoff')
    p=task_state(state)/'handoffs'/f"handoff-{uuid.uuid4().hex}.json"; p.write_text(text+'\n')
    record_metric(state,'handoff',profile=args.profile,chars=len(text),files=len(payload['changed_files']))
    print('Handoff:',p)
    print(text)
