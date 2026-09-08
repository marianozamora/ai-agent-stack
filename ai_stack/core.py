from __future__ import annotations
import hashlib, json, os, re, subprocess, sys, tempfile, time
from pathlib import Path
from typing import Any
from workflow import ORDER


TASK_ID = None


_SOURCE_ROOT = Path(__file__).resolve().parents[1]
_BUNDLED_ROOT = Path(__file__).resolve().parent/'_bundle'
STACK_ROOT = _SOURCE_ROOT if (_SOURCE_ROOT/'VERSION').is_file() else _BUNDLED_ROOT


VERSION = (STACK_ROOT/"VERSION").read_text().strip()


CONFIG_ROOT = Path(os.environ.get("XDG_CONFIG_HOME", Path.home()/".config")) / "ai-agent-stack"


AI_PATTERNS = [
    ".ai-review/", "ai-agent-stack/", "CLAUDE.local.ai.md", "AGENTS.local.ai.md",
    "graphify-out/", ".context7/", ".code-review-graph/"
]


GATES = ('checks','regression','cleanup','provenance','ponytail','summary','contract','review','security','design')


def run(cmd:list[str], cwd:Path|None=None, check=True, capture=True, env=None)->str:
    p=subprocess.run(cmd,cwd=cwd,text=True,stdout=subprocess.PIPE if capture else None,
                     stderr=subprocess.PIPE if capture else None,check=False,env=env)
    if check and p.returncode:
        msg=(p.stderr or p.stdout or "command failed").strip()
        raise RuntimeError(msg)
    return (p.stdout or "").strip()


def git_root()->Path:
    try: return Path(run(["git","rev-parse","--show-toplevel"]))
    except Exception as exc: raise SystemExit("Not inside a git repository.") from exc


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
        # Merge, never clobber: repo.json now carries durable per-repo settings
        # (default_base) that a plain rewrite on every repo_state() call would discard.
        meta=load_json(d/"repo.json",{})
        if not isinstance(meta,dict): meta={}
        meta.update({"version":1,"repo_id":rid,"remote":remote,"last_root":str(root),"updated_at":int(time.time())})
        (d/"repo.json").write_text(json.dumps(meta,indent=2,sort_keys=True)+"\n")
        if not (d/"rules.json").exists(): (d/"rules.json").write_text("[]\n")
        if not (d/"lessons.json").exists(): (d/"lessons.json").write_text("[]\n")
        if not (d/"context7-libraries.json").exists(): (d/"context7-libraries.json").write_text("{}\n")
        if not (d/"skill-overrides.json").exists(): (d/"skill-overrides.json").write_text("{}\n")
    return d


def shasum(s:str)->str: return hashlib.sha256(s.encode()).hexdigest()


def load_json(p:Path, default):
    try: return json.loads(p.read_text())
    except Exception: return default


def json_file_health(p:Path)->str:
    """'missing' | 'ok' | 'corrupt' for one JSON state file.

    load_json() itself stays lenient on purpose — it has ~40 call sites across
    every module, all written assuming it never raises, so changing its
    contract would ripple everywhere for no real benefit. This is the
    detection path `ai doctor` uses instead: a corrupt rules.json/lessons.json/
    validators.json currently degrades silently to load_json's default (often
    `{}`/`[]`), which looks identical to "nothing configured yet" — this makes
    that distinguishable and visible.
    """
    if not p.is_file(): return 'missing'
    try:
        json.loads(p.read_text())
        return 'ok'
    except Exception:
        return 'corrupt'


def save_json(p:Path,obj:Any):
    with tempfile.NamedTemporaryFile(mode='w', dir=p.parent, delete=False) as f:
        tmp=Path(f.name)
        try:
            f.write(json.dumps(obj,indent=2,sort_keys=True)+"\n")
            f.close()
            tmp.replace(p)
        finally:
            tmp.unlink(missing_ok=True)


TASK_ID_PATTERN = r'[A-Za-z0-9][A-Za-z0-9._/-]{0,199}'
_BRANCH_FALLBACK_WARNED = False


def active_task_id(state:Path,root:Path)->str:
    """The task `ai start`/`ai switch` made active for this checkout.

    Keyed by absolute root, not by repository: worktrees share one repo_state but
    must never share a task, which is the same invariant the task key itself keeps.
    """
    entry=load_json(state/'active-task.json',{}).get('active',{}).get(str(root.resolve()))
    return entry.get('id','') if isinstance(entry,dict) else ''


