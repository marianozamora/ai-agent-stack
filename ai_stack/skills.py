from __future__ import annotations
import json
from pathlib import Path
from core import STACK_ROOT, classify_task, context_caps, git_root, load_json, repo_state, save_json


def skill_root()->Path:
    return STACK_ROOT/'skills'


def skill_registry()->dict:
    p=skill_root()/'registry.json'
    return load_json(p,{"skills":{}})


def skill_overrides(state:Path)->dict:
    return load_json(state/'skill-overrides.json',{})


def enabled_skills(state:Path)->dict:
    reg=skill_registry().get('skills',{})
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


def load_skill_context(names:list[str], max_chars:int)->str:
    if not names:return '(none)'
    chunks=[]; used=0
    for name in names:
        p=skill_root()/name/'prompt.md'
        if not p.exists(): continue
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
            print(f"{mark} {name:20} {meta.get('category','')}  cost={meta.get('cost','?')}")
        if getattr(args,'task',None):
            selected=select_skills(state,args.task,args.profile,None,None)
            print('\nRecommended:', ', '.join(selected) or 'none')
        return
    name=args.name
    if name not in registry: raise SystemExit(f'Unknown skill: {name}')
    if args.skill_cmd=='explain':
        meta=registry[name]
        print(json.dumps({k:v for k,v in meta.items() if k!='enabled'},indent=2))
        p=skill_root()/name/'README.md'
        if p.exists(): print('\n'+p.read_text().strip())
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
