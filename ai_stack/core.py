from __future__ import annotations
import contextlib, hashlib, json, os, re, subprocess, sys, tempfile, time
from pathlib import Path
from typing import Any
from workflow import ORDER


TASK_ID = None


_SOURCE_ROOT = Path(__file__).resolve().parents[1]
_BUNDLED_ROOT = Path(__file__).resolve().parent/'_bundle'


def resolve_stack_root(override:str|None,source:Path,bundled:Path)->Path:
    """Where the shipped, read-only assets live: VERSION, templates/ and skills/.

    AI_STACK_HOME lets those come from somewhere other than the directory cli.py
    happens to sit in -- a read-only install, or a test that needs its own
    templates/ instead of writing prompt variants into the source tree it is
    running from.

    Deliberately NOT named AI_STACK_ROOT: that name is already taken by
    [tool.coverage.run] in pyproject.toml and exported by CI's coverage job,
    where it rides into every CLI subprocess through the inherited environment.
    Reusing it would silently couple coverage runs to asset resolution, and would
    pass CI unnoticed because there the two paths happen to coincide.

    An override with no VERSION file is an error rather than a quiet fall back to
    the source tree: a typo that kept working would hide which prompts and skills
    a run actually loaded, and those feed the model directly.
    """
    if not override: return source if (source/'VERSION').is_file() else bundled
    root=Path(override).expanduser().resolve()
    if not (root/'VERSION').is_file():
        raise SystemExit(f'AI_STACK_HOME={override} is not an ai-agent-stack install root '
                         '(no VERSION file there).')
    return root


STACK_ROOT = resolve_stack_root(os.environ.get('AI_STACK_HOME'),_SOURCE_ROOT,_BUNDLED_ROOT)


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


_GIT_ROOT_CACHE:dict[str,Path] = {}


def git_root()->Path:
    # Keyed by cwd, never a single slot: the test suite chdirs between throwaway
    # repos inside one process, and a global cache would hand the second repo the
    # first one's root. Every command calls this at least twice (~16ms a spawn),
    # and a process's cwd->toplevel mapping cannot change under it. Failures are
    # deliberately not cached: a caller that recovers by chdir'ing must re-probe.
    key=os.getcwd()
    cached=_GIT_ROOT_CACHE.get(key)
    if cached is not None: return cached
    try: root=Path(run(["git","rev-parse","--show-toplevel"]))
    except Exception as exc: raise SystemExit("Not inside a git repository.") from exc
    _GIT_ROOT_CACHE[key]=root
    return root


@contextlib.contextmanager
def temp_worktree(root:Path,ref:str):
    """A detached, disposable worktree checked out at `ref`.

    Lets a command review a target (a commit, a fetched PR head) without ever
    touching the caller's own working tree or branch. Shares the same object
    database as `root`, so nothing needs re-cloning or re-fetching for content
    already reachable from `root`. Always removed on exit, even on error.
    """
    with tempfile.TemporaryDirectory(prefix='ai-review-target-') as directory:
        path=Path(directory)/'checkout'
        run(["git","worktree","add","--detach",str(path),ref],cwd=root)
        try:
            yield path
        finally:
            run(["git","worktree","remove","--force",str(path)],cwd=root,check=False)


_SCP_LIKE_REMOTE = re.compile(r'^(?:[\w.-]+@)?([\w.-]+):(.+)$')
_URL_REMOTE = re.compile(r'^([a-zA-Z][a-zA-Z0-9+.-]*)://(?:[^@/]+@)?([^/:]+)(?::(\d+))?(/.*)?$')
_DEFAULT_PORTS = {'https':'443','http':'80','ssh':'22','git':'9418'}


def _legacy_normalize_remote(remote:str)->str:
    """The original remote_id() normalization: strip only a trailing `.git`.

    Kept solely so repo_state() can detect and migrate state a prior release
    keyed by it -- never used to compute a *new* id.
    """
    return re.sub(r"\.git$","",remote.strip())


