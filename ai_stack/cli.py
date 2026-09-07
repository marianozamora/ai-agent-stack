#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, subprocess, sys, textwrap, time
from pathlib import Path
from typing import Any

VERSION = "0.6.0"
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
        for sub in ["contracts","state","graphify","docs-cache","code-review-graph","review"]: (d/sub).mkdir(parents=True,exist_ok=True)
        meta={"version":1,"repo_id":rid,"remote":remote,"last_root":str(root),"updated_at":int(time.time())}
        (d/"repo.json").write_text(json.dumps(meta,indent=2)+"\n")
        if not (d/"rules.json").exists(): (d/"rules.json").write_text("[]\n")
        if not (d/"observations.json").exists(): (d/"observations.json").write_text("[]\n")
        if not (d/"context7-libraries.json").exists(): (d/"context7-libraries.json").write_text("{}\n")
    return d


def shasum(s:str)->str: return hashlib.sha256(s.encode()).hexdigest()

def load_json(p:Path, default):
    try: return json.loads(p.read_text())
    except Exception: return default

def save_json(p:Path,obj:Any): p.write_text(json.dumps(obj,indent=2,sort_keys=True)+"\n")


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
      'fast': {'raw_files':4,'review_files':6,'agent_calls':3,'reviews':0,'docs_queries':1,'graph_queries':2},
      'standard': {'raw_files':8,'review_files':12,'agent_calls':5,'reviews':1,'docs_queries':3,'graph_queries':4},
      'strict': {'raw_files':12,'review_files':18,'agent_calls':7,'reviews':2,'docs_queries':5,'graph_queries':6}
    }[profile]


def ensure_contract(state:Path,task:str,figma:str|None=None):
    p=state/'contracts'/'current-pr.yml'
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
        d=state/'contracts'/'current-design.yml'
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


def build_prompt(root:Path,state:Path,task:str,profile:str,base:str,figma:str|None)->str:
    scope=collect_scope(root,base); risk=classify(scope,profile); caps=context_caps(profile)
    crg_text=''
    if profile!='fast' and crg_cmd():
        crg_text=crg_impact(root,state,base,refresh=False,build_if_missing=False)
        risk=elevate_risk(risk,parse_crg_risk(crg_text))
    ensure_contract(state,task,figma)
    graph=str(state/'graphify'/'graph.json')
    prompt=f'''# AI Agent Stack orchestration\n\nTask: {task}\nProfile: {profile}\nBase: {base}\nRisk: {risk['risk']} ({risk['reason']})\nSecurity boundary: {risk['security']}\n\nExternal state (NEVER commit these files): {state}\nPR Contract: {state/'contracts/current-pr.yml'}\nDesign Contract: {(state/'contracts/current-design.yml') if figma else 'off'}\nGraphify graph: {graph if Path(graph).exists() else 'not built'}\nCode Review Graph: {'ready' if crg_text else ('installed/not-ready' if crg_cmd() else 'missing')}\n\nRepository rules:\n{rules_text(state)}\n\nContext budget:\n- raw files <= {caps['raw_files']} unless evidence requires escalation\n- review files <= {caps['review_files']}\n- agent calls <= {caps['agent_calls']}\n- review rounds <= {caps['reviews']}\n- Context7 docs queries <= {caps['docs_queries']}\n- Graphify structural queries <= {caps['graph_queries']}\n\nContext order:\n1. PR/design contract\n2. Code Review Graph for diff impact, blast radius, affected flows, tests and minimal review context\n3. Graphify for macro architecture/routes/communities when CRG cannot answer the architecture question\n4. CodeGraph for exact symbol navigation when materially better than CRG\n5. Context7 ONLY for external library/framework/API documentation; prefer exact installed version and cached library IDs\n6. RTK for git/tests/lint/search output\n7. raw source reads only when needed to prove/implement something\n\nExternal docs policy:\n- Never rely on memory for version-sensitive library APIs when Context7 can verify them.\n- Query only the library/topic needed for the current implementation.\n- Do not dump broad documentation into context.\n- Repository code and tests remain the source of truth for project-specific behavior.\n\nCorrectness pipeline:\nBuilder -> deterministic checks -> regression check -> Codex adversarial review only when risk/profile warrants -> confirmed fixes -> Cleanup -> checks -> provenance gate -> Ponytail -> PR summary.\nPonytail judges project-specific quality; it does not impose SOLID or FP contrary to repo conventions.\nCleanup removes AI provenance/references and unnecessary comments without changing behavior.\n\nFigma: {'ACTIVE: ingest via Figma MCP into the compact Design Contract, then discard raw design context.' if figma else 'off'}\n\nZero-footprint invariant: DO NOT create or modify AI framework/config/state files in the working repository. Do not modify .gitignore for this framework.\n\nReturn terminal state PR_READY, NEEDS_HUMAN, or FAILED with concise evidence.\n'''
    (state/'state'/'current-run.md').write_text(prompt)
    save_json(state/'state'/'current-plan.json',{"task":task,"profile":profile,"scope":scope,"risk":risk,"caps":caps,"figma":figma,"crg":{"available":bool(crg_cmd()),"risk":parse_crg_risk(crg_text),"impact_cached":bool(crg_text)}})
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
            (state/'review'/'last-impact.txt').write_text(out+'\n'); return out
    rc,out=crg_exec(root,state,['detect-changes','--base',base,'--brief'],check=False)
    if rc==0 and out:
        (state/'review'/'last-impact.txt').write_text(out+'\n')
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
    prompt=f'''# Independent adversarial review\n\nBase: {base}\nProfile: {profile}\nRisk: {risk['risk']} ({risk['reason']})\nChanged files: {scope['file_count']}\n\nCode Review Graph compact impact:\n```text\n{compact}\n```\n\nYou are the independent reviewer. Work read-only.\n\nContext policy:\n1. Start from the git diff and the CRG impact above.\n2. If more evidence is needed, use code-review-graph with CRG_DATA_DIR already provided by the environment. Prefer `detect-changes --brief`, affected flows, callers/importers/tests, and bounded/minimal context.\n3. Use Graphify only for architecture/community/path questions not answered by CRG.\n4. Use CodeGraph only when exact symbol navigation is materially better.\n5. Use Context7 only for version-sensitive external library/API facts.\n6. Read raw source only for changed/impacted locations needed to prove a finding.\n\nReview only for correctness, security/auth, data integrity, concurrency, backwards compatibility, concrete regression risks, and missing tests. No style-only findings; Ponytail handles final code quality.\n\nReturn at most {min(5,max(3,caps['review_files']//4))} findings. Each finding: SEVERITY | CONFIDENCE | FILE:LINE | CONCRETE FAILURE SCENARIO | EVIDENCE.\nDo not modify files and do not propose broad rewrites.\n'''
    path=state/'state'/'current-review.md'; path.write_text(prompt)
    save_json(state/'state'/'current-review.json',{'base':base,'profile':profile,'risk':risk,'scope':scope,'created_at':int(time.time())})
    return prompt


