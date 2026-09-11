from __future__ import annotations
import contextlib, json, os, re, shutil, subprocess, time
from pathlib import Path
from core import (EMPTY_TREE, base_error, classify, collect_scope, context_caps, enforce_budget,
                  git_root, repo_state, resolve_base, run, save_json, task_state, temp_worktree, verify_ref)
from providers import reviewer as get_reviewer


def resolve_commit_target(root:Path,sha:str)->tuple[str,str]:
    """Full commit sha and its diff base: the parent, or the empty tree for a root commit."""
    full=run(["git","rev-parse","--verify","--quiet",sha+"^{commit}"],cwd=root,check=False)
    if not full: raise SystemExit(f'FAILED: commit {sha!r} does not exist in this repository.')
    # A range or multi-revision expression (e.g. "A..B") is not a single commit, but
    # `git rev-parse --verify` doesn't reject it outright: it expands to multiple lines
    # ("B" then "^A"), which `full` would otherwise pass along as a garbage multi-line
    # ref to `git worktree add`, crashing with an unhandled traceback instead of this
    # clean refusal. --commit only ever reviews one commit; a range needs --base instead.
    if '\n' in full or ' ' in full:
        raise SystemExit(f'FAILED: {sha!r} is not a single commit (ranges and multi-revision '
                         "expressions are not supported). Pass one commit sha with --commit, "
                         'or use --base <ref> to review a range as one diff.')
    parent=run(["git","rev-parse","--verify","--quiet",full+"^"],cwd=root,check=False)
    return full,(parent or EMPTY_TREE)


def resolve_pr_target(root:Path,number:int)->tuple[str,str,str]:
    """Fetches a PR's head into this repo's object database (no branch/checkout
    change, works for forks too via GitHub's refs/pull/<n>/head) and returns
    (head_sha, base_ref, label)."""
    gh=shutil.which('gh')
    if not gh: raise SystemExit('GitHub CLI (gh) missing. Install/authenticate gh to review a PR '
                                'by number, or use --commit <sha> instead.')
    raw=run([gh,"pr","view",str(number),"--json","baseRefName,headRefName"],cwd=root,check=False)
    try: info=json.loads(raw) if raw else {}
    except ValueError: info={}
    base_name=info.get('baseRefName')
    if not base_name: raise SystemExit(f'Could not resolve PR #{number} via gh pr view; '
                                       'check the number and that gh is authenticated for this repo.')
    run(["git","fetch","origin",f"pull/{number}/head:refs/remotes/origin/pr/{number}"],cwd=root)
    head=run(["git","rev-parse","--verify",f"refs/remotes/origin/pr/{number}"],cwd=root)
    run(["git","fetch","origin",base_name],cwd=root,check=False)  # best-effort refresh
    base_ref=f'origin/{base_name}'
    if not verify_ref(root,base_ref): raise SystemExit(base_error(root,base_ref,source=f"PR #{number}'s base branch"))
    # Diff against the merge-base, never the base branch's live tip: once a PR merges
    # (or the base branch simply keeps moving while it's open), the tip drifts away from
    # where the PR forked, and diffing the PR's frozen head against today's tip shows
    # everything the base branch picked up since - not what the PR itself introduced.
    # This is the same "three-dot diff" GitHub's own PR view uses; falls back to the
    # live tip only if merge-base can't find a common ancestor (e.g. unrelated histories).
    base=run(["git","merge-base",head,base_ref],cwd=root,check=False) or base_ref
    return head,base,f"PR #{number} ({info.get('headRefName','?')} -> {base_name})"


def crg_cmd()->list[str]|None:
    exe=shutil.which('code-review-graph')
    return [exe] if exe else None


def crg_env(state:Path)->dict:
    e=os.environ.copy()
    # CRG explicitly supports an external data directory. Keep every DB/artifact
    # out of the company repository so no gitignore/project config is required.
    e['CRG_DATA_DIR']=str(state/'code-review-graph')
    e['GRAPHIFY_OUT']=str(state/'graphify')
    e['AI_REPO_STATE']=str(state)
    return e


def crg_exec(root:Path,state:Path,argv:list[str],check=True)->tuple[int,str]:
    cmd=crg_cmd()
    if not cmd:
        if check: raise RuntimeError('code-review-graph missing')
        return 127,''
    p=subprocess.run(cmd+argv,cwd=root,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                     check=False,env=crg_env(state))
    if check and p.returncode:
        raise RuntimeError((p.stdout or 'code-review-graph failed').strip())
    return p.returncode,(p.stdout or '').strip()


