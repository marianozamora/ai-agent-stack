from __future__ import annotations
import fnmatch, json, os, re, shutil, subprocess, time
from pathlib import Path
from core import git_root, load_json, profile_repo, repo_state, run, save_json, shasum


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


def cmd_deploy(args):
    root=git_root(); found=detect_deploy(root)
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
