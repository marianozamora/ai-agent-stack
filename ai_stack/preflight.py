"""`ai preflight` -- prove the environment is up before anything spends tokens.

On danssme #95 Docker was down: the `regression` gate's pgTAP step failed with no visible
error, the gate recorded a FAIL, and `ai loop` paid for three builder rounds trying to
diagnose an environment it was not allowed to start. A preflight is a cheap command the
repository declares (`supabase status`, `docker info`, a health URL) that `ai loop` runs
before every builder round and `ai pipeline` before any gate. It costs no tokens, and a
failure stops with NEEDS_HUMAN instead of being judged as a failure of the change.

Kept in its own file, not in validators.json: that file is part of every gate's evidence
fingerprint, and declaring how to check the environment does not change what a gate judged.
"""
from __future__ import annotations
import json, subprocess
from pathlib import Path
from core import git_root, load_json, repo_state, require_human, save_json

OUTPUT_CHARS = 600
# What a validator command reveals about the services it needs, and the probe that proves
# each one is up. Only suggested, never configured without a human.
SUGGESTIONS = (('supabase', ['supabase', 'status']), ('docker', ['docker', 'info']))


def preflight_path(state:Path)->Path:
    return state/'preflight.json'


def load_preflight(state:Path)->list[dict]:
    checks = load_json(preflight_path(state), {}).get('checks')
    return [c for c in checks or [] if isinstance(c, dict) and isinstance(c.get('command'), list) and c['command']]


def suggestions(validators:dict)->list[list[str]]:
    """Probes for the services the configured checks visibly depend on."""
    text = ' '.join(' '.join(item.get('command') or []) for item in validators.values() if isinstance(item, dict))
    return [probe for word, probe in SUGGESTIONS if word in text]


def preflight_failure(root:Path, state:Path)->str|None:
    """Run every declared check; the first failure as a NEEDS_HUMAN message, else None."""
    for check in load_preflight(state):
        command = check['command']; timeout = check.get('timeout') or 60
        try:
            done = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=timeout)
            code, output = done.returncode, (done.stdout + done.stderr).strip()
        except subprocess.TimeoutExpired:
            code, output = None, f'timed out after {timeout}s'
        except OSError as exc:
            code, output = None, str(exc)
        if code != 0:
            return (f"NEEDS_HUMAN: environment not ready: `{' '.join(command)}` "
                    + (f'exited {code}' if code is not None else 'could not run') + '; nothing was spent.\n'
                    + (f'  {output[-OUTPUT_CHARS:]}\n' if output else '')
                    + '  Start the service it checks, then rerun. `ai preflight` runs the checks on their own.')
    return None


def cmd_preflight(args):
    root = git_root(); state = repo_state(root)
    action = args.action or 'run'
    if action == 'set':
        require_human('Preflight configuration')
        command = args.command[1:] if args.command[:1] == ['--'] else args.command
        if not command: raise SystemExit('ai preflight set needs a command after --, e.g. `-- supabase status`.')
        if args.timeout <= 0: raise SystemExit('Timeout must be positive.')
        checks = [c for c in load_preflight(state) if c['command'] != command]
        save_json(preflight_path(state), {'version': 1, 'checks': checks + [{'command': command, 'timeout': args.timeout}]})
        print('Saved:', preflight_path(state))
    elif action == 'clear':
        require_human('Preflight configuration')
        preflight_path(state).unlink(missing_ok=True)
        print('Cleared.')
    checks = load_preflight(state)
    if action in ('show', 'set', 'clear'):
        print(json.dumps({'checks': checks}, indent=2))
    if not checks:
        from gates import validator_config
        hints = suggestions(validator_config(state)['validators'])
        print('No preflight checks configured.'
              + ''.join(f"\n  suggested: ai preflight set -- {' '.join(probe)}" for probe in hints))
        return
    if action == 'run':
        failure = preflight_failure(root, state)
        if failure: raise SystemExit(failure)
        print(f'Preflight: {len(checks)} check(s) passed.')
