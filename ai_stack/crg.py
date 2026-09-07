from __future__ import annotations
import os, re, shutil, subprocess, time
from pathlib import Path
from core import classify, collect_scope, context_caps, enforce_budget, git_root, repo_state, save_json, task_state


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