def normalize_remote(remote:str)->str:
    """Collapse equivalent remote URLs to the same `host/path` string.

    `git@github.com:x/y.git`, `git@github.com:x/y`, `https://github.com/x/y.git`
    and `ssh://git@github.com:22/x/y` all normalize to `github.com/x/y`, so a
    `git remote set-url` between protocols (routine: ssh<->https) or an
    equivalent URL spelling never silently orphans a repository's recorded
    rules/validators/lessons/tasks under a different id. Only the host is
    lowercased -- a repository path can be case-sensitive on some hosts, and
    lowercasing it would risk colliding two distinct repositories into one id.
    A path with no scheme and no `user@host:` form (a local filesystem path,
    the fallback when there is no `origin`) passes through unchanged.
    """
    s=re.sub(r"\.git$","",remote.strip())
    m=_URL_REMOTE.match(s)
    if m:
        scheme,host,port,path=m.groups()
        if port and port==_DEFAULT_PORTS.get(scheme.lower()): port=None
        s=host.lower()+((':'+port) if port else '')+'/'+((path or '').lstrip('/'))
        return s.rstrip('/')
    m=_SCP_LIKE_REMOTE.match(s)
    if m:
        host,path=m.groups()
        return (host.lower()+'/'+path.lstrip('/')).rstrip('/')
    return s.rstrip('/') or s


_ORIGIN_URL_CACHE:dict[str,str] = {}
_REMOTE_ID_CACHE:dict[str,tuple[str,str]] = {}
# Both keyed by resolved root, never a single global slot: several temp repos
# can share one test process, and each must keep its own origin/id.


def _origin_url(root:Path)->str:
    """Raw `git remote get-url origin` output (or the filesystem-path fallback
    when there is no origin), cached per-root. remote_id() and _legacy_remote_id()
    both start from this same value, and repo_state() calls both of them on
    every invocation -- reading it twice per call for something that can't
    change mid-process was paying the ~16ms spawn twice for nothing.
    """
    key=str(root.resolve())
    cached=_ORIGIN_URL_CACHE.get(key)
    if cached is not None: return cached
    try: remote=run(["git","remote","get-url","origin"],cwd=root)
    except Exception: remote=str(root.resolve())
    _ORIGIN_URL_CACHE[key]=remote
    return remote


def remote_id(root:Path)->tuple[str,str]:
    # repo_state() calls this on every command (`ai doctor` alone calls it 5x);
    # the remote doesn't change under a running process, so re-deriving it each
    # time bought nothing but repeated ~16ms `git remote get-url` spawns.
    key=str(root.resolve())
    cached=_REMOTE_ID_CACHE.get(key)
    if cached is not None: return cached
    normalized=normalize_remote(_origin_url(root))
    rid=hashlib.sha256(normalized.encode()).hexdigest()[:16]
    result=(rid,normalized)
    _REMOTE_ID_CACHE[key]=result
    return result


def _legacy_remote_id(root:Path)->tuple[str,str]:
    normalized=_legacy_normalize_remote(_origin_url(root))
    return hashlib.sha256(normalized.encode()).hexdigest()[:16], normalized


def _clear_caches():
    """Test-only escape hatch for a repo whose origin remote changes mid-process
    (same root, new URL): without this, remote_id()/_legacy_remote_id() would
    keep returning the stale cached values for that root. Production code never
    needs to call it -- the remote a process started against doesn't change out
    from under it."""
    _ORIGIN_URL_CACHE.clear(); _REMOTE_ID_CACHE.clear(); _GIT_ROOT_CACHE.clear(); _TASK_SCAFFOLD_DONE.clear()


