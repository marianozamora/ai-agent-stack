from __future__ import annotations
import json, re, sys
from pathlib import Path
from core import STACK_ROOT, classify_task, context_caps, fold, git_root, load_json, repo_state, require_human, run, save_json, shasum


TASK_TYPES=['bug','feature','architecture','design','prototype','planning']
COSTS=['tiny','low','medium','high']
NAME_RE=re.compile(r'^[a-z][a-z0-9-]*$')


def skill_root()->Path:
    """The skills bundled with the stack itself."""
    return STACK_ROOT/'skills'


def repo_skill_root(state:Path)->Path:
    """Skills belonging to one repository, in its external state.

    Under the repo's external state, not in the checkout: a repository-specific skill is
    still framework state, and the zero-footprint rule does not bend for it.
    """
    return state/'skills'


def skill_roots(state:Path|None)->list[Path]:
    """Lookup order, most specific first: the repo's own skills shadow the bundled ones.

    `state=None` means "no repository in play" (e.g. `ai doctor` run outside a checkout)
    and yields the bundled skills alone, rather than failing on a path that cannot exist.
    """
    return ([repo_skill_root(state)] if state is not None else [])+[skill_root()]


def _valid_skill_entries(path:Path,origin:str)->dict:
    """Read one registry.json, keeping only well-formed name -> dict entries.

    A registry that fails to parse, or whose `skills` value isn't a dict, degrades
    silently through `load_json`'s default (`{}`) -- indistinguishable from "no
    skills defined here". That is fine for the bundled registry (it ships valid
    JSON or the release is broken outright) but not for a repo-authored one, which
    a hand-edit can corrupt at any time; this reports the corruption instead of
    swallowing it. A malformed individual entry (bad name, or not a dict) is
    dropped and reported rather than silently included or crashing the cascade --
    an unvalidated name is also how a `../../` path-traversal entry would reach
    `resolve_skill_file`.
    """
    if not path.is_file(): return {}
    try:
        data=json.loads(path.read_text())
    except (OSError,ValueError):
        print(f'Invalid {origin} skill registry (not valid JSON): {path}',file=sys.stderr)
        return {}
    raw=data.get('skills',{}) if isinstance(data,dict) else None
    if not isinstance(raw,dict):
        print(f'Invalid {origin} skill registry ("skills" is not an object): {path}',file=sys.stderr)
        return {}
    out={}
    for name,meta in raw.items():
        if not isinstance(name,str) or not NAME_RE.match(name) or not isinstance(meta,dict):
            print(f'Skipping invalid {origin} skill entry: {name!r} in {path}',file=sys.stderr)
            continue
        out[name]=meta
    return out


def skill_registry(state:Path|None=None)->dict:
    """The bundled registry, with any repo-local registry layered on top.

    A repo entry with a name the stack also ships replaces it outright rather than merging
    field-by-field: a half-overridden skill (repo triggers, bundled stages) is a definition
    nobody wrote and nobody can predict.
    """
    merged={name:{**meta,'origin':'stack'}
            for name,meta in _valid_skill_entries(skill_root()/'registry.json','stack').items()}
    if state is not None:
        for name,meta in _valid_skill_entries(repo_skill_root(state)/'registry.json','repo').items():
            merged[name]={**meta,'origin':'repo'}
    return {"version":1,"skills":merged}


def resolve_skill_file(state:Path|None,name:str,filename:str)->Path|None:
    if not NAME_RE.match(name or ''): return None
    for root in skill_roots(state):
        candidate=root/name/filename
        if candidate.is_file(): return candidate
    return None


def upstream_registry()->dict:
    path=skill_root()/'upstreams.json'
    if not path.is_file(): return {"version":1,"sources":{}}
    try: data=json.loads(path.read_text())
    except (OSError,ValueError) as exc: raise RuntimeError('Invalid upstream skill manifest') from exc
    if data.get('version')!=1 or not isinstance(data.get('sources'),dict):
        raise RuntimeError('Invalid upstream skill manifest')
    return data


def skill_overrides(state:Path)->dict:
    return load_json(state/'skill-overrides.json',{})


def enabled_skills(state:Path)->dict:
    reg=skill_registry(state).get('skills',{})
    overrides=skill_overrides(state)
    out={}
    for name,meta in reg.items():
        m=dict(meta)
        override=overrides.get(name)
        # Only an actual bool counts as an override. `bool("false")` is True, so a
        # hand-edited skill-overrides.json with the JSON string "false" would silently
        # turn a disable into an enable; anything not a real bool is reported and the
        # registry's own default is used instead of guessing which way it was meant.
        if name in overrides and not isinstance(override,bool):
            print(f'Ignoring non-boolean skill-overrides.json entry for {name!r}: {override!r}',file=sys.stderr)
            override=None
        m['enabled']=bool(override) if isinstance(override,bool) else bool(m.get('enabled',True))
        out[name]=m
    return out


