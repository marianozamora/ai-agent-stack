#!/usr/bin/env python3
"""Export this stack's bundled skill library as standalone `SKILL.md` files.

For a project that wants only the prompt library -- not this stack's routing engine,
gates, or CLI. Run directly from a clone of this repository, no `pip install` needed:

    python3 scripts/export_skills.py --out /path/to/project/.claude/skills

Exports every enabled, selectable bundled skill by default; pass --skill NAME (repeatable)
to export a subset. This is the zero-install entry point to `ai_stack.skills.export_skills()`
-- `ai skill export` (once the stack is installed) does the same thing, additionally
layering in a repository's own skills if run inside one with `ai init` already done.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import skills  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument('--out', required=True, help='Directory to write SKILL.md files into')
    parser.add_argument('--skill', action='append', default=None,
                         help='Export only this skill (repeatable); default: every bundled one')
    args = parser.parse_args()
    out_dir = Path(args.out).expanduser().resolve()
    exported = skills.export_skills(None, out_dir, args.skill)
    print(f'Exported {len(exported)} skill(s) to {out_dir}:')
    for name in exported:
        print(' ', name)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