def cmd_review(args):
    root=git_root(); state=repo_state(root)
    impact=crg_impact(root,state,args.base,refresh=args.refresh,build_if_missing=args.build) if crg_cmd() else ''
    prompt=build_review_prompt(root,state,args.base,args.profile,impact)
    print('Review context: ',state/'state'/'current-review.md')
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
    prompt=build_prompt(root,state,args.task or 'Implement the current working task.',args.profile,args.base,figma)
    plan=load_json(state/'state'/'current-plan.json',{})
    print('AI plan')
    print('  repo state: ',state)
    print('  profile:    ',args.profile)
    print('  risk:       ',plan.get('risk',{}).get('risk'))
    print('  files:      ',plan.get('scope',{}).get('file_count'))
    print('  figma:      ',figma or 'off')
    print('  Context7:   ','ready' if ctx7_cmd() else 'missing')
    print('  Graphify:   ','ready' if (state/'graphify'/'graph.json').exists() else 'not built')
    print('  CRG:        ', 'ready' if plan.get('crg',{}).get('impact_cached') else ('installed/not-ready' if crg_cmd() else 'missing'))
    print('  prompt:     ',state/'state'/'current-run.md')
    if launch:
        claude=shutil.which('claude')
        if not claude: raise SystemExit('Claude CLI missing. Use ai plan to only prepare.')
        os.execvpe(claude,[claude,prompt],crg_env(state))


def cmd_status(args):
    root=git_root(); state=repo_state(root); print('Repository:',root); print('State:',state)
    print('Profile:', 'present' if (state/'project-profile.json').exists() else 'missing')
    print('Contract:', 'present' if (state/'contracts/current-pr.yml').exists() else 'missing')
    print('Graphify:', 'ready' if (state/'graphify/graph.json').exists() else 'off')
    print('Context7 mappings:',len(load_json(state/'context7-libraries.json',{})))
    print('Code Review Graph:', 'ready' if crg_cmd() and crg_exec(root,state,['status'],check=False)[0]==0 else ('installed/not-built' if crg_cmd() else 'off'))
    bad=contamination(root); print('Zero-footprint:', 'PASS' if not bad else 'FAIL');
    if bad:
        for x in bad: print('  tracked:',x)


def cmd_doctor(args):
    print('AI Agent Stack',VERSION)
    for name in ['git','python3','node','claude','codex','rtk','codegraph','graphify','code-review-graph','ctx7','gh']:
        print(('✓' if shutil.which(name) else '·'),f'{name:10}', 'installed' if shutil.which(name) else 'missing')
    try:
        root=git_root(); state=repo_state(root); bad=contamination(root)
        print('\nRepository:',root); print('External state:',state)
        print('Zero-footprint:', 'PASS' if not bad else 'FAIL')
        for x in bad: print('  tracked:',x)
    except SystemExit: pass


def cmd_metrics(args):
    state=repo_state(git_root()); p=state/'metrics.jsonl'
    if not p.exists(): print('No metrics recorded yet.'); return
    lines=p.read_text().splitlines(); print('Events:',len(lines)); print('\n'.join(lines[-10:]))