def known_repo_states()->list[Path]:
    """Every repo.json this installation has ever recorded, for cross-repository
    checks like `ai doctor`'s orphaned-state detection. Reads CONFIG_ROOT fresh on
    each call (not a module-level constant capture) so tests that patch it see the
    effect. Order is not meaningful."""
    d=CONFIG_ROOT/'repos'
    return sorted(d.glob('*/repo.json')) if d.is_dir() else []


def repo_state(root:Path|None=None, create=True)->Path:
    root=root or git_root(); rid,remote=remote_id(root)
    d=CONFIG_ROOT/"repos"/rid
    migrated_from=None
    if create:
        # A `git remote set-url` between ssh and https (routine) or an equivalent
        # URL spelling used to hash to a different repo_id, silently orphaning
        # every rule/validator/lesson/task recorded under the old one. If this
        # repository's state still lives only under the id a prior release would
        # have computed, move it forward once rather than starting empty.
        legacy_rid,_=_legacy_remote_id(root)
        legacy_dir=CONFIG_ROOT/"repos"/legacy_rid
        if legacy_rid!=rid and legacy_dir.is_dir():
            if not d.is_dir():
                legacy_dir.rename(d); migrated_from=legacy_rid
                print(f'notice: migrated repository state from {legacy_rid} to {rid} '
                     '(remote URL normalization). Run `ai doctor` if anything looks missing.',
                     file=sys.stderr)
            else:
                print(f'notice: repository state exists under both {legacy_rid} (legacy) and '
                     f'{rid} (current) for this remote; not merged automatically.\n'
                     f'  legacy:  {legacy_dir}\n  current: {d}',file=sys.stderr)
        for sub in ["contracts","state","graphify","docs-cache","code-review-graph","review","task-cache","handoffs","skill-state"]: (d/sub).mkdir(parents=True,exist_ok=True)
        # Merge, never clobber: repo.json now carries durable per-repo settings
        # (default_base) that a plain rewrite on every repo_state() call would discard.
        meta=load_json(d/"repo.json",{})
        if not isinstance(meta,dict): meta={}
        if migrated_from:
            history:list[Any]=meta['migrated_from'] if isinstance(meta.get('migrated_from'),list) else []
            if migrated_from not in history: history=[*history,migrated_from]
            meta['migrated_from']=history
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
_TASK_SCAFFOLD_DONE:set[tuple[str,str]] = set()


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
    """--task-id > AI_TASK_ID (set by `ai gate`) > the active task.

    There is deliberately no branch fallback. Deriving identity from the current
    branch is what let two unrelated tickets worked on one branch share a contract
    and its stale acceptance criteria; it was deprecated with a warning for one
    release and is now gone. `ai start <id>` makes the choice explicit.
    """
    identity=TASK_ID or os.environ.get('AI_TASK_ID') or active_task_id(state,root)
    if identity: return identity
    raise SystemExit('NEEDS_HUMAN: no active task in this checkout. Run `ai start <id>` to begin '
                     'one, or pass --task-id (or set AI_TASK_ID) for a one-off command.')


def task_state(state:Path)->Path:
    root=git_root()
    identity=task_identity(state,root)
    if not re.fullmatch(TASK_ID_PATTERN,identity):
        raise SystemExit('Invalid task ID.')
    # Worktrees sharing a remote must never share mutable task artifacts.
    key=shasum(str(root.resolve())+'\0'+identity)[:24]
    d=state/'tasks'/key
    # The scaffolding below is idempotent, so redoing it per call bought nothing but
    # five mkdirs and an atomic task.json rewrite each time. The identity above is
    # still resolved live on every call -- `ai start`/`ai switch` change it mid-process,
    # and a cache that skipped that would hand back the previous task's directory.
    if (str(state),key) in _TASK_SCAFFOLD_DONE: return d
    for sub in ('state','contracts','handoffs','review','gates'): (d/sub).mkdir(parents=True,exist_ok=True)
    # Merge: task.json carries lifecycle fields (status, title, created_at) that the
    # old unconditional rewrite would have erased on the next command.
    meta=load_json(d/'task.json',{})
    if not isinstance(meta,dict): meta={}
    meta.setdefault('status','active'); meta.setdefault('created_at',int(time.time()))
    meta.update(id=identity,root=str(root.resolve()),key=key)
    save_json(d/'task.json',meta)
    _TASK_SCAFFOLD_DONE.add((str(state),key))
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
EMPTY_TREE = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'  # git's well-known empty-tree object,
# present in every repository with no ref pointing to it; the base a root commit
# (no parent to diff against) uses instead.