_INFLECTION=r'(?:e|es|s|d|ed|ing|ion|ions)?'


def trigger_hits(triggers:list[str],text:str)->list[str]:
    """Whole-word matches allowing plain inflections: `test` hits "tests" but not "latest",
    `slo` hits "SLOs" but not "slow". Accents are folded on both sides."""
    folded=fold(text)
    return [t for t in triggers if re.search(r'(?<!\w)'+re.escape(fold(t))+_INFLECTION+r'(?!\w)',folded)]


def score_skills(state:Path,task:str,figma:str|None=None)->list[dict]:
    """Every selectable, enabled skill that scores for this task, best first, with why."""
    kind=classify_task(task,figma); rows=[]
    for name,meta in enabled_skills(state).items():
        # selectable:false (tool-routing) is loaded separately, only when a capability is
        # detected, so it must never displace tdd/diagnosing-bugs from `fast`'s single slot.
        if not meta['enabled'] or not meta.get('selectable',True): continue
        hits=trigger_hits(meta.get('triggers',[]),task or '')
        if meta.get('requires_trigger') and not hits: continue
        score=0; reasons=[]
        if kind in meta.get('task_types',[]): score+=5; reasons.append(f'task type {kind}')
        if kind in meta.get('primary_for',[]): score+=3; reasons.append(f'primary for {kind}')
        if hits: score+=2*len(hits); reasons.append('triggers: '+', '.join(hits))
        if meta.get('always_consider') and kind in ('feature','bug','design'): score+=1; reasons.append('always considered')
        if score: rows.append({'name':name,'score':score,'priority':int(meta.get('priority',0)),'reasons':reasons})
    rows.sort(key=lambda row:(row['score'],row['priority'],row['name']),reverse=True)
    return rows


def select_skills(state:Path,task:str,profile:str,figma:str|None=None, explicit:list[str]|None=None)->list[str]:
    """`explicit` (repeatable `--skill`) is a request, not a routing decision: it is still
    capped by the profile's skill budget, and -- unlike scored routing -- it deliberately
    ignores `requires_trigger`, since naming a skill by hand already establishes intent
    a trigger match would otherwise stand in for.
    """
    caps=context_caps(profile); explicit=explicit or []
    if explicit:
        registry={name:meta for name,meta in enabled_skills(state).items() if meta.get('selectable',True)}
        unknown=[x for x in explicit if x not in registry]
        if unknown: raise SystemExit('Unknown skill(s): '+', '.join(unknown))
        disabled=[x for x in explicit if not registry[x]['enabled']]
        if disabled: print('Skipping disabled skill(s): '+', '.join(disabled)+' (ai skill enable NAME)',file=sys.stderr)
        # Preserve the caller's order, drop duplicates and disabled entries, then apply
        # the profile cap last -- and say so, the same way a disabled skill is reported,
        # rather than dropping the tail of the list in silence.
        seen=set(); wanted=[]
        for x in explicit:
            if registry[x]['enabled'] and x not in seen: seen.add(x); wanted.append(x)
        if len(wanted)>caps['skills']:
            print(f"Profile {profile!r} allows {caps['skills']} skill(s); dropping: "+', '.join(wanted[caps['skills']:]),file=sys.stderr)
        return wanted[:caps['skills']]
    return [row['name'] for row in score_skills(state,task,figma)[:caps['skills']]]


