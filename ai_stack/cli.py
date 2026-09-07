#!/usr/bin/env python3
from __future__ import annotations
import tempfile, uuid
import argparse, hashlib, json, os, re, shutil, subprocess, sys, textwrap, time
from pathlib import Path
from typing import Any
from workflow import ORDER, validate_config, usage_from_verdict, summarize, execute

VERSION = "0.7.1"
TASK_ID = None
STACK_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = Path(os.environ.get("XDG_CONFIG_HOME", Path.home()/".config")) / "ai-agent-stack"

AI_PATTERNS = [
    ".ai-review/", "ai-agent-stack/", "CLAUDE.local.ai.md", "AGENTS.local.ai.md",
    "graphify-out/", ".context7/", ".code-review-graph/"
]


def run(cmd:list[str], cwd:Path|None=None, check=True, capture=True, env=None)->str:
    p=subprocess.run(cmd,cwd=cwd,text=True,stdout=subprocess.PIPE if capture else None,
                     stderr=subprocess.PIPE if capture else None,check=False,env=env)
    if check and p.returncode:
        msg=(p.stderr or p.stdout or "command failed").strip()
        raise RuntimeError(msg)
    return (p.stdout or "").strip()


def git_root()->Path:
    try: return Path(run(["git","rev-parse","--show-toplevel"]))
    except Exception: raise SystemExit("Not inside a git repository.")


def remote_id(root:Path)->tuple[str,str]:
    try: remote=run(["git","remote","get-url","origin"],cwd=root)
    except Exception: remote=str(root.resolve())
    normalized=re.sub(r"\.git$","",remote.strip())
    rid=hashlib.sha256(normalized.encode()).hexdigest()[:16]
    return rid, normalized


def repo_state(root:Path|None=None, create=True)->Path:
    root=root or git_root(); rid,remote=remote_id(root)
    d=CONFIG_ROOT/"repos"/rid
    if create:
        for sub in ["contracts","state","graphify","docs-cache","code-review-graph","review","task-cache","handoffs","skill-state"]: (d/sub).mkdir(parents=True,exist_ok=True)
        meta={"version":1,"repo_id":rid,"remote":remote,"last_root":str(root),"updated_at":int(time.time())}
        (d/"repo.json").write_text(json.dumps(meta,indent=2)+"\n")
        if not (d/"rules.json").exists(): (d/"rules.json").write_text("[]\n")
        if not (d/"observations.json").exists(): (d/"observations.json").write_text("[]\n")
        if not (d/"context7-libraries.json").exists(): (d/"context7-libraries.json").write_text("{}\n")
        if not (d/"skill-overrides.json").exists(): (d/"skill-overrides.json").write_text("{}\n")
    return d


def shasum(s:str)->str: return hashlib.sha256(s.encode()).hexdigest()

def load_json(p:Path, default):
    try: return json.loads(p.read_text())
    except Exception: return default

def save_json(p:Path,obj:Any):
    with tempfile.NamedTemporaryFile(mode='w', dir=p.parent, delete=False) as f:
        tmp=Path(f.name)
        try:
            f.write(json.dumps(obj,indent=2,sort_keys=True)+"\n")
            f.close()
            tmp.replace(p)
        finally:
            tmp.unlink(missing_ok=True)


def task_state(state:Path)->Path:
    root=git_root()
    branch=run(['git','symbolic-ref','--short','HEAD'],cwd=root,check=False) or safe_head(root)
    identity=TASK_ID or os.environ.get('AI_TASK_ID') or branch
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,199}',identity):
        raise SystemExit('Invalid task ID.')
    # Worktrees sharing a remote must never share mutable task artifacts.
    key=shasum(str(root.resolve())+'\0'+identity)[:24]
    d=state/'tasks'/key
    for sub in ('state','contracts','handoffs','review','gates'): (d/sub).mkdir(parents=True,exist_ok=True)
    save_json(d/'task.json',{'id':identity,'root':str(root.resolve())})
    return d


def enforce_budget(text:str,limit:int,label:str):
    if len(text)>limit:
        raise SystemExit(f'NEEDS_HUMAN: {label} exceeds budget ({len(text)} > {limit} characters). Shorten input or select a larger profile.')



def profile_repo(root:Path,state:Path)->dict:
    ext_map={'.ts':'TypeScript','.tsx':'TypeScript/React','.js':'JavaScript','.jsx':'JavaScript/React','.py':'Python','.java':'Java','.kt':'Kotlin','.go':'Go','.rs':'Rust','.rb':'Ruby','.php':'PHP','.cs':'C#','.swift':'Swift'}
    try: rels=run(["git","ls-files"],cwd=root).splitlines()
    except Exception: rels=[]
    counts={}
    for rel in rels:
        ext=Path(rel).suffix
        if ext in ext_map: counts[ext_map[ext]]=counts.get(ext_map[ext],0)+1
    langs=[k for k,_ in sorted(counts.items(), key=lambda kv:kv[1], reverse=True)[:8]]
    exists=lambda names:[n for n in names if (root/n).exists()]
    pm=[]
    for lock,name in [('pnpm-lock.yaml','pnpm'),('yarn.lock','yarn'),('package-lock.json','npm'),('bun.lockb','bun'),('uv.lock','uv'),('poetry.lock','poetry'),('go.mod','go'),('Cargo.toml','cargo')]:
        if (root/lock).exists(): pm.append(name)
    deps={}
    pkg=root/'package.json'
    if pkg.exists():
        try:
            j=json.loads(pkg.read_text()); deps={**j.get('dependencies',{}),**j.get('devDependencies',{})}
        except Exception: pass
    profile={
      "version":2,"generated_at":int(time.time()),"analyzed_commit":safe_head(root),
      "languages":langs,"language_file_counts":counts,"package_managers":pm,
      "formatters":exists(['.prettierrc','.prettierrc.json','prettier.config.js','prettier.config.mjs','biome.json','biome.jsonc','rustfmt.toml']),
      "linters":exists(['eslint.config.js','eslint.config.mjs','eslint.config.ts','.eslintrc','.eslintrc.json','biome.json','biome.jsonc','ruff.toml','.ruff.toml','pyproject.toml','checkstyle.xml']),
      "typecheckers":exists(['tsconfig.json','pyrightconfig.json','mypy.ini','pyproject.toml']),
      "tests":exists(['vitest.config.ts','vitest.config.js','jest.config.js','jest.config.ts','playwright.config.ts','pytest.ini','tox.ini']),
      "style_sources":exists(['CONTRIBUTING.md','STYLE.md','ARCHITECTURE.md','CLAUDE.md','AGENTS.md']),
      "dependencies":deps,
      "principle":"Project conventions > tooling > nearby module patterns > generic SOLID/FP advice"
    }
    save_json(state/'project-profile.json',profile); return profile