def verify_ref(root:Path,ref:str)->bool:
    """True when `ref` names a commit that exists in this repository."""
    if not ref or ref.startswith('-'): return False
    return bool(run(["git","rev-parse","--verify","--quiet",ref+"^{commit}"],cwd=root,check=False))


def origin_head(root:Path)->str:
    """The remote's own default branch, when origin/HEAD is set locally."""
    ref=run(["git","symbolic-ref","--quiet","--short","refs/remotes/origin/HEAD"],cwd=root,check=False)
    return ref if verify_ref(root,ref) else ''


def _base_candidate_order(root:Path,requested:str|None=None)->list[str]:
    """Ref names to try for a diff base, best guess first (unverified -- callers
    still need verify_ref() on each). The one place this order is written down,
    so base_suggestions() (needs every verified ref, for error messages) and
    first_base() (stops at the first verified ref, the hot path resolve_base()
    actually uses) can't drift into disagreeing rankings.
    """
    out:list[str]=[]
    # A local name that only exists on the remote is by far the most common near-miss.
    if requested and '/' not in requested: out.append('origin/'+requested)
    out.append(origin_head(root))
    for name in BASE_CANDIDATES: out.append('origin/'+name); out.append(name)
    return out


def base_suggestions(root:Path,requested:str|None=None)->list[str]:
    """Refs this repository actually has, best guess first."""
    out:list[str]=[]
    for ref in _base_candidate_order(root,requested):
        if ref and ref not in out and verify_ref(root,ref): out.append(ref)
    return out


def first_base(root:Path,requested:str|None=None)->str:
    """Like base_suggestions() but stops at the first verified ref.

    resolve_base() only ever uses suggestions[0], so building the full list (up
    to 9 `git rev-parse --verify` spawns, ~16ms each on macOS) just to take its
    head wasted most of that work on every invocation. Behavior stays identical
    to base_suggestions(root,requested)[0] -- same order, same dedup -- because
    both draw from _base_candidate_order().
    """
    seen:set[str]=set()
    for ref in _base_candidate_order(root,requested):
        if ref and ref not in seen:
            seen.add(ref)
            if verify_ref(root,ref): return ref
    return ''


def task_base(state:Path,root:Path)->str:
    """The base recorded by `ai start` for the task this command is acting on."""
    identity=TASK_ID or os.environ.get('AI_TASK_ID') or active_task_id(state,root)
    if not identity: return ''
    key=shasum(str(root.resolve())+'\0'+identity)[:24]
    meta=load_json(state/'tasks'/key/'task.json',{})
    return meta.get('base','') if isinstance(meta,dict) else ''


def base_error(root:Path,base:str,*,source:str)->str:
    suggestions=base_suggestions(root,base)
    lines=[f'FAILED: base ref {base!r} does not exist in this repository.',
           f'It came from {source}; nothing is assumed in its place.']
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
        raise SystemExit(base_error(root,base,source='--base '+base))
    # The task's own base outranks the repository default: `ai start --base` exists
    # precisely so one task can be worked against a different branch than the rest.
    for candidate,source in ((task_base(state,root) if state else '','the active task'),
                             (load_json(state/'repo.json',{}).get('default_base') if state else '',
                              'the stored default base')):
        if candidate:
            if verify_ref(root,candidate): return candidate
            raise SystemExit(base_error(root,candidate,source=f'{source} ({candidate})'))
    detected=first_base(root)
    if detected: return detected
    raise SystemExit('FAILED: no base ref could be detected (looked for origin/HEAD, '
                     +', '.join(BASE_CANDIDATES)+').\nPass an existing branch, tag or commit with --base.')