def upstream_status(check:bool=False)->list[dict]:
    manifest=upstream_registry(); registry=skill_registry().get('skills',{}); rows=[]
    for source_name,source in manifest.get('sources',{}).items():
        repository=source.get('repository',''); ref=source.get('ref','main'); pinned=source.get('commit','')
        if not repository or not re.fullmatch(r'[0-9a-f]{40,64}',pinned):
            raise RuntimeError(f'Invalid upstream manifest entry: {source_name}')
        local_skills=source.get('skills',{})
        for local_name,mapping in local_skills.items():
            provenance=registry.get(local_name,{}).get('provenance',{})
            if (provenance.get('source')!=source_name or provenance.get('path')!=mapping.get('path')
                    or provenance.get('adaptation')!=mapping.get('adaptation')):
                raise RuntimeError(f'Upstream provenance mismatch for skill: {local_name}')
            expected_hash=mapping.get('prompt_sha256',''); prompt_path=skill_root()/local_name/'prompt.md'
            descriptor=load_json(prompt_path.parent/'skill.json',{}).get('provenance',{})
            if descriptor!=provenance:
                raise RuntimeError(f'Skill descriptor provenance mismatch: {local_name}')
            if not re.fullmatch(r'[0-9a-f]{64}',expected_hash) or not prompt_path.is_file():
                raise RuntimeError(f'Invalid curated prompt manifest for skill: {local_name}')
            if shasum(prompt_path.read_text())!=expected_hash:
                raise RuntimeError(f'Curated prompt changed without updating provenance: {local_name}')
        current=None; status='PINNED'
        if check:
            output=run(['git','ls-remote',repository,f'refs/heads/{ref}'])
            first=output.splitlines()[0].split()[0] if output else ''
            if not re.fullmatch(r'[0-9a-f]{40,64}',first):
                raise RuntimeError(f'Upstream ref not found: {source_name} {ref}')
            current=first; status='CURRENT' if current==pinned else 'UPDATE_AVAILABLE'
        rows.append({'source':source_name,'repository':repository,'ref':ref,'license':source.get('license'),
                     'pinned_commit':pinned,'current_commit':current,'status':status,
                     'skills':sorted(local_skills)})
    return rows


def cmd_skill_upstream(args):
    rows=upstream_status(getattr(args,'check',False))
    if getattr(args,'json',False): print(json.dumps({'version':1,'sources':rows},indent=2)); return
    print('Upstream skill sources')
    if not rows: print('(none)'); return
    for row in rows:
        current=f" current={row['current_commit'][:12]}" if row['current_commit'] else ''
        print(f"{row['source']}: {row['status']} pinned={row['pinned_commit'][:12]}{current} "
              f"ref={row['ref']} skills={','.join(row['skills'])}")


def load_skill_context(state:Path|None, names:list[str], max_chars:int)->str:
    if not names:return '(none)'
    chunks=[]; used=0
    for name in names:
        p=resolve_skill_file(state,name,'prompt.md')
        if p is None: continue
        body=p.read_text().strip()
        remaining=max_chars-used
        if remaining<=0: break
        if len(body)>remaining: body=body[:remaining].rstrip()+"\n[skill context truncated by budget]"
        chunks.append(f"## Skill: {name}\n{body}")
        used+=len(body)
    return '\n\n'.join(chunks) if chunks else '(none)'


