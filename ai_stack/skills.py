from __future__ import annotations
import json, re
from pathlib import Path
from core import STACK_ROOT, classify_task, context_caps, git_root, load_json, repo_state, save_json


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
    if explicit:
        unknown=[x for x in explicit if x not in registry]
        if unknown: raise SystemExit('Unknown skill(s): '+', '.join(unknown))
        return [x for x in explicit if registry[x]['enabled']][:caps['skills']]
    kind=classify_task(task,figma)
    scores=[]
    tl=(task or '').lower()
    for name,meta in registry.items():
        if not meta['enabled']: continue
        score=0
        if kind in meta.get('task_types',[]): score+=5
        if kind=='bug' and meta.get('category')=='debugging': score+=3
        if kind=='prototype' and name=='prototype': score+=3
        if kind=='architecture' and name=='wayfinder': score+=3
        if kind=='planning' and name=='to-tickets': score+=3
        for trig in meta.get('triggers',[]):
            if trig.lower() in tl: score+=2
        if meta.get('always_consider') and kind in ('feature','bug','design'): score+=1
        if score: scores.append((score, int(meta.get('priority',0)), name))
    scores.sort(reverse=True)
    return [name for _,_,name in scores[:caps['skills']]]


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