def collect_scope(root:Path,base:str)->dict:
    # `base` must already be resolved by resolve_base(); this guard keeps the old
    # silent degradation to HEAD from ever creeping back in through a new call site.
    # EMPTY_TREE is a tree object, not a commit-ish, so verify_ref() (which checks
    # `^{commit}`) would reject it even though `git diff` itself accepts it fine.
    if base!=EMPTY_TREE and not verify_ref(root,base):
        raise SystemExit(base_error(root,base,source='an unresolved caller'))
    files=run(["git","diff","--name-only",base],cwd=root,check=False).splitlines()
    untracked=run(["git","ls-files","--others","--exclude-standard"],cwd=root,check=False).splitlines()
    files=sorted(set([f for f in files+untracked if f and not any(f.startswith(p) for p in AI_PATTERNS)]))
    untracked_set=set(untracked)
    lines=0; binary=[]; renamed=[]
    for ln in run(["git","diff","--numstat",base],cwd=root,check=False).splitlines():
        parts=ln.split('\t')
        if len(parts)<3: continue
        added,removed,path=parts[0],parts[1],parts[2]
        # git renders a rename as `old => new` (or with a {a => b} infix). Keep the
        # arrow out of the reported paths, and record that a rename happened: a pure
        # rename legitimately scores 0 lines, so without this signal a large
        # reorganisation is indistinguishable from no change at all.
        if ' => ' in path: renamed.append(path)
        if any(path.startswith(pattern) for pattern in AI_PATTERNS): continue
        # `-\t-` is git's marker for a binary file. The old loop tested isdigit() and
        # so silently scored these as zero: a replaced 300KB binary counted as a
        # 0-line, 1-file change, which classify() read as LOW, required_gates() then
        # dropped the review gate over, and `ai ready` could certify PR_READY on a
        # change no reviewer had ever seen. Binaries are now counted, never summed.
        if added=='-' or removed=='-': binary.append(path); continue
        for x in (added,removed):
            if x.isdigit(): lines+=int(x)
    # `git diff` does not describe untracked files at all, so a brand-new 2000-line
    # module scored zero the same way a binary did. They are measured directly here.
    for name in sorted(untracked_set):
        if any(name.startswith(pattern) for pattern in AI_PATTERNS): continue
        full=root/name
        if full.is_symlink() or not full.is_file(): continue
        try: blob=full.read_bytes()
        except OSError: continue
        if b'\0' in blob[:8000]: binary.append(name); continue
        lines+=blob.count(b'\n')+(0 if blob.endswith(b'\n') or not blob else 1)
    return {"base":base,"files":files,"file_count":len(files),"changed_lines":lines,
            "binary_files":sorted(set(binary)),"renamed_files":sorted(set(renamed))}


RENAME_ELEVATION_MIN = 3