def parse_crg_risk(text:str)->str|None:
    # CLI formatting can evolve; use deliberately loose matching and only accept
    # the three stable severity names. CRG may elevate our heuristic risk, never lower it.
    patterns=[r'(?i)\brisk(?: level| score)?\s*[:=-]\s*(HIGH|MEDIUM|LOW)\b',
              r'(?i)\b(HIGH|MEDIUM|LOW)\s+risk\b']
    for pat in patterns:
        m=re.search(pat,text)
        if m: return m.group(1).upper()
    return None


def elevate_risk(base_risk:dict,crg_risk:str|None)->dict:
    if not crg_risk:return base_risk
    rank={'LOW':0,'MEDIUM':1,'HIGH':2}
    current=base_risk.get('risk','LOW')
    if rank.get(crg_risk,-1)>rank.get(current,-1):
        out=dict(base_risk); out['risk']=crg_risk; out['reason']=f"{base_risk.get('reason','heuristic')} + CRG structural impact"
        out['crg_elevated']=True; return out
    out=dict(base_risk); out['crg_elevated']=False; return out


def crg_impact(root:Path,state:Path,base:str,refresh=False,build_if_missing=False)->str:
    if not crg_cmd(): return ''
    status_rc,_=crg_exec(root,state,['status'],check=False)
    if status_rc!=0:
        if not build_if_missing:return ''
        crg_exec(root,state,['build'])
    elif refresh:
        # update --brief re-parses current changes and then reports impact.
        rc,out=crg_exec(root,state,['update','--base',base,'--brief'],check=False)
        if rc==0 and out:
            (task_state(state)/'review'/'last-impact.txt').write_text(out+'\n'); return out
    rc,out=crg_exec(root,state,['detect-changes','--base',base,'--brief'],check=False)
    if rc==0 and out:
        (task_state(state)/'review'/'last-impact.txt').write_text(out+'\n')
        return out
    return ''


def cmd_crg(args):
    root=git_root(); state=repo_state(root); cmd=crg_cmd()
    if args.crg_cmd=='doctor':
        print('Code Review Graph:', 'ready' if cmd else 'missing (recommended: uv tool install code-review-graph)')
        print('External CRG data:',state/'code-review-graph')
        print('Repository writes: disabled by ai-agent-stack wrapper')
        return
    if not cmd: raise SystemExit('code-review-graph missing. Install: uv tool install code-review-graph')
    if args.crg_cmd=='build': argv=['build']
    elif args.crg_cmd=='update': argv=['update','--base',args.base] + (['--brief'] if args.brief else [])
    elif args.crg_cmd=='status': argv=['status']
    elif args.crg_cmd=='detect': argv=['detect-changes','--base',args.base] + (['--brief'] if args.brief else [])
    else: raise SystemExit(2)
    rc,out=crg_exec(root,state,argv,check=False)
    if out: print(out)
    raise SystemExit(rc)


def cmd_impact(args):
    root=git_root(); state=repo_state(root)
    scope=collect_scope(root,args.base); heuristic=classify(scope,args.profile)
    print('Change impact')
    print('  files:       ',scope['file_count'])
    print('  lines:       ',scope['changed_lines'])
    print('  heuristic:   ',heuristic['risk'],f"({heuristic['reason']})")
    if not crg_cmd():
        print('  CRG:         unavailable')
        print('  final risk:  ',heuristic['risk'])
        return
    out=crg_impact(root,state,args.base,refresh=args.refresh,build_if_missing=args.build)
    if not out:
        print('  CRG:         graph unavailable/stale; run `ai crg build` or `ai impact --build`')
        print('  final risk:  ',heuristic['risk'])
        return
    cr= parse_crg_risk(out); final=elevate_risk(heuristic,cr)
    print('  CRG risk:    ',cr or 'reported below')
    print('  final risk:  ',final['risk'])
    print('\n--- CRG compact impact ---')
    print(out)