def cmd_ready(args):
    root=git_root(); state=repo_state(root); bad=contamination(root)
    if bad:
        print('ZERO_FOOTPRINT: FAIL'); [print(' ',x) for x in bad]; raise SystemExit(2)
    # Use the same orchestration prompt but explicitly final-gate only.
    task='Final PR readiness: Cleanup -> checks -> provenance -> Ponytail -> PR summary. Do not change behavior.'
    ns=argparse.Namespace(task=task,profile=args.profile,base=args.base,figma=None,no_figma=True)
    cmd_planrun(ns,args.launch)


def parser():
    p=argparse.ArgumentParser(prog='ai',description='Zero-footprint AI coding orchestrator')
    p.add_argument('--version',action='version',version=VERSION)
    sp=p.add_subparsers(dest='cmd')
    sp.add_parser('init')
    for name in ['run','plan']:
        q=sp.add_parser(name); q.add_argument('task',nargs='?',default=''); q.add_argument('--profile',choices=['fast','standard','strict'],default='standard'); q.add_argument('--base',default='main'); q.add_argument('--figma'); q.add_argument('--no-figma',action='store_true')
    rev=sp.add_parser('review'); rev.add_argument('--profile',choices=['fast','standard','strict'],default='standard'); rev.add_argument('--base',default='main'); rev.add_argument('--refresh',action='store_true'); rev.add_argument('--build',action='store_true'); rev.add_argument('--no-launch',dest='launch',action='store_false',default=True)
    imp=sp.add_parser('impact'); imp.add_argument('--profile',choices=['fast','standard','strict'],default='standard'); imp.add_argument('--base',default='main'); imp.add_argument('--refresh',action='store_true'); imp.add_argument('--build',action='store_true')
    q=sp.add_parser('ready'); q.add_argument('--profile',choices=['fast','standard','strict'],default='standard'); q.add_argument('--base',default='main'); q.add_argument('--no-launch',dest='launch',action='store_false',default=True)
    sp.add_parser('status'); sp.add_parser('doctor'); sp.add_parser('metrics'); sp.add_parser('path')
    r=sp.add_parser('rules'); rs=r.add_subparsers(dest='rules_cmd'); rs.add_parser('list'); a=rs.add_parser('add'); a.add_argument('rule'); a.add_argument('--scope',default='**'); rm=rs.add_parser('remove'); rm.add_argument('index',type=int)
    d=sp.add_parser('docs'); ds=d.add_subparsers(dest='docs_cmd',required=True); ds.add_parser('doctor'); setup=ds.add_parser('setup'); setup.add_argument('--mcp',action='store_true'); setup.add_argument('--universal',action='store_true'); ds.add_parser('detect'); l=ds.add_parser('library'); l.add_argument('name'); l.add_argument('query',nargs='?',default=''); q=ds.add_parser('query'); q.add_argument('library'); q.add_argument('query'); q.add_argument('--refresh',action='store_true')
    f=sp.add_parser('figma'); fs=f.add_subparsers(dest='figma_cmd',required=True); fs.add_parser('doctor'); fsetup=fs.add_parser('setup'); fsetup.add_argument('--claude-only',action='store_true'); fsetup.add_argument('--codex-only',action='store_true')
    c=sp.add_parser('crg'); cs=c.add_subparsers(dest='crg_cmd',required=True); cs.add_parser('doctor'); cs.add_parser('build'); st=cs.add_parser('status'); up=cs.add_parser('update'); up.add_argument('--base',default='main'); up.add_argument('--brief',action='store_true',default=True); de=cs.add_parser('detect'); de.add_argument('--base',default='main'); de.add_argument('--brief',action='store_true',default=True)
    g=sp.add_parser('graph'); gs=g.add_subparsers(dest='graph_cmd',required=True); gs.add_parser('doctor'); gs.add_parser('build'); gs.add_parser('sync'); q=gs.add_parser('query'); q.add_argument('query'); pa=gs.add_parser('path'); pa.add_argument('start'); pa.add_argument('end'); e=gs.add_parser('explain'); e.add_argument('node')
    return p


def main():
    p=parser(); args=p.parse_args()
    if not args.cmd: p.print_help(); return
    if args.cmd=='init': cmd_init(args)
    elif args.cmd=='run': cmd_planrun(args,True)
    elif args.cmd=='plan': cmd_planrun(args,False)
    elif args.cmd=='review': cmd_review(args)
    elif args.cmd=='impact': cmd_impact(args)
    elif args.cmd=='ready': cmd_ready(args)
    elif args.cmd=='status': cmd_status(args)
    elif args.cmd=='doctor': cmd_doctor(args)
    elif args.cmd=='metrics': cmd_metrics(args)
    elif args.cmd=='path': print(repo_state(git_root()))
    elif args.cmd=='rules': cmd_rules(args)
    elif args.cmd=='docs': cmd_docs(args)
    elif args.cmd=='figma': cmd_figma(args)
    elif args.cmd=='crg': cmd_crg(args)
    elif args.cmd=='graph': cmd_graph(args)

if __name__=='__main__': main()
