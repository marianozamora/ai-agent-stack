from __future__ import annotations
import json, os, re, shutil, time
from pathlib import Path
from typing import Any
from capabilities import CAPABILITIES
from core import STACK_ROOT, VERSION, contamination, git_root, json_file_health, known_repo_states, first_base, load_json, profile_repo, remote_id, repo_state, require_human, resolve_base, safe_head, save_json, task_state, verify_ref
from crg import crg_cmd, crg_exec
from detect import proposal, render
from metrics import metric_index_health
from providers import builder as get_builder, reviewer as get_reviewer
from skills import enabled_skills, skill_registry
from tasks import running_tasks


METRICS_ADVISORY_ROWS=5000  # row count above which ai doctor suggests retention for audit size


DEEP_PROFILE_SCHEMA:dict[str,Any]={
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
    active_reviewer=get_reviewer(state)
    if not active_reviewer.probe_binary or not shutil.which(active_reviewer.probe_binary):
        label=active_reviewer.name.capitalize()
        raise SystemExit(f'{label} CLI missing. Install/authenticate {label} to run a deep profile.')
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
        deep=active_reviewer.verdict(root,state/'review','deep-profile',prompt,DEEP_PROFILE_SCHEMA,
                                     check_deep_profile,timeout=args.timeout)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    deep={k:deep[k] for k in DEEP_PROFILE_SCHEMA['required']}|{'usage':deep.get('usage',{})}
    deep.update(version=1,generated_at=int(time.time()),analyzed_commit=current_commit,
                caveat='AI-generated interpretation of the repository; verify before relying on it for critical decisions.')
    save_json(deep_path,deep)
    print(json.dumps(deep,indent=2))


def base_report(root,state)->str:
    """How `ai status`/`ai doctor` describe the diff base, without ever guessing one."""
    stored=load_json(state/'repo.json',{}).get('default_base')
    if stored: return f'{stored} (recorded)' if verify_ref(root,stored) else f'{stored} (recorded, MISSING — rerun ai init --base <ref>)'
    # first_base(), not base_suggestions(): this line only ever showed the top
    # suggestion, so verifying the rest was up to 8 extra `git rev-parse` spawns
    # per `ai status`/`ai doctor` whose results were built and thrown away.
    detected=first_base(root)
    return f'{detected} (detected; record it with ai init)' if detected else 'FAIL: none detected'


def cmd_init(args):
    root=git_root(); state=repo_state(root); profile=profile_repo(root,state)
    # cli.main() exempts `init` from the blanket base resolution so a brand-new repo
    # with zero commits (nothing to verify any ref against yet) can still be
    # initialized. An explicit --base still fails loudly if it doesn't resolve; only
    # silent autodetection over an empty repo is tolerated, and records nothing.
    requested=getattr(args,'base',None)
    base=None
    try:
        base=resolve_base(root,requested,state)
    except SystemExit as exc:
        if requested: raise
        print(f'Note: {exc}')
    meta=load_json(state/'repo.json',{})
    if base: meta['default_base']=base
    save_json(state/'repo.json',meta)
    print(f"Repository: {root}")
    print(f"State:      {state}")
    print("Repository modified: NO")
    print("Languages:  "+(', '.join(profile['languages']) or 'unknown'))
    print(f"Default base: {base or 'none detected yet -- rerun `ai init` after your first commit'}")
    # Report the detected tooling but never write it: validators.json holds commands
    # `ai pipeline` executes, so applying a proposal stays an explicit, separate act.
    configured=load_json(state/'validators.json',{}).get('validators',{})
    report=proposal(root,configured)
    print('\nCheck tooling detected:')
    print('\n'.join(render(report)))
    if any(row['action']=='add' for row in report['rows']):
        print('\nRun `ai validators propose --apply` to configure these gates,')
        print('then `ai validators install` for the bundled semantic validators.')


def cmd_status(args):
    root=git_root(); state=repo_state(root); print('Repository:',root); print('State:',state)
    print('Base:', base_report(root,state))
    print('Profile:', 'present' if (state/'project-profile.json').exists() else 'missing')
    print('Contract:', 'present' if (task_state(state)/'contracts/current-pr.yml').exists() else 'missing')
    print('Graphify:', 'ready' if (state/'graphify/graph.json').exists() else 'off')
    print('Context7 mappings:',len(load_json(state/'context7-libraries.json',{})))
    enabled=sum(1 for x in enabled_skills(state).values() if x['enabled']); print('Skills:',enabled,'enabled /',len(enabled_skills(state)),'installed')
    print('Token policy: lazy skills + hard profile budgets')
    print('Code Review Graph:', 'ready' if crg_cmd() and crg_exec(root,state,['status'],check=False)[0]==0 else ('installed/not-built' if crg_cmd() else 'off'))
    running=running_tasks(state,root)
    print('Pipelines running:', ', '.join(f'{r["id"]} (pid {r["pid"]})' for r in running) or 'none')
    bad=contamination(root); print('Zero-footprint:', 'PASS' if not bad else 'FAIL');
    if bad:
        for x in bad: print('  tracked:',x)


def cmd_doctor(args):
    print('AI Agent Stack',VERSION)
    print('Skills Engine:',len(skill_registry().get('skills',{})),'skills installed')
    # The builder/reviewer binaries to probe come from this repository's actual
    # configuration (`ai providers set`), not a hard-coded claude/codex pair --
    # otherwise doctor would keep reporting on the defaults even once a different
    # provider is configured. Falls back to the defaults outside a git repo, where
    # there is no repo.json yet to read a configuration from.
    builder_binary,reviewer_binary='claude','codex'
    try:
        probe_root=git_root(); probe_state=repo_state(probe_root)
        builder_binary=get_builder(probe_state).executable
        reviewer_binary=get_reviewer(probe_state).probe_binary or reviewer_binary
    except (SystemExit,ValueError): pass
    capability_binaries=[meta['binary'] for meta in CAPABILITIES.values()]
    for name in ['git','python3','node',builder_binary,reviewer_binary,*capability_binaries,'gh']:
        print(('✓' if shutil.which(name) else '·'),f'{name:10}', 'installed' if shutil.which(name) else 'missing')
    try:
        root=git_root(); state=repo_state(root); bad=contamination(root)
        print('\nRepository:',root); print('External state:',state)
        # An AI_STACK_HOME override decides which prompts and skills a run loads, so it
        # must never be invisible: doctor names the effective root whenever it is set.
        if os.environ.get('AI_STACK_HOME'): print('Stack assets:',STACK_ROOT,'(AI_STACK_HOME)')
        print('Base:', base_report(root,state))
        print('Zero-footprint:', 'PASS' if not bad else 'FAIL')
        for x in bad: print('  tracked:',x)
        state_files=['rules.json','lessons.json','patterns.json','validators.json',
            'prompt-overrides.json','prompt-experiments.json','skill-overrides.json',
            'context7-libraries.json','project-profile.json','project-deep-profile.json']
        corrupt=[name for name in state_files if json_file_health(state/name)=='corrupt']
        print('State files:', 'PASS' if not corrupt else f'CORRUPT ({len(corrupt)})')
        for name in corrupt: print('  corrupt:',state/name)
        # A repository moved its remote (ssh<->https, or an equivalent URL spelling)
        # before repo_state()'s migration existed, or has state left over under a
        # repo_id that no longer resolves for this root at all -- surface it rather
        # than let rules/validators/lessons/tasks sit invisibly unused.
        active_rid,_=remote_id(root)
        here=str(root.resolve())
        orphaned=[]
        for meta_path in known_repo_states():
            meta=load_json(meta_path,{})
            if not isinstance(meta,dict): continue
            if meta.get('last_root')==here and meta.get('repo_id') and meta.get('repo_id')!=active_rid:
                orphaned.append(meta_path.parent)
        if orphaned:
            print(f'Orphaned state: {len(orphaned)} director{"y" if len(orphaned)==1 else "ies"} '
                  'recorded for this repository under a different id (see `ai path`/remote URL history)')
            for d in orphaned: print('  ',d)
        metrics_file=state/'metrics.jsonl'
        if metrics_file.exists():
            row_count=sum(1 for _ in metrics_file.open())
            print('Metrics index:', metric_index_health(state))
            if row_count>METRICS_ADVISORY_ROWS:
                print(f'Metrics log:  {row_count} events (audited; queries use metrics.sqlite3); consider '
                      f'`ai metrics prune --older-than <window> --confirm` only for retention.')
    except SystemExit: pass


def cmd_optimize(args):
    # The optimization target is the installed stack's own bundled prompts,
    # never the work repo, so this needs no git context at all.
    targets=[STACK_ROOT/'templates/policy.md',STACK_ROOT/'templates/orchestration.yml']
    skill_prompts=list((STACK_ROOT/'skills').glob('*/prompt.md'))
    rows:list[tuple[str,int,int,int]]=[]
    for p in targets+skill_prompts:
        if not p.exists(): continue
        txt=p.read_text(); prompt_lines=[x.strip() for x in txt.splitlines() if x.strip()]
        dup=len(prompt_lines)-len(set(prompt_lines)); rows.append((str(p.relative_to(STACK_ROOT)),len(txt),len(prompt_lines),dup))
    print('Prompt/context optimization audit')
    for name,chars,line_count,dup in sorted(rows,key=lambda r:r[1],reverse=True):
        print(f"  {name:42} chars={chars:5} lines={line_count:3} duplicate_lines={dup}")
    print('\nPolicy: lazy skills; progressive context; evidence-first escalation; compact PASS outputs; hard per-profile budgets.')
    print('Repository modified: NO')


_RULE_ITEM=re.compile(r'(?m)^\s*(?:[-*]\s*(?:\[[ xX]?\]\s*)?|\d+[.)]\s+)(.+?)\s*$')
MIN_RULE_CHARS=12


def extract_rules(text:str)->list[str]:
    """Bullet and numbered lines from a conventions document, in order, de-duplicated.

    Deliberately dumb: headings, prose paragraphs and code fences are not rules, and a
    document's bullet list is the one shape that reliably *is* one. Anything this misses a
    human adds with `ai rules add`; anything it wrongly includes a human drops from the
    proposal, which is why importing is a two-step confirm rather than a direct write.
    """
    body=re.sub(r'(?ms)^```.*?^```\s*$','',text)
    items=[]
    for match in _RULE_ITEM.finditer(body):
        item=re.sub(r'\s+',' ',match.group(1)).strip().strip('*_`')
        if len(item)>=MIN_RULE_CHARS and item not in items: items.append(item)
    return items


def cmd_rules_import(args,state:Path,rules:list[dict]):
    """Turn a conventions/constitution document the repo already has into candidate rules.

    Reads the checkout, never writes to it: a project that keeps its conventions in
    CONTRIBUTING.md (or a spec-kit `.specify/memory/constitution.md`) should not have to
    retype them as `ai rules add` calls, but the rules that actually reach a prompt still
    land in external state, and still only after a human has seen the list.
    """
    require_human('Rule import')
    source=Path(args.file)
    if not source.is_file(): raise SystemExit(f'No such file: {source}')
    existing={r['rule'] for r in rules}
    candidates=[item for item in extract_rules(source.read_text()) if item not in existing]
    print(f'Rule import candidate: {source} (scope {args.scope})')
    if not candidates:
        print('  nothing new to import (no bullet/numbered lines, or all of them are already rules).')
        return
    for item in candidates: print(f'  + {item}')
    print(f'\n{len(candidates)} candidate rule(s). These are lines a regex matched, not conventions anyone verified.')
    if not args.confirm: raise SystemExit('Re-run with --confirm to add them.')
    now=int(time.time())
    rules.extend({"rule":item,"scope":args.scope,"source":"imported","source_file":str(source),
                  "confidence":0.5,"created_at":now} for item in candidates)
    save_json(state/'rules.json',rules)
    print(f'Added {len(candidates)} rule(s). Drop any that do not belong with `ai rules remove <index>`.')


def cmd_rules(args):
    root=git_root(); state=repo_state(root); p=state/'rules.json'; rules=load_json(p,[])
    if args.rules_cmd=='import': return cmd_rules_import(args,state,rules)
    if args.rules_cmd in (None,'list'):
        if not rules: print('No repository-specific rules.'); return
        for i,r in enumerate(rules,1): print(f"{i}. [{r.get('scope','**')}] {r['rule']}")
    elif args.rules_cmd=='add':
        rules.append({"rule":args.rule,"scope":args.scope,"source":"user","confidence":1.0,"created_at":int(time.time())}); save_json(p,rules); print('Rule added.')
    elif args.rules_cmd=='remove':
        idx=args.index-1
        if idx<0 or idx>=len(rules): raise SystemExit('Invalid rule index.')
        old=rules.pop(idx); save_json(p,rules); print('Removed:',old['rule'])