def cmd_skill(args):
    if args.skill_cmd=='upstream': return cmd_skill_upstream(args)
    root=git_root(); state=repo_state(root); registry=enabled_skills(state)
    if args.skill_cmd in (None,'list'):
        print('Skills')
        for name,meta in registry.items():
            mark='✓' if meta['enabled'] else '·'
            origin='' if meta.get('origin')=='stack' else '  [repo]'
            print(f"{mark} {name:20} {meta.get('category','')}  cost={meta.get('cost','?')}{origin}")
        if getattr(args,'task',None):
            selected=select_skills(state,args.task,args.profile,None,None)
            print(f'\nTask type: {classify_task(args.task)}')
            print('Recommended:', ', '.join(selected) or 'none')
            for row in score_skills(state,args.task)[:5]:
                mark='*' if row['name'] in selected else ' '
                print(f"  {mark} {row['name']:34} score={row['score']}  {'; '.join(row['reasons'])}")
        return
    if args.skill_cmd=='create': return cmd_skill_create(args)
    name=args.name
    if name not in registry: raise SystemExit(f'Unknown skill: {name}')
    if args.skill_cmd=='explain':
        meta=registry[name]
        print(json.dumps({k:v for k,v in meta.items() if k!='enabled'},indent=2))
        p=resolve_skill_file(state,name,'README.md')
        if p is not None: print('\n'+p.read_text().strip())
        return
    if args.skill_cmd in ('enable','disable'):
        require_human('Skill overrides')
        o=skill_overrides(state); o[name]=(args.skill_cmd=='enable'); save_json(state/'skill-overrides.json',o)
        print(f"{name}: {'enabled' if o[name] else 'disabled'}")
        return
    if args.skill_cmd=='dry-run':
        caps=context_caps(args.profile)
        # The same budget build_prompt() splits across every selected skill (lifecycle.py);
        # shown here against just this one skill, so a skill alone over budget is visible
        # before it ever loses a slot-sharing truncation race against another.
        budget=max(2000,caps['context_chars']//3)
        p=resolve_skill_file(state,name,'prompt.md')
        body=p.read_text().strip() if p is not None else None
        print('SKILL DRY RUN')
        print('  name:       ',name)
        print('  category:   ',registry[name].get('category'))
        print('  cost:       ',registry[name].get('cost'))
        print('  origin:     ',registry[name].get('origin'))
        print('  max skills: ',caps['skills'])
        print('  stages:     ', ' -> '.join(registry[name].get('stages',[])))
        print('  repo writes: NO')
        if body is None:
            print('  prompt:      missing (no prompt.md found for this skill)')
            return
        print(f'  prompt size: {len(body)} chars (skill context budget for {args.profile}: {budget} chars,'
              f' shared across up to {caps["skills"]} selected skill(s))')
        if len(body)>budget:
            print(f'  NOTE: this prompt alone exceeds the budget and would be truncated by'
                  f' {len(body)-budget} chars if selected.')
        print('  prompt:')
        print(body)
        return


def cmd_skill_create(args):
    require_human('Skill creation')
    name=args.name
    if not NAME_RE.match(name):
        raise SystemExit('Skill name must be lowercase letters, digits and hyphens, starting with a letter.')
    if args.cost not in COSTS:
        raise SystemExit(f"Unknown cost {args.cost!r}. Valid: {', '.join(COSTS)}")
    task_types=[t.strip() for t in (args.task_types or '').split(',') if t.strip()]
    bad_types=[t for t in task_types if t not in TASK_TYPES]
    if bad_types:
        raise SystemExit(f"Unknown task type(s): {', '.join(bad_types)}. Valid: {', '.join(TASK_TYPES)}")
    triggers=[t.strip() for t in (args.triggers or '').split(',') if t.strip()]
    stages=[s.strip() for s in (args.stages or '').split(',') if s.strip()]
    repo_scoped=bool(getattr(args,'repo',False))
    target_root=repo_skill_root(repo_state(git_root())) if repo_scoped else skill_root()
    # `skill_root()` is STACK_ROOT/skills. In an installed release, STACK_ROOT is one of
    # `install.py`'s disposable release directories (ai-agent-stack-releases/release-*) --
    # a stack-scoped skill created there is written into a copy the next upgrade replaces
    # wholesale, so it silently vanishes on the very next `ai upgrade`. Nothing about this
    # is repo-specific, so --repo isn't a fix here; only creating from the source checkout
    # (where STACK_ROOT is the working tree itself) makes a stack-scoped skill durable.
    if not repo_scoped and 'ai-agent-stack-releases' in target_root.parts:
        raise SystemExit('Stack-scoped skills cannot be created in an installed release '
                          '(they would be lost on upgrade). Use --repo, or create it in the source checkout.')
    # The registry that gets written back is always the raw file, never the merged view:
    # the merged view carries a synthesized `origin` and drops the file's own `policy`, so
    # saving it would rewrite the bundled registry into something nobody authored.
    registry=load_json(target_root/'registry.json',{"version":1,"skills":{}})
    # Each scope owns its own namespace: the collision check is against the registry being
    # written, not the merged view. Shadowing is the point of the cascade, in both
    # directions -- a repo may deliberately override a bundled skill, and a bundled skill
    # is still worth creating for every other repository even if one repo shadows the name.
    if name in registry.get('skills',{}):
        raise SystemExit(f'Skill already exists: {name}')
    folder=target_root/name
    if folder.exists():
        raise SystemExit(f'{folder} already exists.')
    meta={"enabled":True,"category":args.category,"cost":args.cost,"priority":args.priority,
          "task_types":task_types,"triggers":triggers}
    if args.always_consider: meta["always_consider"]=True
    meta["stages"]=stages
    folder.mkdir(parents=True)
    (folder/'skill.json').write_text(json.dumps({"name":name,"version":1,**{k:v for k,v in meta.items() if k!='enabled'}},indent=2)+'\n')
    (folder/'prompt.md').write_text(args.prompt.strip()+'\n')
    (folder/'README.md').write_text(f"# {name}\n\n{(args.description or '(no description provided)').strip()}\n")
    registry.setdefault('skills',{})[name]=meta
    save_json(target_root/'registry.json',registry)
    print('Skill created:',name)
    print('  scope:   ','repo (this repository only)' if repo_scoped else 'stack (every repository)')
    print('  folder:  ',folder)
    print('  registry:',target_root/'registry.json','(updated, enabled by default)')
