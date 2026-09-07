from __future__ import annotations
import json, shutil, time
from pathlib import Path
from core import STACK_ROOT, VERSION, contamination, git_root, load_json, profile_repo, repo_state, safe_head, save_json, task_state
from crg import crg_cmd, crg_exec
from skills import enabled_skills, skill_registry
from validators import run_codex_json


DEEP_PROFILE_SCHEMA={
    'type':'object','additionalProperties':False,
    'required':['architecture_summary','stack','database','deployment','related_repos','key_docs','confidence_caveats'],
    'properties':{
        'architecture_summary':{'type':'string'},
        'stack':{'type':'object','additionalProperties':False,'required':['backend','frontend','other'],
                 'properties':{'backend':{'type':'array','items':{'type':'string'}},
                               'frontend':{'type':'array','items':{'type':'string'}},
                               'other':{'type':'array','items':{'type':'string'}}}},
        'database':{'type':'string'},
        'deployment':{'type':'string'},
        'related_repos':{'type':'array','items':{'type':'string'}},
        'key_docs':{'type':'array','items':{'type':'string'}},
        'confidence_caveats':{'type':'array','items':{'type':'string'}},
    },
}


def check_deep_profile(value:dict)->dict:
    required=DEEP_PROFILE_SCHEMA['required']
    if not isinstance(value,dict) or not all(k in value for k in required):
        raise ValueError('Deep profile response missing required fields.')
    stack=value.get('stack')
    if not isinstance(stack,dict) or not all(k in stack for k in ('backend','frontend','other')):
        raise ValueError('Deep profile stack must include backend/frontend/other arrays.')
    for key in ('related_repos','key_docs','confidence_caveats'):
        if not isinstance(value[key],list): raise ValueError(f'Deep profile {key} must be an array.')
    return value


def cmd_profile(args):
    root=git_root(); state=repo_state(root)
    if args.refresh or not (state/'project-profile.json').exists(): profile_repo(root,state)
    profile=load_json(state/'project-profile.json',{})
    if not args.deep: print(json.dumps(profile,indent=2)); return
    deep_path=state/'project-deep-profile.json'
    current_commit=safe_head(root)
    existing=load_json(deep_path,None)
    if existing and not args.refresh and existing.get('analyzed_commit')==current_commit:
        print(f"Deep profile up to date (commit {current_commit[:12]}); use --refresh to force.")
        print(json.dumps(existing,indent=2)); return
    executable=shutil.which('codex')
    if not executable: raise SystemExit('Codex CLI missing. Install/authenticate Codex to run a deep profile.')
    prompt=f'''Read this repository read-only and describe it factually, citing file paths for every claim.
Do not modify files, run any command that writes, or fabricate anything you cannot verify by reading.
Identify: 1) architecture pattern (backend/frontend separation, monolith vs services, layering);
2) the stack actually in use (languages/frameworks, split into backend/frontend/other);
3) database technology and schema structure if present (migrations, models, schema files) as free text,
or "none detected" if there is none; 4) deployment/CI-CD workflow, reading files such as
.github/workflows, Dockerfile, docker-compose.yml, deploy scripts, or "none detected";
5) links to other repositories: git submodules, workspace/monorepo package references, a
"repository" field in package.json/pyproject.toml pointing elsewhere, or explicit mentions in
README/docs of related repos — empty array if none found; 6) relevant documentation: read
README/ARCHITECTURE.md/CONTRIBUTING.md/docs/* content (not just note that they exist) and list
each as "path: one-line summary of what it actually says". List in confidence_caveats anything
you could not verify, that seems ambiguous, or that you are inferring rather than reading directly.
This is descriptive context for future work, not a pass/fail review — there is no PASS/FAIL status.

Repository: {root}
Already-detected static facts (languages, package managers, tooling): {json.dumps(profile)}
'''
    try:
        deep=run_codex_json(executable,root,state/'review','deep-profile',prompt,DEEP_PROFILE_SCHEMA,
                             check_deep_profile,timeout=args.timeout)
    except ValueError as exc:
        raise SystemExit(str(exc))
    deep={k:deep[k] for k in DEEP_PROFILE_SCHEMA['required']}|{'usage':deep.get('usage',{})}
    deep.update(version=1,generated_at=int(time.time()),analyzed_commit=current_commit,
                caveat='AI-generated interpretation of the repository; verify before relying on it for critical decisions.')
    save_json(deep_path,deep)
    print(json.dumps(deep,indent=2))


def cmd_init(args):
    root=git_root(); state=repo_state(root); profile=profile_repo(root,state)
    print(f"Repository: {root}")
    print(f"State:      {state}")
    print("Repository modified: NO")
    print("Languages:  "+(', '.join(profile['languages']) or 'unknown'))


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
