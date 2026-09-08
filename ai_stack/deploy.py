from __future__ import annotations
import fnmatch, hashlib, json, os, re, shutil, time
from pathlib import Path
from typing import Any
from uuid import uuid4
from core import git_root, load_json, profile_repo, repo_state, require_human, run, safe_head, save_json, task_state
from metrics import record_metric
from validators import run_codex_json
from workflow import execute


TARGET_RE = re.compile(r'[a-z][a-z0-9_-]{0,31}')


def detect_deploy(root:Path)->dict:
    # Heuristic, offline, tracked-files-only detection - no network, no model call.
    # Mirrors profile_repo()'s use of `git ls-files` so untracked/ignored scratch
    # files (build output, local .env) never influence the result.
    try: files=run(["git","ls-files"],cwd=root).splitlines()
    except Exception: files=[]
    fset=set(files)
    docker=[f for f in files if f=='Dockerfile' or f.endswith('/Dockerfile') or fnmatch.fnmatch(f,'docker-compose*.y*ml')]
    ci_cd=sorted(f for f in files if f.startswith('.github/workflows/') and f.endswith(('.yml','.yaml')))
    paas=[f for f in ('Procfile','fly.toml','render.yaml','vercel.json','netlify.toml','app.yaml','now.json','serverless.yml','serverless.yaml') if f in fset]
    infra=sorted(f for f in files if f.endswith('.tf') or f.startswith(('k8s/','kubernetes/','manifests/','helm/','charts/')))
    scripts=sorted(f for f in files if re.search(r'(^|/)deploy[^/]*\.(sh|py|js|ts|rb)$',f,re.I) or (f=='Makefile' and 'deploy' in (root/f).read_text(errors='ignore').lower()))
    docs=[]
    for f in files:
        if f=='README.md' or (f.startswith('docs/') and f.endswith('.md')):
            text=(root/f).read_text(errors='ignore')
            if re.search(r'(?im)^#+\s*deploy',text): docs.append(f)
    return {'docker':docker,'ci_cd':ci_cd,'paas':paas,'infra':infra,'scripts':scripts,'docs':docs}


RUNBOOK_SCHEMA: dict[str, Any] = {
    'type':'object','additionalProperties':False,
    'required':['summary','prerequisites','dev_steps','verification','rollback','confidence_caveats','suggested_dev_command'],
    'properties':{
        'summary':{'type':'string'},
        'prerequisites':{'type':'array','items':{'type':'string'}},
        'dev_steps':{'type':'array','items':{'type':'string'}},
        'verification':{'type':'array','items':{'type':'string'}},
        'rollback':{'type':'array','items':{'type':'string'}},
        'confidence_caveats':{'type':'array','items':{'type':'string'}},
        'suggested_dev_command':{'type':'array','items':{'type':'string'}},
    },
}


def check_runbook(value: Any) -> dict[str, Any]:
    required=RUNBOOK_SCHEMA['required']
    if not isinstance(value,dict) or set(value)!=set(required): raise ValueError('Runbook response does not match the required fields.')
    if not isinstance(value['summary'],str) or not value['summary'].strip(): raise ValueError('Runbook summary must be nonempty.')
    for key in ('prerequisites','dev_steps','verification','rollback','confidence_caveats','suggested_dev_command'):
        if not isinstance(value[key],list) or not all(isinstance(x,str) and x.strip() for x in value[key]):
            raise ValueError(f'Invalid {key}.')
    return value


def validate_deploy_config(config):
    if not isinstance(config,dict) or config.get('version')!=1: raise ValueError('Deploy configuration requires version 1.')
    targets=config.get('targets')
    if not isinstance(targets,dict): raise ValueError('targets must be an object.')
    for name,item in targets.items():
        if not TARGET_RE.fullmatch(name) or not isinstance(item,dict): raise ValueError(f'Invalid target: {name}')
        command=item.get('command')
        if not isinstance(command,list) or not command or not all(isinstance(x,str) and x for x in command):
            raise ValueError(f'{name}: command must be a nonempty array of strings.')
        timeout=item.get('timeout')
        if type(timeout) is not int or timeout<=0: raise ValueError(f'{name}: timeout must be a positive integer.')
        if not (isinstance(item.get('evidence'),str) and item['evidence'].strip()):
            raise ValueError(f'{name}: evidence description is required.')
    return config