def build_review_prompt(root:Path,state:Path,base:str,profile:str,impact:str,*,output_dir:Path|None=None)->str:
    scope=collect_scope(root,base); heuristic=classify(scope,profile)
    cr=parse_crg_risk(impact); risk=elevate_risk(heuristic,cr)
    caps=context_caps(profile)
    compact=impact[-12000:] if impact else '(CRG unavailable; use git diff plus targeted graph evidence.)'
    prompt=f'''# Independent adversarial review\n\nBase: {base}\nProfile: {profile}\nRisk: {risk['risk']} ({risk['reason']})\nChanged files: {scope['file_count']}\n\nCode Review Graph compact impact:\n```text\n{compact}\n```\n\nYou are the independent reviewer. Work read-only.\n\nContext policy:\n1. Start from the git diff and the CRG impact above.\n2. If more evidence is needed, use code-review-graph with CRG_DATA_DIR already provided by the environment. Prefer `detect-changes --brief`, affected flows, callers/importers/tests, and bounded/minimal context.\n3. Use Graphify only for architecture/community/path questions not answered by CRG.\n4. Use CodeGraph only when exact symbol navigation is materially better.\n5. Use Context7 only for version-sensitive external library/API facts.\n6. Read raw source only for changed/impacted locations needed to prove a finding.\n\nReview only for correctness, security/auth, data integrity, concurrency, backwards compatibility, concrete regression risks, and missing tests. No style-only findings; Ponytail handles final code quality.\n\nReturn at most {caps['findings']} findings. Each finding: SEVERITY | CONFIDENCE | FILE:LINE | CONCRETE FAILURE SCENARIO | EVIDENCE.\nDo not modify files and do not propose broad rewrites.\nFinish with a single-line JSON object containing status (PASS only when no unresolved findings, otherwise FAIL) and a nonempty evidence list of concrete checked facts.\n'''
    enforce_budget(prompt,caps['context_chars'],'review context')
    # An adhoc commit/PR target writes to its own directory under external state,
    # never into the active task's own current-review.md/json -- reviewing an
    # unrelated commit or PR must not clobber the active task's review artifact.
    directory=output_dir or task_state(state)/'state'
    directory.mkdir(parents=True,exist_ok=True)
    (directory/'current-review.md').write_text(prompt)
    save_json(directory/'current-review.json',{'base':base,'profile':profile,'risk':risk,'scope':scope,'created_at':int(time.time())})
    return prompt


def cmd_review(args):
    root=git_root(); state=repo_state(root)
    commit=getattr(args,'commit',None); pr=getattr(args,'pr',None)
    if commit and pr: raise SystemExit('Use --commit or --pr, not both.')
    label=None; output_dir=None
    context:contextlib.AbstractContextManager[Path]
    if commit:
        head,base=resolve_commit_target(root,commit)
        context=temp_worktree(root,head); label=f'commit {head[:12]}'; output_dir=state/'adhoc-reviews'/head[:12]
    elif pr:
        head,base,label=resolve_pr_target(root,pr)
        context=temp_worktree(root,head); output_dir=state/'adhoc-reviews'/f'pr-{pr}'
    else:
        base=resolve_base(root,args.base,state); context=contextlib.nullcontext(root)
    with context as review_root:
        impact=crg_impact(review_root,state,base,refresh=args.refresh,build_if_missing=args.build) if crg_cmd() else ''
        prompt=build_review_prompt(review_root,state,base,args.profile,impact,output_dir=output_dir)
        review_path=(output_dir/'current-review.md') if output_dir else task_state(state)/'state'/'current-review.md'
        print('Review context: ',review_path,f'({label})' if label else '')
        print('CRG:            ','ready' if impact else ('installed but graph unavailable' if crg_cmd() else 'missing'))
        if not args.launch:return
        active_reviewer=get_reviewer(state)
        # A launched review streams a reviewer directly against review_root -- the
        # caller's own checkout for a plain `ai review`, or a disposable worktree for
        # --commit/--pr. Either way this is a live launch, not a schema-checked
        # verdict() call, so it can only ever use a reviewer proven read-only: a
        # reviewer that could write here could make its own review come true, and for
        # --commit/--pr it could do so inside a worktree that is destroyed on exit
        # without a trace.
        if not active_reviewer.read_only:
            raise SystemExit(f'{active_reviewer.name.capitalize()} reviewer has no guaranteed read-only '
                             'sandbox; ai review launches it directly against the checkout (or a disposable '
                             'worktree for --commit/--pr) and cannot risk it mutating what it reviews. '
                             'Configure a read-only reviewer, or use --no-launch to only prepare the prompt.')
        if not active_reviewer.probe_binary or not shutil.which(active_reviewer.probe_binary):
            label=active_reviewer.name.capitalize()
            raise SystemExit(f'{label} CLI missing. Review prompt was prepared; rerun with {label} installed.')
        # The active reviewer builds its own argv for a live, streamed launch --
        # never a hardcoded command here, so any read-only reviewer works, not
        # just Codex. The CRG external-data environment is inherited so the
        # reviewer can query the graph without touching the repo.
        env=crg_env(state)
        p=subprocess.run(active_reviewer.review_argv(review_root,prompt),cwd=review_root,env=env)
        raise SystemExit(p.returncode)
