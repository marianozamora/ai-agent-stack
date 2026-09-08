"""Detect a repository's real check/test tooling and propose `checks`/`regression` validators.

Only the two deterministic gates are proposed. The semantic gates (contract, review,
security, ponytail, design, summary, provenance, cleanup) are the bundled model
validators that `ai validators install` configures; nothing here touches them.

A proposal is never written without `--apply`: validators.json holds commands that
`ai pipeline` later executes, so generating one and persisting it in the same
unattended step would turn tooling detection into silent code execution.
"""
from __future__ import annotations
import json, shlex, shutil
from pathlib import Path


def has(root:Path,*names:str)->bool:
    return any((root/name).exists() for name in names)


def pyproject_has(root:Path,section:str)->bool:
    path=root/'pyproject.toml'
    try: return section in path.read_text()
    except OSError: return False


def package_json(root:Path)->dict:
    try: value=json.loads((root/'package.json').read_text())
    except (OSError,ValueError): return {}
    return value if isinstance(value,dict) else {}


def has_script(root:Path,name:str)->bool:
    scripts=package_json(root).get('scripts')
    return isinstance(scripts,dict) and bool(scripts.get(name))


def package_manager(root:Path)->str:
    """The lockfile decides; preference order only breaks ties in a repo with several."""
    for lock,name in (('pnpm-lock.yaml','pnpm'),('yarn.lock','yarn'),('bun.lockb','bun'),
                      ('package-lock.json','npm')):
        if (root/lock).exists(): return name
    return 'npm' if (root/'package.json').exists() else ''


def node_exec(root:Path,*argv:str)->list[str]:
    """Run a locally-installed binary the way this repo's package manager does."""
    return {'pnpm':['pnpm','exec',*argv],'yarn':['yarn',*argv],
            'bun':['bunx',*argv],'npm':['npx',*argv]}[package_manager(root) or 'npm']


def has_python_tests(root:Path)->bool:
    if has(root,'pytest.ini','tox.ini') or pyproject_has(root,'[tool.pytest'): return True
    for directory in ('tests','test'):
        if (root/directory).is_dir() and any((root/directory).glob('test_*.py')): return True
    return False


# (id, gate, predicate, command builder, evidence description)
RULES = [
    ('ruff','checks',lambda r: has(r,'ruff.toml','.ruff.toml') or pyproject_has(r,'[tool.ruff'),
     lambda r: ['ruff','check','.'],'Ruff lint passed with no findings'),
    ('mypy','checks',lambda r: has(r,'mypy.ini','.mypy.ini') or pyproject_has(r,'[tool.mypy'),
     lambda r: ['mypy','.'],'mypy type check passed'),
    ('pytest','regression',has_python_tests,
     lambda r: ['pytest','-q'],'pytest suite passed'),

    ('eslint','checks',lambda r: has_script(r,'lint'),
     lambda r: [package_manager(r),'run','lint'],'Lint script passed'),
    ('tsc','checks',lambda r: has(r,'tsconfig.json') and not has_script(r,'typecheck'),
     lambda r: node_exec(r,'tsc','--noEmit'),'TypeScript type check passed'),
    ('typecheck-script','checks',lambda r: has_script(r,'typecheck'),
     lambda r: [package_manager(r),'run','typecheck'],'Typecheck script passed'),
    ('npm-test','regression',lambda r: has_script(r,'test'),
     lambda r: [package_manager(r),'test'],'Test script passed'),

    ('clippy','checks',lambda r: has(r,'Cargo.toml'),
     lambda r: ['cargo','clippy','--all-targets','--','-D','warnings'],'Clippy passed with no warnings'),
    ('cargo-test','regression',lambda r: has(r,'Cargo.toml'),
     lambda r: ['cargo','test'],'Cargo test suite passed'),

    ('golangci-lint','checks',lambda r: has(r,'.golangci.yml','.golangci.yaml','.golangci.toml'),
     lambda r: ['golangci-lint','run'],'golangci-lint passed'),
    ('go-vet','checks',lambda r: has(r,'go.mod'),
     lambda r: ['go','vet','./...'],'go vet passed'),
    ('go-test','regression',lambda r: has(r,'go.mod'),
     lambda r: ['go','test','./...'],'Go test suite passed'),
]


def detect(root:Path)->list[dict]:
    """Every tool rule that matches this repository, in table order."""
    found=[]
    for name,gate,predicate,builder,evidence in RULES:
        if predicate(root):
            command=builder(root)
            found.append({'id':name,'gate':gate,'command':command,'evidence':evidence,
                          'available':bool(shutil.which(command[0]))})
    return found


# A stricter tool replaces the one it subsumes, but only when it is actually
# installed -- otherwise the repo would be left with no check for that gate at all.
SUPERSEDES = {'golangci-lint':('go-vet',)}


def combine(root:Path,gate:str,matches:list[dict])->dict|None:
    """One validator per gate; several tools for one gate run in sequence, fail-fast.

    `checks` is a single slot but a repo commonly has both a linter and a type
    checker, so multiple commands are joined with `&&` under an explicit `sh -c`
    rather than dropping all but one. The generated string is composed from this
    module's fixed table only — it never interpolates repository content.
    """
    selected=[m for m in matches if m['gate']==gate and m['available']]
    superseded={victim for m in selected for victim in SUPERSEDES.get(m['id'],())}
    selected=[m for m in selected if m['id'] not in superseded]
    if not selected: return None
    if len(selected)==1: command=selected[0]['command']
    else: command=['sh','-c',' && '.join(shlex.join(m['command']) for m in selected)]
    return {'command':command,'adapter':'exit-code','timeout':600,
            'evidence':'; '.join(m['evidence'] for m in selected),
            'detected_from':[m['id'] for m in selected]}


def proposal(root:Path,configured:dict)->dict:
    """What `ai validators propose` reports: per gate, the command and what to do with it."""
    matches=detect(root)
    rows=[]
    for gate in ('checks','regression'):
        candidate=combine(root,gate,matches)
        unavailable=[m['id'] for m in matches if m['gate']==gate and not m['available']]
        if gate in configured:
            rows.append({'gate':gate,'action':'keep','command':configured[gate]['command'],
                         'detected_from':[],'unavailable':unavailable})
        elif candidate:
            rows.append({'gate':gate,'action':'add','command':candidate['command'],
                         'detected_from':candidate['detected_from'],'unavailable':unavailable,
                         'validator':{k:v for k,v in candidate.items() if k!='detected_from'}})
        else:
            rows.append({'gate':gate,'action':'none','command':None,'detected_from':[],
                         'unavailable':unavailable})
    return {'version':1,'rows':rows,'detected':matches}


def render(report:dict)->list[str]:
    lines=[]
    for row in report['rows']:
        gate=row['gate']
        if row['action']=='keep':
            lines.append(f'  {gate:11} keep      {shlex.join(row["command"])} (already configured)')
        elif row['action']=='add':
            lines.append(f'  {gate:11} propose   {shlex.join(row["command"])}')
            lines.append(f"  {'':11}           detected from: {', '.join(row['detected_from'])}")
        else:
            lines.append(f"  {gate:11} none      no tooling detected for this gate")
        if row['unavailable']:
            lines.append(f"  {'':11}           detected but not installed here: "
                         +', '.join(row['unavailable']))
    return lines