def deploy_config(state):
    path=state/'deploy.json'
    try:
        config=json.loads(path.read_text()) if path.exists() else {'version':1,'targets':{}}
        return validate_deploy_config(config)
    except (OSError,ValueError) as exc:
        raise SystemExit(f'Invalid deploy configuration: {exc}') from exc


def render_runbook(runbook:dict)->str:
    def section(title,items):
        body='\n'.join(f'- {x}' for x in items) if items else '(none)'
        return f'## {title}\n\n{body}\n'
    lines=["# Deploy runbook\n", runbook['summary'], '',
        section('Prerequisites',runbook['prerequisites']),
        section('Deploy to dev',runbook['dev_steps']),
        section('Verification',runbook['verification']),
        section('Rollback',runbook['rollback'])]
    command=runbook['suggested_dev_command']
    if command:
        lines.append('## Suggested dev deploy command\n')
        lines.append('```text\n'+' '.join(command)+'\n```\n')
        lines.append('Review the command above, then declare it explicitly:\n')
        lines.append('```text\nai deploy set --evidence "..." dev -- '+' '.join(command)+'\n```\n')
    else:
        lines.append('## Suggested dev deploy command\n')
        lines.append('No readable dev deploy command was found in this repository.\n')
    lines.append(section('Caveats',runbook['confidence_caveats']))
    lines.append('AI-generated, verify before relying on it.\n')
    return '\n'.join(lines)


def _plan(args,root,state):
    if not (state/'project-profile.json').exists(): profile_repo(root,state)
    profile=load_json(state/'project-profile.json',{})
    found=detect_deploy(root)
    runbook_path=state/'deploy-runbook.json'
    current_commit=safe_head(root)
    existing=load_json(runbook_path,None)
    if existing and not args.refresh and existing.get('analyzed_commit')==current_commit:
        print(f"Deploy runbook up to date (commit {current_commit[:12]}); use --refresh to force.")
        print('Runbook:',runbook_path); print('Markdown:',state/'deploy-runbook.md'); return
    executable=shutil.which('codex')
    if not executable: raise SystemExit('Codex CLI missing. Install/authenticate Codex to generate a deploy runbook.')
    deep=load_json(state/'project-deep-profile.json',{})
    prompt=f'''Read this repository read-only and produce a deploy runbook for the **dev** environment only.
Do not modify files, run any command that writes, or invent commands/credentials that are
not actually present in the repository. Focus exclusively on getting a change running in dev,
not staging or production. If you cannot find a genuine, readable path to deploy this repository
to dev, return an empty suggested_dev_command and explain why in confidence_caveats — do not guess.

Repository: {root}
Already-detected deployment tooling (heuristic, tracked files only): {json.dumps(found)}
Project profile: {json.dumps(profile)}
Deployment notes from a prior deep profile, if any: {json.dumps(deep.get('deployment','')) if deep else '"none"'}
'''
    try:
        runbook=run_codex_json(executable,root,state/'review','deploy-runbook',prompt,RUNBOOK_SCHEMA,check_runbook,timeout=args.timeout)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    runbook={k:runbook[k] for k in RUNBOOK_SCHEMA['required']}
    runbook.update(version=1,generated_at=int(time.time()),analyzed_commit=current_commit,
                   caveat='AI-generated interpretation of the repository; verify before relying on it for critical decisions.')
    save_json(runbook_path,runbook)
    md=state/'deploy-runbook.md'; md.write_text(render_runbook(runbook))
    print('Runbook:',runbook_path); print('Markdown:',md)


def _show(state):
    config=deploy_config(state)
    targets=config['targets']
    if not targets: print('No deploy targets configured.')
    for name,item in sorted(targets.items()):
        print(f'{name}: {json.dumps(item)}')
        record=load_json(state/'deploy'/f'{name}.json',{})
        if record:
            print(f"  last run: {'succeeded' if record.get('succeeded') else 'failed'} "
                  f"(exit {record.get('exit_code')}, commit {str(record.get('commit'))[:12]}, {record.get('log')})")
    runbook=load_json(state/'deploy-runbook.json',{})
    if runbook:
        print('Runbook:',state/'deploy-runbook.md'); print('Summary:',runbook.get('summary',''))


