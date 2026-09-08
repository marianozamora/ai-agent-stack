#!/usr/bin/env python3
"""Generate docs/commands.md from the argparse command tree."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ai_stack'))
from cli import parser  # noqa: E402


def subcommands(command:argparse.ArgumentParser)->dict[str,argparse.ArgumentParser]:
    for action in command._actions:
        if isinstance(action,argparse._SubParsersAction):
            return action.choices
    return {}


def render_command(lines:list[str],path:list[str],command:argparse.ArgumentParser,level:int):
    lines.extend([f"{'#'*level} `{' '.join(path)}`",'',command.description or '', '', '```text',command.format_usage().strip(),'```',''])
    for name,child in subcommands(command).items(): render_command(lines,[*path,name],child,level+1)


def render()->str:
    root=parser()
    action=next(a for a in root._actions if isinstance(a,argparse._SubParsersAction))
    lines=['# Command reference','',
        'Generated from the CLI parser. Run `python3 scripts/generate_command_reference.py` after changing commands or flags.','']
    for name,command in action.choices.items(): render_command(lines,['ai',name],command,2)
    return '\n'.join(lines).rstrip()+'\n'


def main()->int:
    target=ROOT/'docs/commands.md'; generated=render()
    if '--check' in sys.argv:
        if not target.exists() or target.read_text()!=generated:
            print('docs/commands.md is stale; regenerate it.',file=sys.stderr); return 1
        return 0
    target.write_text(generated); print('Generated:',target); return 0


if __name__=='__main__': raise SystemExit(main())