def safe_head(root:Path)->str:
    try:return run(["git","rev-parse","HEAD"],cwd=root)
    except:return ""


def collect_scope(root:Path,base:str)->dict:
    try: run(["git","rev-parse","--verify",base],cwd=root)
    except Exception: base="HEAD"
    files=run(["git","diff","--name-only",base],cwd=root,check=False).splitlines()
    untracked=run(["git","ls-files","--others","--exclude-standard"],cwd=root,check=False).splitlines()
    files=sorted(set([f for f in files+untracked if f and not any(f.startswith(p) for p in AI_PATTERNS)]))
    num=run(["git","diff","--numstat",base],cwd=root,check=False).splitlines(); lines=0
    for ln in num:
        parts=ln.split('\t')
        if len(parts)>=2:
            for x in parts[:2]:
                if x.isdigit(): lines+=int(x)
    return {"base":base,"files":files,"file_count":len(files),"changed_lines":lines}


def classify(scope:dict, profile:str)->dict:
    paths='\n'.join(scope['files']).lower(); risk='LOW'; reason='small/local change'; security=False
    high=re.compile(r'(^|/)(auth|authentication|authorization|rbac|iam|payment|payments|billing|migration|migrations|schema|database|db|crypto|secrets?|permissions?|infra|terraform|k8s|kubernetes)(/|$)|\.sql$|\.tf$',re.M)
    medium=re.compile(r'(^|/)(api|services?|integrations?|workers?|queues?|jobs?|repositories?|controllers?|middleware)(/|$)',re.M)
    sec=re.compile(r'(^|/)(auth|authentication|authorization|rbac|iam|crypto|secrets?|permissions?)(/|$)|security|tenant|token|credential',re.M)
    security=bool(sec.search(paths))
    if high.search(paths): risk,reason='HIGH','high-risk path/domain'
    elif medium.search(paths): risk,reason='MEDIUM','business/API/integration path'
    elif scope['changed_lines']>=160 or scope['file_count']>=6: risk,reason='MEDIUM','non-trivial diff size'
    if profile=='strict' and risk=='LOW': risk,reason='MEDIUM','strict profile minimum'
    return {"risk":risk,"reason":reason,"security":security}


def context_caps(profile:str)->dict:
    return {
      'fast': {
        'raw_files':4,'review_files':5,'agent_calls':3,'reviews':0,'docs_queries':1,'graph_queries':2,
        'skills':1,'findings':3,'context_chars':12000,'handoff_chars':3500,'retries':1
      },
      'standard': {
        'raw_files':8,'review_files':10,'agent_calls':5,'reviews':1,'docs_queries':3,'graph_queries':4,
        'skills':2,'findings':3,'context_chars':24000,'handoff_chars':5500,'retries':2
      },
      'strict': {
        'raw_files':12,'review_files':15,'agent_calls':7,'reviews':1,'docs_queries':5,'graph_queries':6,
        'skills':3,'findings':5,'context_chars':36000,'handoff_chars':7500,'retries':2
      }
    }[profile]


def skill_root()->Path:
    return STACK_ROOT/'skills'


def skill_registry()->dict:
    p=skill_root()/'registry.json'
    return load_json(p,{"skills":{}})


def skill_overrides(state:Path)->dict:
    return load_json(state/'skill-overrides.json',{})


def enabled_skills(state:Path)->dict:
    reg=skill_registry().get('skills',{})
    overrides=skill_overrides(state)
    out={}
    for name,meta in reg.items():
        m=dict(meta); m['enabled']=bool(overrides.get(name,m.get('enabled',True)))
        out[name]=m
    return out


def classify_task(task:str,figma:str|None=None)->str:
    t=(task or '').lower()
    if figma or 'figma.com/' in t: return 'design'
    if re.search(r'\b(bug|regression|exception|crash|failing|fails|failure|broken|incorrect|timeout|403|401|500)\b',t): return 'bug'
    if re.search(r'\b(migrate|migration|redesign|architecture|refactor|multi[- ]repo|rewrite|replace|deprecate)\b',t): return 'architecture'
    if re.search(r'\b(prototype|spike|proof of concept|poc|feasibility|can we|whether)\b',t): return 'prototype'
    if re.search(r'\b(ticket|tickets|epic|break down|decompose|roadmap)\b',t): return 'planning'
    return 'feature'


def select_skills(state:Path,task:str,profile:str,figma:str|None=None, explicit:list[str]|None=None)->list[str]:
    caps=context_caps(profile); registry=enabled_skills(state); explicit=explicit or []
    if explicit:
        unknown=[x for x in explicit if x not in registry]
        if unknown: raise SystemExit('Unknown skill(s): '+', '.join(unknown))
        return [x for x in explicit if registry[x]['enabled']][:caps['skills']]
    kind=classify_task(task,figma)
    scores=[]
    tl=(task or '').lower()
    for name,meta in registry.items():
        if not meta['enabled']: continue
        score=0
        if kind in meta.get('task_types',[]): score+=5
        if kind=='bug' and meta.get('category')=='debugging': score+=3
        if kind=='prototype' and name=='prototype': score+=3
        if kind=='architecture' and name=='wayfinder': score+=3
        if kind=='planning' and name=='to-tickets': score+=3
        for trig in meta.get('triggers',[]):
            if trig.lower() in tl: score+=2
        if meta.get('always_consider') and kind in ('feature','bug','design'): score+=1
        if score: scores.append((score, int(meta.get('priority',0)), name))
    scores.sort(reverse=True)
    return [name for _,_,name in scores[:caps['skills']]]


def load_skill_context(names:list[str], max_chars:int)->str:
    if not names:return '(none)'
    chunks=[]; used=0
    for name in names:
        p=skill_root()/name/'prompt.md'
        if not p.exists(): continue
        body=p.read_text().strip()
        remaining=max_chars-used
        if remaining<=0: break
        if len(body)>remaining: body=body[:remaining].rstrip()+"\n[skill context truncated by budget]"
        chunks.append(f"## Skill: {name}\n{body}")
        used+=len(body)
    return '\n\n'.join(chunks) if chunks else '(none)'


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


