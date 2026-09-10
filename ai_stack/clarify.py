"""Pre-flight ambiguity check on the PR contract, before any model is launched.

The `contract` gate already maps every acceptance criterion to source and fresh test
evidence — but it runs at the *end*, so a vague criterion is only discovered after the
builder has already implemented against it and the tokens are spent. The gate's own
pre-check is emptiness only (`acceptance: []`), which a single word satisfies.

This moves that failure left. It is deterministic by design — no model call, no network,
the same contract `analyze_ticket_text` keeps: it reports what a pattern matches and
never claims to have judged whether the criteria are *sufficient*, only that some are
demonstrably unverifiable as written. A human answers; nothing here rewrites the
contract, because a criterion an agent invented is exactly what the contract gate's
"Do not invent acceptance criteria" instruction exists to prevent.

The report is written to `state/clarify.json`, deliberately outside
evidence_fingerprint()'s allowlist: it records a reading of the contract, not an input
any validator is told, so producing one must never invalidate in-flight task evidence
(the same reasoning prompt-history.jsonl and patterns.json are kept out for).
"""
from __future__ import annotations
import json, re, time
from core import git_root, load_json, repo_state, save_json, task_state
from workflow import contract_list_field


# Two tiers, because the cost of being wrong is not symmetric. A BLOCKING phrase makes a
# criterion unverifiable outright: there is no source location or test that settles "works
# correctly", so no validator can ever be pointed at it. A SOFT phrase is only a smell —
# "improve the error message to include the file path" is perfectly verifiable despite the
# word "improve" — so it raises a question a human can dismiss instead of stopping the task.
BLOCKING_TERMS = (
    'works correctly', 'works properly', 'works well', 'works as expected',
    'behaves correctly', 'behaves properly', 'as expected', 'as appropriate',
    'user-friendly', 'user friendly', 'intuitive', 'seamless',
    'properly handled', 'handled properly', 'handled correctly', 'make sure it works',
    'no bugs', 'no issues', 'everything works', 'etc.', 'and so on', 'and more',
)
SOFT_TERMS = (
    'robust', 'performant', 'scalable', 'clean code', 'good performance', 'fast enough',
    'improve', 'improved', 'better', 'nice', 'polish', 'tidy up', 'refactor',
)

MIN_CRITERION_CHARS = 12


# Shared with the contract gate's deterministic must_not_change check, so both read a
# contract list field exactly the same way.
_list_field = contract_list_field


def _objective(text:str)->str:
    m=re.search(r'(?m)^objective:[ \t]*(.*)$', text)
    if not m: return ''
    raw=m.group(1).strip()
    try:
        return str(json.loads(raw)).strip() if raw[:1]=='"' else raw
    except ValueError:
        return raw


def _matching_terms(criterion:str, terms:tuple[str,...])->list[str]:
    low=criterion.lower()
    return [term for term in terms if re.search(rf'(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])', low)]


def analyze_contract(contract_text:str, ticket:dict)->dict:
    """Deterministic read of one contract plus its ticket snapshot. Pure; no I/O."""
    objective=_objective(contract_text)
    acceptance=_list_field(contract_text,'acceptance')
    must_not_change=_list_field(contract_text,'must_not_change')
    blockers=[]; questions=[]

    if not objective or objective.lower() in ('', 'implement the current working task.'):
        blockers.append('The contract has no specific objective; it still holds the placeholder text.')
    if not acceptance:
        blockers.append('The contract has no acceptance criteria. Nothing downstream can be verified against it.')
    for index,criterion in enumerate(acceptance,1):
        if len(criterion.strip())<MIN_CRITERION_CHARS:
            blockers.append(f'Acceptance criterion {index} is too short to be verifiable: {criterion!r}')
            continue
        blocking=_matching_terms(criterion,BLOCKING_TERMS)
        if blocking:
            blockers.append(f'Acceptance criterion {index} is not observable as written '
                            f'({", ".join(repr(t) for t in blocking)}): {criterion!r}')
            questions.append(f'Criterion {index}: what specific, observable outcome would show this is done?')
            continue
        soft=_matching_terms(criterion,SOFT_TERMS)
        if soft:
            questions.append(f'Criterion {index} leans on {", ".join(repr(t) for t in soft)}: '
                             f'is the intended outcome measurable as written? {criterion!r}')

    for marker in ticket.get('clarifications_needed') or []:
        blockers.append(f'Unresolved clarification marker carried in from the source: {marker}')
        questions.append(f'Answer and remove from the contract: {marker}')

    for blocker in ticket.get('blockers_mentioned') or []:
        questions.append(f'Is this dependency still open (advisory; not verified): {blocker}')

    if not must_not_change:
        questions.append('must_not_change is empty: is there genuinely no behavior, interface or file '
                         'this change must leave alone? An empty list is a claim, not a default.')

    return {
        'objective': objective,
        'acceptance_count': len(acceptance),
        'must_not_change_count': len(must_not_change),
        'blockers': blockers,
        'questions': questions,
        'status': 'NEEDS_HUMAN' if blockers else 'READY',
    }


def cmd_clarify(args):
    state=repo_state(git_root()); task=task_state(state)
    contract=task/'contracts'/'current-pr.yml'
    if not contract.is_file():
        raise SystemExit('NEEDS_HUMAN: no PR contract for the active task. Run `ai start <id>` first.')
    ticket=load_json(task/'state'/'ticket.json',{})
    report=analyze_contract(contract.read_text(), ticket if isinstance(ticket,dict) else {})
    payload={'version':1,'generated_at':time.time(),'contract':str(contract),
             'caveat':'Deterministic regex read of the contract; not a verified judgement of sufficiency.',
             **report}
    save_json(task/'state'/'clarify.json',payload)

    if getattr(args,'json',False):
        print(json.dumps(payload,indent=2))
    else:
        print('AI clarify')
        print('  contract:       ',contract)
        print('  objective:      ',report['objective'] or '(none)')
        print('  acceptance:     ',f"{report['acceptance_count']} criteria")
        print('  must_not_change:',f"{report['must_not_change_count']} constraint(s)")
        if report['blockers']:
            print('\nBlockers (resolve in the contract before implementing):')
            for b in report['blockers']: print(f'  - {b}')
        if report['questions']:
            print('\nOpen questions for a human:')
            for q in report['questions']: print(f'  - {q}')
        if not report['blockers'] and not report['questions']:
            print('\nNo unverifiable criteria detected. This is a pattern check, not a sufficiency review.')
        print('\nReport:',task/'state'/'clarify.json')

    if report['blockers']:
        raise SystemExit(f"NEEDS_HUMAN: {len(report['blockers'])} blocker(s); edit {contract} and re-run `ai clarify`.")
