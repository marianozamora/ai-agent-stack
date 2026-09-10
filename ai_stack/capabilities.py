"""Read-only external context tools, behind one narrow registry.

The orchestration prompt used to hardcode a five-tool routing block --
Code Review Graph, Graphify, CodeGraph, Context7, RTK -- unconditionally, in three
places (`lifecycle.build_prompt`'s status lines, its "Context order" list, and its
budget caps) with no check that any of them exist. Worse: two of the five, RTK and
CodeGraph, had no detection anywhere in this codebase at all -- they were prompted
for but never once verified installed. The builder tried to reach for a tool that
was not there, failed, and retried, which cost far more than the prose itself.

A capability is one such tool: read-only, purely a context source, never mutating
the repository it's given. It is not a Builder or a Reviewer (see providers.py) --
it never launches, never returns a verdict, and the orchestration prompt is free to
mention or omit it entirely depending on whether it is actually usable right now.

The controlling rule is silence, not degradation: a capability that is not detected
(or has been explicitly disabled) appears NOWHERE in the built prompt -- not in a
status line, not in the context order, not in a budget cap. The model never learns
it exists, so it never tries to route through it and never burns a retry finding out
it can't. `detect()` is the single source of truth every renderer below reads from.

Detection is by binary presence only (`shutil.which`), exactly like crg_cmd() and
ctx7_cmd() already do -- this registry generalizes what those two ad hoc functions
were doing, it does not replace their command implementations (crg.py/tools.py keep
those). Whether a tool's own cache/graph has been *built* yet (e.g. Graphify's
graph.json) is a separate, later concern already handled where that tool is
actually invoked (`ai graph query` refuses on its own if nothing was built) -- not
something this registry, or the prompt, needs to anticipate.
"""
from __future__ import annotations
from pathlib import Path
import shutil
from core import load_json, save_json


# Registry order is routing priority: CRG (diff-scoped, cheapest and most specific)
# before Graphify (macro/architecture) before CodeGraph (symbol navigation) before
# Context7 (external docs) before RTK (generic git/test/lint/search fallback).
# `order` is the exact routing-priority text this tool contributed to the old
# "Context order:" list, kept verbatim so the all-installed case renders
# byte-for-byte the same list, just contiguously renumbered.
# `cap` names the context_caps() budget key this tool consumes, or None if it
# has no dedicated budget line.
CAPABILITIES: dict[str, dict] = {
    'crg': {
        'binary': 'code-review-graph',
        'label': 'Code Review Graph',
        'cap': None,
        'order': 'Code Review Graph for diff impact, blast radius, affected flows, '
                 'tests and minimal review context',
    },
    'graphify': {
        'binary': 'graphify',
        'label': 'Graphify',
        'cap': 'graph_queries',
        'order': 'Graphify for macro architecture/routes/communities when CRG '
                 'cannot answer the architecture question',
    },
    'codegraph': {
        'binary': 'codegraph',
        'label': 'CodeGraph',
        'cap': None,
        'order': 'CodeGraph for exact symbol navigation when materially better than CRG',
    },
    'context7': {
        'binary': 'ctx7',
        'label': 'Context7',
        'cap': 'docs_queries',
        'order': 'Context7 ONLY for external library/framework/API documentation; '
                 'prefer exact installed version and cached library IDs',
    },
    'rtk': {
        'binary': 'rtk',
        'label': 'RTK',
        'cap': None,
        'order': 'RTK for git/tests/lint/search output',
    },
}


# The budget block historically listed Context7 before Graphify -- the reverse of
# their routing priority in the Context order list above. That is simply how the
# original template was written; preserved verbatim rather than "fixed" so the
# all-installed prompt is provably unchanged.
_BUDGET_LINE_ORDER = ('context7', 'graphify')
_BUDGET_LINE_TEXT = {
    'context7': 'Context7 docs queries <= {}',
    'graphify': 'Graphify structural queries <= {}',
}

_CONTEXT_ORDER_HEAD = 'PR/design contract'
_CONTEXT_ORDER_TAIL = 'raw source reads only when needed to prove/implement something'

# Only relevant once Context7 is in play; folded into capability_block() rather
# than always emitted, per the silence rule.
_EXTERNAL_DOCS_POLICY = '''External docs policy:
- Never rely on memory for version-sensitive library APIs when Context7 can verify them.
- Query only the library/topic needed for the current implementation.
- Do not dump broad documentation into context.
- Repository code and tests remain the source of truth for project-specific behavior.'''