def classify(scope:dict, profile:str)->dict:
    paths='\n'.join(scope['files']).lower(); risk='LOW'; reason='small/local change'; security=False
    high=re.compile(r'(^|/)(auth|authentication|authorization|rbac|iam|payment|payments|billing|migration|migrations|schema|database|db|crypto|secrets?|permissions?|infra|terraform|k8s|kubernetes)(/|$)|\.sql$|\.tf$',re.M)
    medium=re.compile(r'(^|/)(api|services?|integrations?|workers?|queues?|jobs?|repositories?|controllers?|middleware)(/|$)',re.M)
    sec=re.compile(r'(^|/)(auth|authentication|authorization|rbac|iam|crypto|secrets?|permissions?)(/|$)|security|tenant|token|credential',re.M)
    security=bool(sec.search(paths))
    if high.search(paths): risk,reason='HIGH','high-risk path/domain'
    elif medium.search(paths): risk,reason='MEDIUM','business/API/integration path'
    elif scope['changed_lines']>=160 or scope['file_count']>=6: risk,reason='MEDIUM','non-trivial diff size'
    # A binary carries no reviewable diff: the review gate would be reading a size, not
    # a change. That is the opposite of a reason to skip review, so it never scores LOW.
    if scope.get('binary_files') and risk=='LOW': risk,reason='MEDIUM','binary content cannot be reviewed as a diff'
    # A pure rename scores zero lines, so a set of them below the file-count threshold
    # lands LOW and skips review -- yet moving a module breaks every importer, and the
    # line diff a reviewer would read says nothing about that. Elevate once there are
    # enough of them to be a reorganisation rather than a single tidy-up.
    if len(scope.get('renamed_files') or [])>=RENAME_ELEVATION_MIN and risk=='LOW':
        risk,reason='MEDIUM','file reorganisation (renames) is not a reviewable line diff'
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


AUTO_FAST_TASK_TYPES={'feature','bug','prototype'}


def resolve_profile(explicit:str|None,scope:dict,task:str,figma:str|None=None)->tuple[str,str|None]:
    """Pick a context/budget profile when the operator did not name one.

    An explicit `--profile` always wins. Otherwise `fast` is chosen only for a
    small, low-risk change of a routine kind (a footer, a copy tweak, a
    contained bug fix); anything touching a high/medium-risk path, crossing a
    security boundary, exceeding the LOW size thresholds, or of an architectural
    kind falls back to `standard`, the safe default. The second tuple element is
    the downgrade reason, non-None only when the downgrade fired, so callers can
    show why the profile was not `standard`.
    """
    if explicit is not None: return explicit,None
    risk=classify(scope,'standard')
    small=scope['file_count']<6 and scope['changed_lines']<160
    if (small and risk['risk']=='LOW' and not risk['security']
            and classify_task(task,figma) in AUTO_FAST_TASK_TYPES):
        return 'fast','small low-risk change'
    return 'standard',None


def context_caps(profile:str)->dict:
    # `usage_cost_usd` is a second ceiling on the same spend `usage_tokens` bounds:
    # equal token counts cost an order of magnitude apart across the models `ai
    # providers` can select, so a token budget alone stops meaning the same thing
    # the moment the provider changes. It binds only where a provider actually
    # reports cost -- usage_from_verdict() takes cost_usd only when given it, and
    # an unreported cost can never trip a budget.
    return {
      'fast': {
        'raw_files':4,'review_files':5,'agent_calls':3,'reviews':0,'docs_queries':1,'graph_queries':2,
        'skills':1,'findings':3,'context_chars':12000,'handoff_chars':3500,'retries':1,
        'usage_tokens':40000,'usage_cost_usd':0.50
      },
      'standard': {
        'raw_files':8,'review_files':10,'agent_calls':5,'reviews':1,'docs_queries':3,'graph_queries':4,
        'skills':2,'findings':3,'context_chars':24000,'handoff_chars':5500,'retries':2,
        'usage_tokens':120000,'usage_cost_usd':1.50
      },
      'strict': {
        'raw_files':12,'review_files':15,'agent_calls':7,'reviews':1,'docs_queries':5,'graph_queries':6,
        'skills':3,'findings':5,'context_chars':36000,'handoff_chars':7500,'retries':2,
        'usage_tokens':250000,'usage_cost_usd':3.00
      }
    }[profile]


def required_gates(root,plan):
    risk=classify(collect_scope(root,plan['scope']['base']),plan['profile'])
    required=list(GATES[:7])
    if plan['profile']!='fast' or risk['risk']!='LOW' or plan['risk']['risk']!='LOW': required.append('review')
    if risk['security'] or plan['risk']['security']: required.append('security')
    if plan.get('figma'): required.append('design')
    return [name for name in ORDER if name in required]