def set_active_task(state:Path,root:Path,identity:str|None):
    data=load_json(state/'active-task.json',{})
    active:dict[str,Any]=data['active'] if isinstance(data,dict) and isinstance(data.get('active'),dict) else {}
    key=str(root.resolve())
    if identity: active[key]={'id':identity,'started_at':int(time.time())}
    else: active.pop(key,None)
    save_json(state/'active-task.json',{'version':1,'active':active})


def task_identity(state:Path,root:Path)->str:
    """--task-id > AI_TASK_ID (set by ai gate) > the active task > the branch.

    The branch fallback is what let two unrelated tickets worked on one branch share
    a contract and its stale acceptance criteria. It is kept for one release so
    existing checkouts keep working, but it warns once per process and is deprecated.
    """
    global _BRANCH_FALLBACK_WARNED
    identity=TASK_ID or os.environ.get('AI_TASK_ID') or active_task_id(state,root)
    if identity: return identity
    branch=run(['git','symbolic-ref','--short','HEAD'],cwd=root,check=False) or safe_head(root)
    if branch and not _BRANCH_FALLBACK_WARNED:
        _BRANCH_FALLBACK_WARNED=True
        print(f'warning: no active task; using the branch {branch!r} as the task identity. '
              'Run `ai start <id>` to make it explicit (this fallback is deprecated).',file=sys.stderr)
    return branch


def task_state(state:Path)->Path:
    root=git_root()
    identity=task_identity(state,root)
    if not re.fullmatch(TASK_ID_PATTERN,identity):
        raise SystemExit('Invalid task ID.')
    # Worktrees sharing a remote must never share mutable task artifacts.
    key=shasum(str(root.resolve())+'\0'+identity)[:24]
    d=state/'tasks'/key
    for sub in ('state','contracts','handoffs','review','gates'): (d/sub).mkdir(parents=True,exist_ok=True)
    # Merge: task.json carries lifecycle fields (status, title, created_at) that the
    # old unconditional rewrite would have erased on the next command.
    meta=load_json(d/'task.json',{})
    if not isinstance(meta,dict): meta={}
    meta.setdefault('status','active'); meta.setdefault('created_at',int(time.time()))
    meta.update(id=identity,root=str(root.resolve()),key=key)
    save_json(d/'task.json',meta)
    return d


def safe_head(root:Path)->str:
    try:return run(["git","rev-parse","HEAD"],cwd=root)
    except:return ""


def enforce_budget(text:str,limit:int,label:str):
    if len(text)>limit:
        raise SystemExit(f'NEEDS_HUMAN: {label} exceeds budget ({len(text)} > {limit} characters). Shorten input or select a larger profile.')


def require_human(action:str):
    """Refuse when running inside a gate/validator's own environment.

    One implementation for the "curation is human-only" invariant, so a model running
    inside a gate can never confirm/inject/retire its own future prompt context (lessons)
    or promote its own validator's prompt (experiments) — shared by every caller instead
    of each command re-typing the same env check, which drifted out of sync once already.
    """
    if os.environ.get('AI_GATE') or os.environ.get('AI_TASK_DIR'):
        raise SystemExit(f'{action} is human-only; run this outside a gate/validator environment.')


def contamination(root:Path)->list[str]:
    tracked=run(["git","ls-files"],cwd=root,check=False).splitlines(); bad=[]
    for f in tracked:
        if f.startswith('.ai-review/') or f.startswith('graphify-out/') or f.startswith('.code-review-graph/') or f in ('CLAUDE.local.ai.md','AGENTS.local.ai.md'):
            bad.append(f)
    return bad


def profile_repo(root:Path,state:Path)->dict:
    ext_map={'.ts':'TypeScript','.tsx':'TypeScript/React','.js':'JavaScript','.jsx':'JavaScript/React','.py':'Python','.java':'Java','.kt':'Kotlin','.go':'Go','.rs':'Rust','.rb':'Ruby','.php':'PHP','.cs':'C#','.swift':'Swift'}
    try: rels=run(["git","ls-files"],cwd=root).splitlines()
    except Exception: rels=[]
    counts:dict[str,int]={}
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


BASE_CANDIDATES = ('main','master','develop','trunk')


def verify_ref(root:Path,ref:str)->bool:
    """True when `ref` names a commit that exists in this repository."""
    if not ref or ref.startswith('-'): return False
    return bool(run(["git","rev-parse","--verify","--quiet",ref+"^{commit}"],cwd=root,check=False))


def origin_head(root:Path)->str:
    """The remote's own default branch, when origin/HEAD is set locally."""
    ref=run(["git","symbolic-ref","--quiet","--short","refs/remotes/origin/HEAD"],cwd=root,check=False)
    return ref if verify_ref(root,ref) else ''