def overrides(state:Path)->dict:
    """Explicit enable/disable decisions, mirroring skills.skill_overrides().

    Kept in a separate file from skill-overrides.json: a capability is not a
    skill (it has no prompt body, no task-type scoring, no lazy-loading), so its
    own on/off switch gets its own state file rather than overloading that one.
    """
    return load_json(state/'capability-overrides.json',{})


def enabled_capabilities(state:Path)->dict:
    """Every known capability plus whether it is enabled, regardless of installed state."""
    disabled=overrides(state)
    return {name:{**meta,'enabled':bool(disabled.get(name,True))} for name,meta in CAPABILITIES.items()}


def detect(state:Path)->list[str]:
    """Present, non-disabled capability names, in registry (routing-priority) order.

    This is the one function every renderer below reads from. A capability is
    "present" only when both its binary is actually on PATH and it has not been
    explicitly disabled -- never one without the other.
    """
    disabled=overrides(state)
    return [name for name,meta in CAPABILITIES.items()
            if disabled.get(name,True) and shutil.which(meta['binary'])]


def render_budget_lines(detected:list[str],caps:dict)->str:
    """Only the budget-cap lines for a detected, cap-bearing capability; '' if none.

    Each returned line is already '- ...' and newline-terminated, so splicing this
    directly between two fixed budget lines in the prompt template introduces no
    blank line when it is empty, and no reformatting when it is not.
    """
    lines=[_BUDGET_LINE_TEXT[name].format(caps[CAPABILITIES[name]['cap']])
           for name in _BUDGET_LINE_ORDER if name in detected]
    return ''.join(f'- {line}\n' for line in lines)


def render_context_order(detected:list[str])->str:
    """The numbered routing list: two fixed endpoints, contiguously renumbered.

    With nothing detected this is exactly two lines -- '1. PR/design contract' and
    the final raw-source fallback -- never the full seven-line list with five
    unusable entries in the middle.
    """
    body=[_CONTEXT_ORDER_HEAD]+[meta['order'] for name,meta in CAPABILITIES.items() if name in detected]+[_CONTEXT_ORDER_TAIL]
    return '\n'.join(f'{i}. {line}' for i,line in enumerate(body,1))


def capability_block(state:Path,detected:list[str])->str:
    """The full 'Context order' section: the tool-routing skill (only when at
    least one capability is detected -- with nothing to route between it would be
    dead weight, not guidance), the numbered order (never empty: its two fixed
    endpoints always survive), and 'External docs policy' appended only when
    Context7 is detected -- the silence rule applied to that whole policy
    paragraph, not just its own budget/order lines.

    Takes `state`, not just `detected`, because the tool-routing skill is loaded
    through the same repo-overridable cascade every other skill uses
    (resolve_skill_file) -- a repository may replace this stack's routing advice
    with its own without forking it, exactly like any other bundled skill.
    """
    from skills import resolve_skill_file
    block='Context order:\n'+render_context_order(detected)
    if detected:
        routing=resolve_skill_file(state,'tool-routing','prompt.md')
        if routing is not None:
            block=routing.read_text().strip()+'\n\n'+block
    if 'context7' in detected:
        block+='\n\n'+_EXTERNAL_DOCS_POLICY
    return block


def cmd_capabilities(args):
    from core import git_root, repo_state
    root=git_root(); state=repo_state(root)
    registry=enabled_capabilities(state)
    if args.capabilities_cmd in (None,'list'):
        present=set(detect(state))
        print('Capabilities')
        for name,meta in registry.items():
            mark='✓' if meta['enabled'] else '·'
            status='ready' if name in present else ('disabled' if not meta['enabled'] else 'missing')
            print(f"{mark} {name:12} {meta['label']:20} {status}")
        return
    name=args.name
    if name not in registry: raise SystemExit(f'Unknown capability: {name}')
    if args.capabilities_cmd in ('enable','disable'):
        o=overrides(state); o[name]=(args.capabilities_cmd=='enable'); save_json(state/'capability-overrides.json',o)
        print(f"{name}: {'enabled' if o[name] else 'disabled'}")
        return
