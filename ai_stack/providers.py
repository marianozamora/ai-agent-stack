"""Builder and reviewer providers behind two narrow interfaces.

The stack hard-coded Claude for implementation and Codex for review in three
places. Nothing about the workflow requires either: what the workflow needs is a
*builder* (interactive, mutating, takes over the terminal) and a *reviewer*
(non-interactive, read-only, returns schema-validated JSON plus reported usage).

The reviewer contract is the demanding one, and it is not negotiable:

  * read-only — it must not be able to modify the checkout it reviews;
  * schema-conforming — output validated independently of process success;
  * usage-reporting — token counts come from the provider, never estimated.

A provider that cannot guarantee read-only is not a valid reviewer. `ai providers
doctor` says so rather than degrading to one that mutates the repository, because
a reviewer with write access could make its own verdict come true.
"""
from __future__ import annotations
import json, os, shutil, subprocess, sys, tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol


class Builder(Protocol):
    name:str
    def available(self)->bool: ...
    def launch(self,prompt:str,root:Path,env:dict)->None: ...


class Reviewer(Protocol):
    name:str
    read_only:bool
    def available(self)->bool: ...
    def verdict(self,root:Path,review_dir:Path,name:str,prompt:str,
                schema:dict,checker:Callable[[Any],dict],timeout:int|None)->dict: ...
    def review_argv(self,root:Path,prompt:str)->list[str]: ...


class ClaudeBuilder:
    """Replaces this process with the Claude CLI, handing it the built prompt."""
    name='claude'; executable='claude'

    def available(self)->bool: return bool(shutil.which(self.executable))

    def launch(self,prompt:str,root:Path,env:dict)->None:
        path=shutil.which(self.executable)
        if not path: raise SystemExit(f'{self.name.capitalize()} CLI missing. Use ai plan to only prepare.')
        # execvpe replaces this process with an interactive Claude session, which needs
        # a real terminal to talk to. Without one (a script, CI, a pipe) this used to
        # hang silently forever instead of failing - the builder started, found no TTY
        # to read from, and just sat there with zero output until something killed it.
        if not sys.stdin.isatty():
            raise SystemExit(f'{self.name.capitalize()} needs an interactive terminal; '
                             'this session has none (piped, scripted, or CI). '
                             'Use ai plan / --plan-only to only prepare the prompt instead.')
        os.execvpe(path,[path,prompt],env)


CODEX_READONLY_SANDBOX=['exec','-s','read-only']  # shared by verdict() and review_argv(): never write to the checkout


class CodexReviewer:
    """Codex in its read-only sandbox, with a required output schema.

    Invoked with `-s read-only` and `approval_policy="never"`, so it cannot write
    to the checkout or stop to ask. A gate caller relies on the enclosing `ai gate`
    process-group timeout (see workflow.execute) instead of passing one here; a
    caller with no enclosing gate — `ai profile --deep` — must pass one.
    """
    name='codex'; executable='codex'
    probe_binary='codex'  # the binary a caller checks for before invoking verdict()
    read_only=True

    def available(self)->bool: return bool(shutil.which(self.executable))

    def verdict(self,root,review_dir,name,prompt,schema,checker,timeout=None):
        path=shutil.which(self.executable)
        if not path: raise ValueError('Codex CLI missing. Install/authenticate Codex or configure a custom validator.')
        return run_codex_json(path,root,review_dir,name,prompt,schema,checker,timeout)

    def review_argv(self,root:Path,prompt:str)->list[str]:
        """Argv for a live, streamed review (ai review --launch): interactive-ish
        output straight to the terminal, unlike verdict()'s schema-checked run_codex_json."""
        return [self.executable,*CODEX_READONLY_SANDBOX,'-C',str(root),prompt]


class CommandReviewer:
    """A configured command that reads the prompt on stdin and returns one JSON line.

    Deliberately the same protocol the `json` validator adapter already defines, so
    a custom reviewer is not a new format to learn. `read_only` is False because the
    stack cannot prove an arbitrary command's sandboxing — `ai providers doctor`
    reports that rather than pretending otherwise.
    """
    read_only=False

    def __init__(self,command:list[str],name:str='command'):
        self.command=command; self.name=name
        self.probe_binary=command[0] if command else None

    def available(self)->bool: return bool(self.command) and bool(shutil.which(self.command[0]))

    def review_argv(self,root:Path,prompt:str)->list[str]:
        raise ValueError('command reviewers are not read-only; cannot launch a review')

    def verdict(self,root,review_dir,name,prompt,schema,checker,timeout=None):
        if not self.available(): raise ValueError(f'Reviewer command not executable: {" ".join(self.command)}')
        diagnostics=Path(review_dir)/f'{name}-events.jsonl'
        try:
            result=subprocess.run(self.command,input=prompt,text=True,cwd=root,capture_output=True,timeout=timeout)
        except subprocess.TimeoutExpired as err:
            raise ValueError(f'Reviewer timed out after {timeout}s.') from err
        diagnostics.write_text(result.stdout+result.stderr)
        if result.returncode: raise ValueError(f'Reviewer exited {result.returncode}; diagnostics: {diagnostics}')
        lines=[line for line in result.stdout.strip().splitlines() if line.strip()]
        if not lines: raise ValueError(f'Reviewer produced no output; diagnostics: {diagnostics}')
        try: value=checker(json.loads(lines[-1]))
        except ValueError as err: raise ValueError(f'{err}; diagnostics: {diagnostics}') from err
        return value