def record_metric(state:Path,event:str,**data):
    task=task_state(state)
    identity=load_json(task/'task.json',{})
    plan=load_json(task/'state/current-plan.json',{})
    p=state/'metrics.jsonl'; row={"ts":time.time(),"event":event,
        "task_key":task.name,"task_id":identity.get('id'),"profile":plan.get('profile'),**data}
    with p.open('a') as f: f.write(json.dumps(row,sort_keys=True)+"\n")

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


def rules_text(state:Path)->str:
    rules=load_json(state/'rules.json',[])
    if not rules:return "(none)"
    return '\n'.join(f"- [{r.get('scope','**')}] {r['rule']}" for r in rules)


def build_prompt(root:Path,state:Path,task:str,profile:str,base:str,figma:str|None,explicit_skills:list[str]|None=None)->str:
    scope=collect_scope(root,base); risk=classify(scope,profile); caps=context_caps(profile)
    selected_skills=select_skills(state,task,profile,figma,explicit_skills)
    skill_context=load_skill_context(selected_skills, max(2000,caps['context_chars']//3))
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

Repository rules:
{rules_text(state)}

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
    cache_key=task_cache_key(root,task,profile,base,selected_skills)
    save_json(task_state(state)/'state'/'current-plan.json',{"task":task,"task_type":classify_task(task,figma),"profile":profile,"scope":scope,"risk":risk,"caps":caps,"figma":figma,"skills":selected_skills,"cache_key":cache_key,"fingerprint":semantic_fingerprint(root),"crg":{"available":bool(crg_cmd()),"risk":parse_crg_risk(crg_text),"impact_cached":bool(crg_text)}})
    record_metric(state,'plan',profile=profile,task_type=classify_task(task,figma),risk=risk['risk'],skills=selected_skills,file_count=scope['file_count'],changed_lines=scope['changed_lines'])
    return prompt

def contamination(root:Path)->list[str]:
    tracked=run(["git","ls-files"],cwd=root,check=False).splitlines(); bad=[]
    for f in tracked:
        if f.startswith('.ai-review/') or f.startswith('graphify-out/') or f.startswith('.code-review-graph/') or f in ('CLAUDE.local.ai.md','AGENTS.local.ai.md'):
            bad.append(f)
    return bad


def cmd_init(args):
    root=git_root(); state=repo_state(root); profile=profile_repo(root,state)
    print(f"Repository: {root}")
    print(f"State:      {state}")
    print("Repository modified: NO")
    print("Languages:  "+(', '.join(profile['languages']) or 'unknown'))


def ctx7_cmd()->list[str]|None:
    if shutil.which('ctx7'): return ['ctx7']
    return None


def cmd_docs(args):
    root=git_root(); state=repo_state(root)
    if args.docs_cmd=='doctor':
        cmd=ctx7_cmd(); print('Context7:', 'ready' if cmd else 'missing (install: npm install -g ctx7)')
        if cmd:
            print(run(cmd+['--version'],check=False) or 'installed')
            who=run(cmd+['whoami'],check=False); print(who or 'not authenticated / anonymous limits')
        return
    if args.docs_cmd=='setup':
        cmd=ctx7_cmd()
        if not cmd: raise SystemExit('ctx7 CLI missing. Install first: npm install -g ctx7')
        mode='--mcp' if args.mcp else '--cli'
        target=[] if args.universal else ['--claude']
        print('Configuring Context7 globally; no project files will be created.')
        p=subprocess.run(cmd+['setup',mode,*target,'--yes'])
        if p.returncode: raise SystemExit(p.returncode)
        if args.mcp:
            print('Codex alternative: install the Context7 plugin globally from the Codex plugin marketplace, or configure the remote MCP in ~/.codex/config.toml.')
        return
    cmd=ctx7_cmd()
    if args.docs_cmd=='detect':
        p=load_json(state/'project-profile.json',{}) or profile_repo(root,state)
        deps=p.get('dependencies',{}); print(json.dumps(deps,indent=2)); return
    if args.docs_cmd=='library':
        if not cmd: raise SystemExit('ctx7 CLI missing. Install with: npm install -g ctx7')
        q=args.query or f"Documentation for {args.name} used by this repository"
        out=run(cmd+['library',args.name,q,'--json'],cwd=root)
        print(out)
        try:
            arr=json.loads(out); best=arr[0] if isinstance(arr,list) and arr else None
            if best and best.get('id'):
                m=load_json(state/'context7-libraries.json',{}); m[args.name]={"id":best['id'],"resolved_at":int(time.time())}; save_json(state/'context7-libraries.json',m)
        except Exception: pass
        return
    if args.docs_cmd=='query':
        lib=args.library
        mappings=load_json(state/'context7-libraries.json',{})
        if not lib.startswith('/') and lib in mappings: lib=mappings[lib]['id']
        if not lib.startswith('/'):
            raise SystemExit(f"Unknown Context7 ID for {lib}. Run: ai docs library {lib} \"<task>\"")
        key=shasum(lib+'\n'+args.query)[:20]; cache=state/'docs-cache'/f'{key}.json'
        if cache.exists() and not args.refresh:
            print(cache.read_text(), end=''); return
        if not cmd: raise SystemExit('ctx7 CLI missing and this query is not cached. Install with: npm install -g ctx7')
        out=run(cmd+['docs',lib,args.query,'--json'],cwd=root)
        cache.write_text(out+'\n'); print(out); return


def graph_env(state:Path)->dict:
    e=os.environ.copy(); e['GRAPHIFY_OUT']=str(state/'graphify'); return e

def cmd_graph(args):
    root=git_root(); state=repo_state(root); exe=shutil.which('graphify')
    if args.graph_cmd=='doctor':
        print('Graphify:', 'ready' if exe else 'missing (recommended: uv tool install graphifyy)')
        print('Graph storage:',state/'graphify'); return
    if not exe: raise SystemExit('graphify missing. Install: uv tool install graphifyy')
    env=graph_env(state)
    if args.graph_cmd in ('build','sync'):
        # GRAPHIFY_OUT supports an absolute external path, keeping the repository clean.
        p=subprocess.run([exe,str(root)],cwd=root,env=env)
        raise SystemExit(p.returncode)
    graph=state/'graphify'/'graph.json'
    if not graph.exists(): raise SystemExit('Graph not built. Run: ai graph build')
    if args.graph_cmd=='query': cmd=[exe,'query',args.query,'--graph',str(graph)]
    elif args.graph_cmd=='path': cmd=[exe,'path',args.start,args.end,'--graph',str(graph)]
    elif args.graph_cmd=='explain': cmd=[exe,'explain',args.node,'--graph',str(graph)]
    else: raise SystemExit(2)
    p=subprocess.run(cmd,cwd=root,env=env); raise SystemExit(p.returncode)



def crg_cmd()->list[str]|None:
    exe=shutil.which('code-review-graph')
    return [exe] if exe else None


def crg_env(state:Path)->dict:
    e=os.environ.copy()
    # CRG explicitly supports an external data directory. Keep every DB/artifact
    # out of the company repository so no gitignore/project config is required.
    e['CRG_DATA_DIR']=str(state/'code-review-graph')
    e['GRAPHIFY_OUT']=str(state/'graphify')
    e['AI_REPO_STATE']=str(state)
    return e


def crg_exec(root:Path,state:Path,argv:list[str],check=True)->tuple[int,str]:
    cmd=crg_cmd()
    if not cmd:
        if check: raise RuntimeError('code-review-graph missing')
        return 127,''
    p=subprocess.run(cmd+argv,cwd=root,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                     check=False,env=crg_env(state))
    if check and p.returncode:
        raise RuntimeError((p.stdout or 'code-review-graph failed').strip())
    return p.returncode,(p.stdout or '').strip()


def parse_crg_risk(text:str)->str|None:
    # CLI formatting can evolve; use deliberately loose matching and only accept
    # the three stable severity names. CRG may elevate our heuristic risk, never lower it.
    patterns=[r'(?i)\brisk(?: level| score)?\s*[:=-]\s*(HIGH|MEDIUM|LOW)\b',
              r'(?i)\b(HIGH|MEDIUM|LOW)\s+risk\b']
    for pat in patterns:
        m=re.search(pat,text)
        if m: return m.group(1).upper()
    return None


def elevate_risk(base_risk:dict,crg_risk:str|None)->dict:
    if not crg_risk:return base_risk
    rank={'LOW':0,'MEDIUM':1,'HIGH':2}
    current=base_risk.get('risk','LOW')
    if rank.get(crg_risk,-1)>rank.get(current,-1):
        out=dict(base_risk); out['risk']=crg_risk; out['reason']=f"{base_risk.get('reason','heuristic')} + CRG structural impact"
        out['crg_elevated']=True; return out
    out=dict(base_risk); out['crg_elevated']=False; return out


def crg_impact(root:Path,state:Path,base:str,refresh=False,build_if_missing=False)->str:
    if not crg_cmd(): return ''
    status_rc,_=crg_exec(root,state,['status'],check=False)
    if status_rc!=0:
        if not build_if_missing:return ''
        crg_exec(root,state,['build'])
    elif refresh:
        # update --brief re-parses current changes and then reports impact.
        rc,out=crg_exec(root,state,['update','--base',base,'--brief'],check=False)
        if rc==0 and out:
            (task_state(state)/'review'/'last-impact.txt').write_text(out+'\n'); return out
    rc,out=crg_exec(root,state,['detect-changes','--base',base,'--brief'],check=False)
    if rc==0 and out:
        (task_state(state)/'review'/'last-impact.txt').write_text(out+'\n')
        return out
    return ''


def cmd_crg(args):
    root=git_root(); state=repo_state(root); cmd=crg_cmd()
    if args.crg_cmd=='doctor':
        print('Code Review Graph:', 'ready' if cmd else 'missing (recommended: uv tool install code-review-graph)')
        print('External CRG data:',state/'code-review-graph')
        print('Repository writes: disabled by ai-agent-stack wrapper')
        return
    if not cmd: raise SystemExit('code-review-graph missing. Install: uv tool install code-review-graph')
    if args.crg_cmd=='build': argv=['build']
    elif args.crg_cmd=='update': argv=['update','--base',args.base] + (['--brief'] if args.brief else [])
    elif args.crg_cmd=='status': argv=['status']
    elif args.crg_cmd=='detect': argv=['detect-changes','--base',args.base] + (['--brief'] if args.brief else [])
    else: raise SystemExit(2)
    rc,out=crg_exec(root,state,argv,check=False)
    if out: print(out)
    raise SystemExit(rc)


def cmd_impact(args):
    root=git_root(); state=repo_state(root)
    scope=collect_scope(root,args.base); heuristic=classify(scope,args.profile)
    print('Change impact')
    print('  files:       ',scope['file_count'])
    print('  lines:       ',scope['changed_lines'])
    print('  heuristic:   ',heuristic['risk'],f"({heuristic['reason']})")
    if not crg_cmd():
        print('  CRG:         unavailable')
        print('  final risk:  ',heuristic['risk'])
        return
    out=crg_impact(root,state,args.base,refresh=args.refresh,build_if_missing=args.build)
    if not out:
        print('  CRG:         graph unavailable/stale; run `ai crg build` or `ai impact --build`')
        print('  final risk:  ',heuristic['risk'])
        return
    cr= parse_crg_risk(out); final=elevate_risk(heuristic,cr)
    print('  CRG risk:    ',cr or 'reported below')
    print('  final risk:  ',final['risk'])
    print('\n--- CRG compact impact ---')
    print(out)


def build_review_prompt(root:Path,state:Path,base:str,profile:str,impact:str)->str:
    scope=collect_scope(root,base); heuristic=classify(scope,profile)
    cr=parse_crg_risk(impact); risk=elevate_risk(heuristic,cr)
    caps=context_caps(profile)
    compact=impact[-12000:] if impact else '(CRG unavailable; use git diff plus targeted graph evidence.)'
    prompt=f'''# Independent adversarial review\n\nBase: {base}\nProfile: {profile}\nRisk: {risk['risk']} ({risk['reason']})\nChanged files: {scope['file_count']}\n\nCode Review Graph compact impact:\n```text\n{compact}\n```\n\nYou are the independent reviewer. Work read-only.\n\nContext policy:\n1. Start from the git diff and the CRG impact above.\n2. If more evidence is needed, use code-review-graph with CRG_DATA_DIR already provided by the environment. Prefer `detect-changes --brief`, affected flows, callers/importers/tests, and bounded/minimal context.\n3. Use Graphify only for architecture/community/path questions not answered by CRG.\n4. Use CodeGraph only when exact symbol navigation is materially better.\n5. Use Context7 only for version-sensitive external library/API facts.\n6. Read raw source only for changed/impacted locations needed to prove a finding.\n\nReview only for correctness, security/auth, data integrity, concurrency, backwards compatibility, concrete regression risks, and missing tests. No style-only findings; Ponytail handles final code quality.\n\nReturn at most {caps['findings']} findings. Each finding: SEVERITY | CONFIDENCE | FILE:LINE | CONCRETE FAILURE SCENARIO | EVIDENCE.\nDo not modify files and do not propose broad rewrites.\nFinish with a single-line JSON object containing status (PASS only when no unresolved findings, otherwise FAIL) and a nonempty evidence list of concrete checked facts.\n'''
    enforce_budget(prompt,caps['context_chars'],'review context')
    path=task_state(state)/'state'/'current-review.md'; path.write_text(prompt)
    save_json(task_state(state)/'state'/'current-review.json',{'base':base,'profile':profile,'risk':risk,'scope':scope,'created_at':int(time.time())})
    return prompt


def cmd_review(args):
    root=git_root(); state=repo_state(root)
    impact=crg_impact(root,state,args.base,refresh=args.refresh,build_if_missing=args.build) if crg_cmd() else ''
    prompt=build_review_prompt(root,state,args.base,args.profile,impact)
    print('Review context: ',task_state(state)/'state'/'current-review.md')
    print('CRG:            ','ready' if impact else ('installed but graph unavailable' if crg_cmd() else 'missing'))
    if not args.launch:return
    codex=shutil.which('codex')
    if not codex: raise SystemExit('Codex CLI missing. Review prompt was prepared; rerun with Codex installed.')
    # Codex exec supports a read-only sandbox. The CRG external-data environment
    # is inherited so the reviewer can query the graph without touching the repo.
    env=crg_env(state)
    p=subprocess.run([codex,'exec','-s','read-only','-C',str(root),prompt],cwd=root,env=env)
    raise SystemExit(p.returncode)


def cmd_figma(args):
    url='https://mcp.figma.com/mcp'
    if args.figma_cmd=='doctor':
        found=False
        claude=shutil.which('claude')
        codex=shutil.which('codex')
        if claude:
            out=run([claude,'mcp','list'],check=False)
            ok='figma' in out.lower(); found|=ok; print('Claude Figma MCP:', 'ready' if ok else 'not detected')
        else: print('Claude Figma MCP: claude missing')
        if codex:
            out=run([codex,'mcp','list'],check=False)
            ok='figma' in out.lower(); found|=ok; print('Codex Figma MCP: ', 'ready' if ok else 'not detected')
        else: print('Codex Figma MCP:  codex missing')
        print('Recommended remote:',url)
        return
    if args.figma_cmd=='setup':
        did=False
        if not args.codex_only and shutil.which('claude'):
            print('Adding Figma MCP to Claude user scope...')
            subprocess.run(['claude','mcp','add','--scope','user','--transport','http','figma',url],check=False); did=True
        if not args.claude_only and shutil.which('codex'):
            print('Adding Figma MCP to Codex user config...')
            subprocess.run(['codex','mcp','add','figma','--url',url],check=False); did=True
        if not did: raise SystemExit('Neither Claude nor Codex CLI is available.')
        print('Complete the OAuth authentication flow in each client.')
        return

def cmd_rules(args):
    root=git_root(); state=repo_state(root); p=state/'rules.json'; rules=load_json(p,[])
    if args.rules_cmd in (None,'list'):
        if not rules: print('No repository-specific rules.'); return
        for i,r in enumerate(rules,1): print(f"{i}. [{r.get('scope','**')}] {r['rule']}")
    elif args.rules_cmd=='add':
        rules.append({"rule":args.rule,"scope":args.scope,"source":"user","confidence":1.0,"created_at":int(time.time())}); save_json(p,rules); print('Rule added.')
    elif args.rules_cmd=='remove':
        idx=args.index-1
        if idx<0 or idx>=len(rules): raise SystemExit('Invalid rule index.')
        old=rules.pop(idx); save_json(p,rules); print('Removed:',old['rule'])


def cmd_planrun(args,launch:bool):
    root=git_root(); state=repo_state(root)
    if not (state/'project-profile.json').exists(): profile_repo(root,state)
    figma=args.figma
    if not args.no_figma and not figma:
        m=re.search(r'https?://\S*figma\.com/\S+',args.task or '')
        figma=m.group(0) if m else None
    if args.no_figma: figma=None
    prompt=build_prompt(root,state,args.task or 'Implement the current working task.',args.profile,args.base,figma,getattr(args,'skill',None))
    plan=load_json(task_state(state)/'state'/'current-plan.json',{})
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
    if launch:
        claude=shutil.which('claude')
        if not claude: raise SystemExit('Claude CLI missing. Use ai plan to only prepare.')
        env=crg_env(state)
        env['AI_TASK_ID']=load_json(task_state(state)/'task.json',{})['id']
        os.execvpe(claude,[claude,prompt],env)


def cmd_skill(args):
    root=git_root(); state=repo_state(root); registry=enabled_skills(state)
    if args.skill_cmd in (None,'list'):
        print('Skills')
        for name,meta in registry.items():
            mark='✓' if meta['enabled'] else '·'
            print(f"{mark} {name:20} {meta.get('category','')}  cost={meta.get('cost','?')}")
        if getattr(args,'task',None):
            selected=select_skills(state,args.task,args.profile,None,None)
            print('\nRecommended:', ', '.join(selected) or 'none')
        return
    name=args.name
    if name not in registry: raise SystemExit(f'Unknown skill: {name}')
    if args.skill_cmd=='explain':
        meta=registry[name]
        print(json.dumps({k:v for k,v in meta.items() if k!='enabled'},indent=2))
        p=skill_root()/name/'README.md'
        if p.exists(): print('\n'+p.read_text().strip())
        return
    if args.skill_cmd in ('enable','disable'):
        o=skill_overrides(state); o[name]=(args.skill_cmd=='enable'); save_json(state/'skill-overrides.json',o)
        print(f"{name}: {'enabled' if o[name] else 'disabled'}")
        return
    if args.skill_cmd=='dry-run':
        caps=context_caps(args.profile)
        print('SKILL DRY RUN')
        print('  name:       ',name)
        print('  category:   ',registry[name].get('category'))
        print('  cost:       ',registry[name].get('cost'))
        print('  max skills: ',caps['skills'])
        print('  stages:     ', ' -> '.join(registry[name].get('stages',[])))
        print('  repo writes: NO')
        return


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


def cmd_optimize(args):
    root=git_root(); state=repo_state(root)
    files=[root/'templates/policy.md',root/'templates/orchestration.yml']
    # The optimization target is the installed stack, not the work repo.
    targets=[STACK_ROOT/'templates/policy.md',STACK_ROOT/'templates/orchestration.yml']
    skill_prompts=list((STACK_ROOT/'skills').glob('*/prompt.md'))
    rows=[]
    for p in targets+skill_prompts:
        if not p.exists(): continue
        txt=p.read_text(); lines=[x.strip() for x in txt.splitlines() if x.strip()]
        dup=len(lines)-len(set(lines)); rows.append((str(p.relative_to(STACK_ROOT)),len(txt),len(lines),dup))
    print('Prompt/context optimization audit')
    for name,chars,lines,dup in sorted(rows,key=lambda r:r[1],reverse=True):
        print(f"  {name:42} chars={chars:5} lines={lines:3} duplicate_lines={dup}")
    print('\nPolicy: lazy skills; progressive context; evidence-first escalation; compact PASS outputs; hard per-profile budgets.')
    print('Repository modified: NO')

def cmd_status(args):
    root=git_root(); state=repo_state(root); print('Repository:',root); print('State:',state)
    print('Profile:', 'present' if (state/'project-profile.json').exists() else 'missing')
    print('Contract:', 'present' if (task_state(state)/'contracts/current-pr.yml').exists() else 'missing')
    print('Graphify:', 'ready' if (state/'graphify/graph.json').exists() else 'off')
    print('Context7 mappings:',len(load_json(state/'context7-libraries.json',{})))
    enabled=sum(1 for x in enabled_skills(state).values() if x['enabled']); print('Skills:',enabled,'enabled /',len(enabled_skills(state)),'installed')
    print('Token policy: lazy skills + hard profile budgets')
    print('Code Review Graph:', 'ready' if crg_cmd() and crg_exec(root,state,['status'],check=False)[0]==0 else ('installed/not-built' if crg_cmd() else 'off'))
    bad=contamination(root); print('Zero-footprint:', 'PASS' if not bad else 'FAIL');
    if bad:
        for x in bad: print('  tracked:',x)


def cmd_doctor(args):
    print('AI Agent Stack',VERSION)
    print('Skills Engine:',len(skill_registry().get('skills',{})),'skills installed')
    for name in ['git','python3','node','claude','codex','rtk','codegraph','graphify','code-review-graph','ctx7','gh']:
        print(('✓' if shutil.which(name) else '·'),f'{name:10}', 'installed' if shutil.which(name) else 'missing')
    try:
        root=git_root(); state=repo_state(root); bad=contamination(root)
        print('\nRepository:',root); print('External state:',state)
        print('Zero-footprint:', 'PASS' if not bad else 'FAIL')
        for x in bad: print('  tracked:',x)
    except SystemExit: pass


def cmd_metrics(args):
    state=repo_state(git_root()); p=state/'metrics.jsonl'; rows=[]; malformed=0
    for line in p.read_text().splitlines() if p.exists() else []:
        try:
            row=json.loads(line)
            if not isinstance(row,dict): raise ValueError()
            rows.append(row)
        except ValueError: malformed+=1
    if not args.all_tasks:
        key=task_state(state).name
        rows=[row for row in rows if row.get('task_key')==key]
    report=summarize(rows)
    report['scope']='repository' if args.all_tasks else 'task'
    report['malformed_events_skipped']=malformed
    if args.json: print(json.dumps(report,indent=2)); return
    print(f"Metrics ({report['scope']}): {report['gate_attempts']} gate attempts, "
          f"{report['gate_passes']} passed, {report['gate_failures']} failed")
    print(f"Gate time: {report['gate_duration_seconds']}s; repeated attempts: {report['repeated_gate_attempts']}")
    print(f"Pipelines ready: {report['pipeline_successes']}/{report['pipeline_runs']}")
    for name,value in report['usage'].items():
        total=value['reported_total']
        print(f"{name}: {total if total is not None else 'unreported'} ({value['reported_attempts']} reporting attempts)")


GATES = ('checks','regression','cleanup','provenance','ponytail','summary','contract','review','security','design')


def evidence_fingerprint(root:Path,state:Path,plan:dict)->str:
    digest=hashlib.sha256()
    for value in (safe_head(root), run(['git','rev-parse','--verify',plan['scope']['base']],cwd=root),
                  run(['git','ls-files','--stage'],cwd=root), run(['git','diff','--binary','HEAD'],cwd=root), json.dumps(plan,sort_keys=True)):
        digest.update(value.encode()); digest.update(b'\0')
    names=run(['git','ls-files','-z','--cached','--others','--exclude-standard'],cwd=root).split('\0')
    for name in sorted(set(filter(None,names))):
        path=root/name
        digest.update(name.encode()); digest.update(b'\0')
        if path.is_symlink(): digest.update(os.readlink(path).encode())
        elif path.is_file():
            digest.update(str(path.stat().st_mode).encode())
            with path.open('rb') as f:
                for block in iter(lambda:f.read(1024*1024),b''): digest.update(block)
        else: digest.update(b'<missing>')
    for path in [state/'rules.json',state/'skill-overrides.json',state/'validators.json',*sorted((task_state(state)/'contracts').glob('*'))]:
        if path.is_file(): digest.update(path.name.encode()+b'\0'+path.read_bytes())
    return digest.hexdigest()


def current_plan(state:Path)->dict:
    plan=load_json(task_state(state)/'state/current-plan.json',{})
    if not plan: raise SystemExit('NEEDS_HUMAN: run ai plan for this task first.')
    return plan


def validator_config(state):
    path=state/'validators.json'
    try:
        config=json.loads(path.read_text()) if path.exists() else {'version':1,'validators':{}}
        return validate_config(config)
    except (OSError,ValueError) as exc:
        raise SystemExit(f'Invalid validator configuration: {exc}')


def cmd_validators(args):
    state=repo_state(git_root()); config=validator_config(state)
    if args.action=='set':
        command=args.command[1:] if args.command[:1]==['--'] else args.command
        config['validators'][args.name]={'command':command,'adapter':args.adapter,
            'timeout':args.timeout,'evidence':args.evidence}
        try: validate_config(config)
        except ValueError as exc: raise SystemExit(str(exc))
        save_json(state/'validators.json',config)
    elif args.action=='remove':
        config['validators'].pop(args.name,None)
        save_json(state/'validators.json',config)
    print(json.dumps(config,indent=2))
    if args.action!='show': print('Saved:',state/'validators.json')


def cmd_pipeline(args):
    root=git_root(); state=repo_state(root); plan=current_plan(state)
    config=validator_config(state)['validators']
    required=required_gates(root,plan)
    missing=[name for name in required if name not in config]
    if missing:
        raise SystemExit('NEEDS_HUMAN: configure validators for '+', '.join(missing))
    if args.dry_run:
        print(json.dumps({'order':required,'validators':{name:config[name] for name in required}},indent=2))
        return
    started=time.monotonic(); status='FAILED'; executed=[]; skipped=[]
    try:
        for name in required:
            if validator_config(state)['validators']!=config:
                raise SystemExit('NEEDS_HUMAN: validator configuration changed; rerun pipeline.')
            fingerprint=evidence_fingerprint(root,state,current_plan(state))
            record=load_json(task_state(state)/'gates'/(name+'.json'),{})
            log=Path(record.get('log',''))
            item=config[name]
            if (args.resume and record.get('passed') and record.get('fingerprint')==fingerprint
                    and record.get('command')==item['command'] and record.get('adapter')==item['adapter']
                    and log.is_file() and hashlib.sha256(log.read_bytes()).hexdigest()==record.get('log_hash')):
                print(name+': fresh evidence reused'); skipped.append(name); continue
            executed.append(name)
            cmd_gate(argparse.Namespace(name=name,**item))
        cmd_ready(args)
        status='PR_READY'
    finally:
        record_metric(state,'pipeline',status=status,executed=executed,skipped=skipped,
                      duration_seconds=round(time.monotonic()-started,3))


def cmd_gate(args):
    root=git_root(); state=repo_state(root); plan=current_plan(state)
    command=args.command
    if command[:1]==['--']: command=command[1:]
    if not command: raise SystemExit('A gate requires an executable command after --.')
    if args.timeout<=0: raise SystemExit('Timeout must be positive.')
    before=evidence_fingerprint(root,state,plan)
    started=time.monotonic()
    directory=task_state(state)/'gates'
    log=directory/(args.name+'-'+uuid.uuid4().hex+'.log')
    env=dict(os.environ,AI_TASK_ID=load_json(task_state(state)/'task.json',{})['id'],
             AI_TASK_DIR=str(task_state(state)),AI_REPO_STATE=str(state),AI_GATE=args.name,
             AI_BASE=plan['scope']['base'])
    with log.open('w') as out:
        code=execute(command,root,env,out,args.timeout)
    after=evidence_fingerprint(root,state,current_plan(state))
    verdict=None
    try:
        verdict=json.loads(log.read_text(errors='replace').strip().splitlines()[-1])
    except (ValueError,IndexError): pass
    usage=usage_from_verdict(verdict)
    adapter=getattr(args,'adapter',None)
    if adapter=='exit-code':
        verdict={'status':'PASS' if code==0 else 'FAIL','evidence':[args.evidence]}
    valid_verdict=(isinstance(verdict,dict) and verdict.get('status')=='PASS'
        and isinstance(verdict.get('evidence'),list) and bool(verdict['evidence'])
        and all(isinstance(item,str) and item.strip() for item in verdict['evidence']))
    needs_verdict=adapter=='json' or args.name not in ('checks','regression')
    passed=code==0 and before==after and (not needs_verdict or valid_verdict)
    duration=round(time.monotonic()-started,3)
    save_json(directory/(args.name+'.json'),{'gate':args.name,'passed':passed,'exit_code':code,
        'verdict':verdict,'fingerprint':after,'command':command,'adapter':adapter,
        'duration_seconds':duration,'usage':usage,
        'log':str(log),'log_hash':hashlib.sha256(log.read_bytes()).hexdigest(),'created_at':time.time()})
    record_metric(state,'gate',gate=args.name,passed=passed,exit_code=code,duration_seconds=duration,usage=usage)
    print(f"{args.name}: {'PASS' if passed else 'FAIL'} | {log}")
    if before!=after: print('Repository or task changed during gate; rerun against the final state.')
    if needs_verdict and not valid_verdict: print('Gate requires final JSON line with status PASS and a nonempty evidence list.')
    if not passed: raise SystemExit(1)


def required_gates(root,plan):
    risk=classify(collect_scope(root,plan['scope']['base']),plan['profile'])
    required=list(GATES[:7])
    if plan['profile']!='fast' or risk['risk']!='LOW' or plan['risk']['risk']!='LOW': required.append('review')
    if risk['security'] or plan['risk']['security']: required.append('security')
    if plan.get('figma'): required.append('design')
    return [name for name in ORDER if name in required]


def cmd_ready(args):
    root=git_root(); state=repo_state(root); plan=current_plan(state)
    if contamination(root): raise SystemExit('FAILED: zero-footprint check failed.')
    required=required_gates(root,plan)
    fingerprint=evidence_fingerprint(root,state,plan)
    missing=[]; failed=[]
    for name in required:
        record=load_json(task_state(state)/'gates'/(name+'.json'),{})
        if record.get('fingerprint')!=fingerprint: missing.append(name); continue
        log=Path(record.get('log',''))
        if not log.is_file() or hashlib.sha256(log.read_bytes()).hexdigest()!=record.get('log_hash'): missing.append(name)
        elif not record.get('passed'): failed.append(name)
    status='FAILED' if failed else ('NEEDS_HUMAN' if missing else 'PR_READY')
    save_json(task_state(state)/'state/readiness.json',{'status':status,'required':required,'missing_or_stale':missing,'failed':failed,'fingerprint':fingerprint})
    record_metric(state,'readiness',status=status)
    print(status)
    if missing: print('Missing or stale: '+', '.join(missing))
    if failed: print('Failed: '+', '.join(failed))
    if status!='PR_READY': raise SystemExit(1)


def parser():
    p=argparse.ArgumentParser(prog='ai',description='Zero-footprint AI coding orchestrator')
    p.add_argument('--version',action='version',version=VERSION)
    sp=p.add_subparsers(dest='cmd')
    sp.add_parser('init')
    for name in ['run','plan']:
        q=sp.add_parser(name); q.add_argument('task',nargs='?',default=''); q.add_argument('--profile',choices=['fast','standard','strict'],default='standard'); q.add_argument('--base',default='main'); q.add_argument('--figma'); q.add_argument('--no-figma',action='store_true'); q.add_argument('--skill',action='append',default=None,help='Force a skill (repeatable; still capped by profile)')
    rev=sp.add_parser('review'); rev.add_argument('--profile',choices=['fast','standard','strict'],default='standard'); rev.add_argument('--base',default='main'); rev.add_argument('--refresh',action='store_true'); rev.add_argument('--build',action='store_true'); rev.add_argument('--no-launch',dest='launch',action='store_false',default=True)
    imp=sp.add_parser('impact'); imp.add_argument('--profile',choices=['fast','standard','strict'],default='standard'); imp.add_argument('--base',default='main'); imp.add_argument('--refresh',action='store_true'); imp.add_argument('--build',action='store_true')
    q=sp.add_parser('ready'); q.add_argument('--no-launch',action='store_true',help=argparse.SUPPRESS)
    gate=sp.add_parser('gate'); gate.add_argument('name',choices=GATES); gate.add_argument('--timeout',type=int,default=600); gate.add_argument('command',nargs=argparse.REMAINDER)
    pipeline=sp.add_parser('pipeline'); pipeline.add_argument('--dry-run',action='store_true'); pipeline.add_argument('--resume',action='store_true')
    validators=sp.add_parser('validators'); vs=validators.add_subparsers(dest='action',required=True)
    vs.add_parser('show')
    remove=vs.add_parser('remove'); remove.add_argument('name',choices=GATES)
    setting=vs.add_parser('set'); setting.add_argument('name',choices=GATES)
    setting.add_argument('--adapter',choices=['json','exit-code'],default='json')
    setting.add_argument('--evidence'); setting.add_argument('--timeout',type=int,default=600)
    setting.add_argument('command',nargs=argparse.REMAINDER)
    metrics=sp.add_parser('metrics'); metrics.add_argument('--all-tasks',action='store_true'); metrics.add_argument('--json',action='store_true')
    sp.add_parser('status'); sp.add_parser('doctor'); sp.add_parser('path'); sp.add_parser('optimize')
    sk=sp.add_parser('skill'); sks=sk.add_subparsers(dest='skill_cmd'); sl=sks.add_parser('list'); sl.add_argument('--task'); sl.add_argument('--profile',choices=['fast','standard','strict'],default='standard'); se=sks.add_parser('explain'); se.add_argument('name'); sen=sks.add_parser('enable'); sen.add_argument('name'); sdis=sks.add_parser('disable'); sdis.add_argument('name'); sd=sks.add_parser('dry-run'); sd.add_argument('name'); sd.add_argument('--profile',choices=['fast','standard','strict'],default='standard')
    ho=sp.add_parser('handoff'); ho.add_argument('task',nargs='?',default=''); ho.add_argument('--profile',choices=['fast','standard','strict'],default='standard'); ho.add_argument('--base',default='main'); ho.add_argument('--state'); ho.add_argument('--evidence',action='append'); ho.add_argument('--next')
    r=sp.add_parser('rules'); rs=r.add_subparsers(dest='rules_cmd'); rs.add_parser('list'); a=rs.add_parser('add'); a.add_argument('rule'); a.add_argument('--scope',default='**'); rm=rs.add_parser('remove'); rm.add_argument('index',type=int)
    d=sp.add_parser('docs'); ds=d.add_subparsers(dest='docs_cmd',required=True); ds.add_parser('doctor'); setup=ds.add_parser('setup'); setup.add_argument('--mcp',action='store_true'); setup.add_argument('--universal',action='store_true'); ds.add_parser('detect'); l=ds.add_parser('library'); l.add_argument('name'); l.add_argument('query',nargs='?',default=''); q=ds.add_parser('query'); q.add_argument('library'); q.add_argument('query'); q.add_argument('--refresh',action='store_true')
    f=sp.add_parser('figma'); fs=f.add_subparsers(dest='figma_cmd',required=True); fs.add_parser('doctor'); fsetup=fs.add_parser('setup'); fsetup.add_argument('--claude-only',action='store_true'); fsetup.add_argument('--codex-only',action='store_true')
    c=sp.add_parser('crg'); cs=c.add_subparsers(dest='crg_cmd',required=True); cs.add_parser('doctor'); cs.add_parser('build'); st=cs.add_parser('status'); up=cs.add_parser('update'); up.add_argument('--base',default='main'); up.add_argument('--brief',action='store_true',default=True); de=cs.add_parser('detect'); de.add_argument('--base',default='main'); de.add_argument('--brief',action='store_true',default=True)
    g=sp.add_parser('graph'); gs=g.add_subparsers(dest='graph_cmd',required=True); gs.add_parser('doctor'); gs.add_parser('build'); gs.add_parser('sync'); q=gs.add_parser('query'); q.add_argument('query'); pa=gs.add_parser('path'); pa.add_argument('start'); pa.add_argument('end'); e=gs.add_parser('explain'); e.add_argument('node')
    for command in sp.choices.values():
        command.add_argument('--task-id',help='Task identity; defaults to AI_TASK_ID or current branch/worktree')
    return p


def main():
    global TASK_ID
    p=parser(); args=p.parse_args()
    TASK_ID=getattr(args,'task_id',None)
    if not args.cmd: p.print_help(); return
    if args.cmd=='init': cmd_init(args)
    elif args.cmd=='run': cmd_planrun(args,True)
    elif args.cmd=='plan': cmd_planrun(args,False)
    elif args.cmd=='review': cmd_review(args)
    elif args.cmd=='impact': cmd_impact(args)
    elif args.cmd=='ready': cmd_ready(args)
    elif args.cmd=='gate': cmd_gate(args)
    elif args.cmd=='validators': cmd_validators(args)
    elif args.cmd=='pipeline': cmd_pipeline(args)
    elif args.cmd=='status': cmd_status(args)
    elif args.cmd=='doctor': cmd_doctor(args)
    elif args.cmd=='metrics': cmd_metrics(args)
    elif args.cmd=='skill': cmd_skill(args)
    elif args.cmd=='handoff': cmd_handoff(args)
    elif args.cmd=='optimize': cmd_optimize(args)
    elif args.cmd=='path': print(task_state(repo_state(git_root())))
    elif args.cmd=='rules': cmd_rules(args)
    elif args.cmd=='docs': cmd_docs(args)
    elif args.cmd=='figma': cmd_figma(args)
    elif args.cmd=='crg': cmd_crg(args)
    elif args.cmd=='graph': cmd_graph(args)

if __name__=='__main__': main()