def base_suggestions(root:Path,requested:str|None=None)->list[str]:
    """Refs this repository actually has, best guess first."""
    out:list[str]=[]
    def add(ref:str):
        if ref and ref not in out and verify_ref(root,ref): out.append(ref)
    # A local name that only exists on the remote is by far the most common near-miss.
    if requested and '/' not in requested: add('origin/'+requested)
    add(origin_head(root))
    for name in BASE_CANDIDATES: add('origin/'+name); add(name)
    return out


def base_error(root:Path,base:str,*,explicit:bool)->str:
    suggestions=base_suggestions(root,base)
    origin=('--base '+base) if explicit else f'the stored default base ({base})'
    lines=[f'FAILED: base ref {base!r} does not exist in this repository.',
           f'It came from {origin}; nothing is assumed in its place.']
    if suggestions:
        lines.append('Refs detected here: '+', '.join(suggestions[:5]))
        lines.append(f'Try: --base {suggestions[0]}   (persist it with: ai init --base {suggestions[0]})')
    else:
        lines.append('No usable base ref was detected. Pass an existing branch, tag or commit with --base.')
    return '\n'.join(lines)


def resolve_base(root:Path,base:str|None,state:Path|None=None)->str:
    """Resolve the diff base fail-closed; never degrade an unknown base to HEAD.

    A base that silently became HEAD produced an empty scope, which drove classify()
    to LOW risk, dropped the review gate out of required_gates() and let `ai ready`
    certify PR_READY over a diff no gate had ever seen. Any base that cannot be
    verified is now an error naming the refs this repository really has.
    """
    if base:
        if verify_ref(root,base): return base
        raise SystemExit(base_error(root,base,explicit=True))
    stored=load_json((state or Path())/'repo.json',{}).get('default_base') if state else None
    if stored:
        if verify_ref(root,stored): return stored
        raise SystemExit(base_error(root,stored,explicit=False))
    detected=base_suggestions(root)
    if detected: return detected[0]
    raise SystemExit('FAILED: no base ref could be detected (looked for origin/HEAD, '
                     +', '.join(BASE_CANDIDATES)+').\nPass an existing branch, tag or commit with --base.')


def collect_scope(root:Path,base:str)->dict:
    # `base` must already be resolved by resolve_base(); this guard keeps the old
    # silent degradation to HEAD from ever creeping back in through a new call site.
    if not verify_ref(root,base): raise SystemExit(base_error(root,base,explicit=True))
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


def classify_task(task:str,figma:str|None=None)->str:
    t=(task or '').lower()
    if figma or 'figma.com/' in t: return 'design'
    if re.search(r'\b(bug|regression|exception|crash|failing|fails|failure|broken|incorrect|timeout|403|401|500)\b',t): return 'bug'
    if re.search(r'\b(migrate|migration|redesign|architecture|refactor|multi[- ]repo|rewrite|replace|deprecate)\b',t): return 'architecture'
    if re.search(r'\b(prototype|spike|proof of concept|poc|feasibility|can we|whether)\b',t): return 'prototype'
    if re.search(r'\b(ticket|tickets|epic|break down|decompose|roadmap)\b',t): return 'planning'
    return 'feature'


def context_caps(profile:str)->dict:
    return {
      'fast': {
        'raw_files':4,'review_files':5,'agent_calls':3,'reviews':0,'docs_queries':1,'graph_queries':2,
        'skills':1,'findings':3,'context_chars':12000,'handoff_chars':3500,'retries':1,'usage_tokens':40000
      },
      'standard': {
        'raw_files':8,'review_files':10,'agent_calls':5,'reviews':1,'docs_queries':3,'graph_queries':4,
        'skills':2,'findings':3,'context_chars':24000,'handoff_chars':5500,'retries':2,'usage_tokens':120000
      },
      'strict': {
        'raw_files':12,'review_files':15,'agent_calls':7,'reviews':1,'docs_queries':5,'graph_queries':6,
        'skills':3,'findings':5,'context_chars':36000,'handoff_chars':7500,'retries':2,'usage_tokens':250000
      }
    }[profile]


def required_gates(root,plan):
    risk=classify(collect_scope(root,plan['scope']['base']),plan['profile'])
    required=list(GATES[:7])
    if plan['profile']!='fast' or risk['risk']!='LOW' or plan['risk']['risk']!='LOW': required.append('review')
    if risk['security'] or plan['risk']['security']: required.append('security')
    if plan.get('figma'): required.append('design')
    return [name for name in ORDER if name in required]