def run_codex_json(executable:str,root:Path,review_dir:Path,name:str,prompt:str,
                   schema:dict[str,Any],checker:Callable[[Any],dict[str,Any]],
                   timeout:int|None=None)->dict[str,Any]:
    """Invoke Codex read-only/ephemeral with a required output schema; never mutates the checkout."""
    with tempfile.TemporaryDirectory(prefix='codex-',dir=review_dir) as temporary_directory:
        directory=Path(temporary_directory)
        schema_path=directory/'schema.json'; final=directory/'final.json'
        schema_path.write_text(json.dumps(schema))
        events=directory/'events.jsonl'; diagnostics=review_dir/f'{name}-events.jsonl'
        try:
            with events.open('w') as output:
                try:
                    result=subprocess.run([executable,*CODEX_READONLY_SANDBOX,
                        '-c','approval_policy="never"','--ephemeral','--json',
                        '--output-schema',str(schema_path),'--output-last-message',str(final),'-'],
                        input=prompt,text=True,cwd=root,stdout=output,stderr=subprocess.STDOUT,timeout=timeout)
                except subprocess.TimeoutExpired as err:
                    raise ValueError(f'Reviewer timed out after {timeout}s; diagnostics: {diagnostics}') from err
        finally:
            # Keep process diagnostics externally for failures, including a timeout or missing output.
            diagnostics.write_bytes(events.read_bytes())
        if result.returncode: raise ValueError(f'Reviewer exited {result.returncode}; diagnostics: {diagnostics}')
        if not final.is_file() or final.stat().st_size>64000:
            raise ValueError(f'Missing or oversized reviewer output; diagnostics: {diagnostics}')
        value=checker(json.loads(final.read_text()))
        usage:dict[str,int]={}
        for line in events.read_text(errors='replace').splitlines():
            try:
                event=json.loads(line)
                if isinstance(event,dict) and event.get('type')=='turn.completed':
                    reported=event.get('usage',{})
                    if isinstance(reported,dict):
                        for key in ('input_tokens','output_tokens'):
                            reported_value=reported.get(key)
                            if type(reported_value) is int and reported_value>=0:
                                usage[key]=usage.get(key,0)+reported_value
            except ValueError: continue
        if usage: value['usage']=usage
        return value


BUILDERS:dict[str,Callable[[],Any]]={'claude':ClaudeBuilder}
REVIEWERS:dict[str,Callable[[],Any]]={'codex':CodexReviewer}


def configured(state:Path)->dict:
    """Provider choice: environment override first, then repo.json, then the defaults."""
    from core import load_json
    stored=load_json(state/'repo.json',{}).get('providers',{})
    if not isinstance(stored,dict): stored={}
    return {'builder':os.environ.get('AI_BUILDER') or stored.get('builder') or 'claude',
            'reviewer':os.environ.get('AI_REVIEWER') or stored.get('reviewer') or 'codex',
            'reviewer_command':stored.get('reviewer_command')}


def builder(state:Path)->Any:
    choice=configured(state)['builder']
    if choice not in BUILDERS: raise SystemExit(f'Unknown builder {choice!r}. Available: '+', '.join(BUILDERS))
    return BUILDERS[choice]()


def reviewer(state:Path)->Any:
    settings=configured(state); choice=settings['reviewer']
    if choice=='command':
        command=settings.get('reviewer_command')
        if not command: raise ValueError('Reviewer "command" selected but no reviewer_command is configured.')
        return CommandReviewer(command)
    if choice not in REVIEWERS:
        raise ValueError(f'Unknown reviewer {choice!r}. Available: '+', '.join([*REVIEWERS,'command']))
    return REVIEWERS[choice]()


def cmd_providers(args):
    from core import git_root, load_json, repo_state, save_json
    root=git_root(); state=repo_state(root)
    if args.providers_cmd=='set':
        meta=load_json(state/'repo.json',{})
        settings=meta.get('providers') if isinstance(meta.get('providers'),dict) else {}
        if args.builder:
            if args.builder not in BUILDERS: raise SystemExit(f'Unknown builder {args.builder!r}. Available: '+', '.join(BUILDERS))
            settings['builder']=args.builder
        if args.reviewer:
            if args.reviewer not in REVIEWERS and args.reviewer!='command':
                raise SystemExit(f'Unknown reviewer {args.reviewer!r}. Available: '+', '.join([*REVIEWERS,'command']))
            settings['reviewer']=args.reviewer
        if args.reviewer_command:
            command=args.reviewer_command[1:] if args.reviewer_command[:1]==['--'] else args.reviewer_command
            if not command: raise SystemExit('A reviewer command requires an executable after --.')
            settings['reviewer_command']=command
        meta['providers']=settings
        save_json(state/'repo.json',meta)
        print('Saved:',state/'repo.json')
    settings=configured(state)
    if getattr(args,'json',False): print(json.dumps(settings,indent=2)); return
    print('Builder:  '+settings['builder'])
    print('Reviewer: '+settings['reviewer']
          +(' '+' '.join(settings['reviewer_command']) if settings.get('reviewer_command')
            and settings['reviewer']=='command' else ''))
    if args.providers_cmd!='doctor': return
    active_builder=builder(state)
    print(f'\n{"✓" if active_builder.available() else "·"} builder {active_builder.name}: '
          +('installed' if active_builder.available() else 'MISSING'))
    try: active_reviewer=reviewer(state)
    except ValueError as exc:
        print(f'· reviewer: {exc}'); raise SystemExit(1) from None
    print(f'{"✓" if active_reviewer.available() else "·"} reviewer {active_reviewer.name}: '
          +('installed' if active_reviewer.available() else 'MISSING'))
    print('  read-only sandbox: '+('guaranteed by the provider' if active_reviewer.read_only else
          'NOT guaranteed — a reviewer that can write could make its own verdict come true'))
