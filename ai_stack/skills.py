from __future__ import annotations
import json, re
from pathlib import Path
from core import STACK_ROOT, classify_task, context_caps, git_root, load_json, repo_state, require_human, run, save_json, shasum


TASK_TYPES=['bug','feature','architecture','design','prototype','planning']
COSTS=['tiny','low','medium','high']


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


def skill_registry(state:Path|None=None)->dict:
    """The bundled registry, with any repo-local registry layered on top.

    A repo entry with a name the stack also ships replaces it outright rather than merging
    field-by-field: a half-overridden skill (repo triggers, bundled stages) is a definition
    nobody wrote and nobody can predict.
    """
    merged={name:{**meta,'origin':'stack'}
            for name,meta in load_json(skill_root()/'registry.json',{"skills":{}}).get('skills',{}).items()}
    if state is not None:
        for name,meta in load_json(repo_skill_root(state)/'registry.json',{"skills":{}}).get('skills',{}).items():
            merged[name]={**meta,'origin':'repo'}
    return {"version":1,"skills":merged}


def resolve_skill_file(state:Path|None,name:str,filename:str)->Path|None:
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
        m=dict(meta); m['enabled']=bool(overrides.get(name,m.get('enabled',True)))
        out[name]=m
    return out


def select_skills(state:Path,task:str,profile:str,figma:str|None=None, explicit:list[str]|None=None)->list[str]:
    caps=context_caps(profile); registry=enabled_skills(state); explicit=explicit or []
    # A skill marked selectable:false (tool-routing) never competes for one of the
    # profile's skill slots -- it is loaded separately, only when at least one
    # capability is actually detected (capabilities.py), so it must never be able to
    # displace tdd/diagnosing-bugs out of a `fast` profile's single slot.
    registry={name:meta for name,meta in registry.items() if meta.get('selectable',True)}
    if explicit:
        unknown=[x for x in explicit if x not in registry]
        if unknown: raise SystemExit('Unknown skill(s): '+', '.join(unknown))
        return [x for x in explicit if registry[x]['enabled']][:caps['skills']]
    kind=classify_task(task,figma)
    scores=[]
    tl=(task or '').lower()
    for name,meta in registry.items():
        if not meta['enabled']: continue
        matched_triggers=[trigger for trigger in meta.get('triggers',[]) if trigger.lower() in tl]
        if meta.get('requires_trigger') and not matched_triggers: continue
        score=0
        if kind in meta.get('task_types',[]): score+=5
        if kind=='bug' and meta.get('category')=='debugging': score+=3
        if kind=='prototype' and name=='prototype': score+=3
        if kind=='architecture' and name=='wayfinder': score+=3
        if kind=='planning' and name=='to-tickets': score+=3
        score+=2*len(matched_triggers)
        if meta.get('always_consider') and kind in ('feature','bug','design'): score+=1
        if score: scores.append((score, int(meta.get('priority',0)), name))
    scores.sort(reverse=True)
    return [name for _,_,name in scores[:caps['skills']]]


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
            print('\nRecommended:', ', '.join(selected) or 'none')
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
        print('SKILL DRY RUN')
        print('  name:       ',name)
        print('  category:   ',registry[name].get('category'))
        print('  cost:       ',registry[name].get('cost'))
        print('  max skills: ',caps['skills'])
        print('  stages:     ', ' -> '.join(registry[name].get('stages',[])))
        print('  repo writes: NO')
        return


def cmd_skill_create(args):
    require_human('Skill creation')
    name=args.name
    if not re.fullmatch(r'[a-z][a-z0-9-]*',name):
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