def _set(args,state):
    config=deploy_config(state)
    command=args.command[1:] if args.command[:1]==['--'] else args.command
    config['targets'][args.target]={'command':command,'timeout':args.timeout,'evidence':args.evidence,'description':args.description}
    try: validate_deploy_config(config)
    except ValueError as exc: raise SystemExit(str(exc)) from exc
    save_json(state/'deploy.json',config)
    print(json.dumps(config,indent=2)); print('Saved:',state/'deploy.json')


def _remove(args,state):
    config=deploy_config(state)
    config['targets'].pop(args.target,None)
    save_json(state/'deploy.json',config)
    print(json.dumps(config,indent=2)); print('Saved:',state/'deploy.json')


def _run(args,root,state):
    config=deploy_config(state)
    item=config['targets'].get(args.target)
    if item is None:
        raise SystemExit(f"No target named '{args.target}'. Configure one first: ai deploy set --evidence \"...\" {args.target} -- <cmd>")
    commit=safe_head(root)
    dirty=bool(run(["git","status","--porcelain"],cwd=root,check=False))
    readiness=load_json(task_state(state)/'state/readiness.json',{}).get('status','unknown')
    print('Target:',args.target)
    print('Command:',' '.join(item['command']))
    print('Cwd:',root)
    print('Timeout:',item['timeout'])
    print('Commit:',commit)
    print('Evidence expected:',item['evidence'])
    if dirty: print('Warning: working tree has uncommitted changes.')
    print('Readiness:',readiness)
    if not args.execute:
        print('Dry run: nothing executed. Re-run with --execute to deploy.')
        return
    require_human(f'Deploying to {args.target}')
    directory=state/'deploy'; directory.mkdir(parents=True,exist_ok=True)
    log=directory/f'{args.target}-{int(time.time())}-{uuid4().hex[:8]}.log'
    env=dict(os.environ,AI_DEPLOY_TARGET=args.target,AI_DEPLOY_COMMIT=commit,AI_REPO_STATE=str(state))
    started=time.monotonic()
    with log.open('w') as out:
        code=execute(item['command'],root,env,out,item['timeout'])
    duration=round(time.monotonic()-started,3)
    record={'version':1,'target':args.target,'command':item['command'],'succeeded':code==0,'exit_code':code,
        'evidence':item['evidence'] if code==0 else None,'commit':commit,'dirty':dirty,'readiness':readiness,
        'duration_seconds':duration,'log':str(log),'log_hash':hashlib.sha256(log.read_bytes()).hexdigest(),'created_at':time.time()}
    save_json(directory/f'{args.target}.json',record)
    record_metric(state,'deploy',target=args.target,succeeded=code==0,exit_code=code,duration_seconds=duration,dirty=dirty)
    print(f"{args.target}: {'DEPLOYED' if code==0 else 'FAILED'} (exit {code}, {duration}s) | {log}")
    if code!=0: raise SystemExit(1)


def cmd_deploy(args):
    root=git_root(); state=repo_state(root)
    sub=getattr(args,'deploy_cmd',None)
    if sub is None or sub=='detect':
        found=detect_deploy(root)
        print('Deployment detection (local heuristic; tracked files only, no network, no model call)')
        labels=[('Docker','docker'),('CI/CD workflows','ci_cd'),('PaaS/serverless config','paas'),
                ('Infra as code / k8s manifests','infra'),('Deploy scripts','scripts'),('Docs with a Deploy section','docs')]
        any_found=False
        for label,key in labels:
            items=found[key]
            if items:
                any_found=True
                print(f'  {label}:')
                for f in items: print('    -',f)
            else:
                print(f'  {label}: none detected')
        if not any_found:
            print('\nNo deployment tooling or documentation detected in tracked files.')
        return
    if sub=='plan': return _plan(args,root,state)
    if sub=='show': return _show(state)
    if sub=='set': return _set(args,state)
    if sub=='remove': return _remove(args,state)
    if sub=='run': return _run(args,root,state)
