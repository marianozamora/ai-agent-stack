# Plan: capability registry + one `tool-routing` skill

Branch: `worktree-tool-plugin-mode`, based on `main` @ `18949ad`.
Executor: Sonnet. Everything below was verified against the current tree — file
paths and line numbers are real, not guesses. Re-check line numbers after your
first edit, since they shift.

---

## 1. Why this change exists

**Not for the token saving.** Measured before writing this plan:

| | |
|---|---:|
| Prompt template in `build_prompt` | 4,856 chars |
| Lines mentioning an external tool | 11 lines / 898 chars |
| Share of `fast` budget (12,000) | 7.5% |
| Share of `standard` budget (24,000) | 3.7% |
| Share of `strict` budget (36,000) | 2.5% |

Pruning ~900 chars saves ~225 tokens, and only when *every* tool is absent. That
is not the point.

**The point is that the prompt contradicts itself and orders work that cannot be
done.** `ai_stack/lifecycle.py:114` prints:

```
Code Review Graph: missing
```

and 40 lines later, unconditionally:

```
Context order:
2. Code Review Graph for diff impact, blast radius, affected flows, tests and minimal review context
3. Graphify for macro architecture/routes/communities when CRG cannot answer the architecture question
4. CodeGraph for exact symbol navigation when materially better than CRG
6. RTK for git/tests/lint/search output
```

The builder is instructed to route through five tools with no check that any of
them exist. It tries, fails, retries, and burns far more than the 225 tokens the
text itself costs. The waste being removed is the *flailing*, not the prose.

Worse, verified by grep:

- **`rtk` has no detection anywhere in `ai_stack/`.** It appears only in prompt
  text (`lifecycle.py:156`) and in `ai doctor`'s hardcoded probe list
  (`repo.py:165`).
- **`codegraph` is the same** (`lifecycle.py:154`, `gates.py:139`, `repo.py:165`).

So two of the five advertised tools were never detectable at all.

This closes two open ROADMAP items:

- `[ ] Driver interfaces for external tools (Context7, Graphify, CodeGraph, RTK)`
- `[ ] Capability/plugin interfaces`

`ai_stack/providers.py` already did exactly this for the builder/reviewer roles —
two narrow interfaces, selection by env > `repo.json` > default. **Read it first
and follow its shape.** This is the same move for read-only context tools.

---

## 2. Decisions already made — do not relitigate

1. **One new skill**, `tool-routing`, holding the invariant routing policy. The
   eight bundled skills are untouched.
   *(Rejected: merging the 8 into 1. `select_skills` already injects only 1–3 per
   task by profile — about 700 chars — while the 8 together are 2,742. Merging
   would raise per-task tokens ~290% and make lazy metadata routing pointless.)*

2. **Total silence for an absent capability.** If a tool is not installed it
   appears *nowhere* in the prompt: not in the context order, not in the budget,
   not as a status line. The model never learns it exists.

---

## 3. Design

### 3.1 New module: `ai_stack/capabilities.py`

A capability is a read-only external context tool. It declares how to detect
itself and the one prompt line that explains when to reach for it.

```python
CAPABILITIES = {
  'crg':       {'binary': 'code-review-graph', 'label': 'Code Review Graph',
                'cap': None,            'order': '...'},
  'graphify':  {'binary': 'graphify',          'label': 'Graphify',
                'cap': 'graph_queries', 'order': '...'},
  'codegraph': {'binary': 'codegraph',         'label': 'CodeGraph',
                'cap': None,            'order': '...'},
  'context7':  {'binary': 'ctx7',              'label': 'Context7',
                'cap': 'docs_queries',  'order': '...'},
  'rtk':       {'binary': 'rtk',               'label': 'RTK',
                'cap': None,            'order': '...'},
}
```

Take the `order` strings verbatim from the current `lifecycle.py:151-156` so this
change is provably behaviour-preserving when everything *is* installed. The
dict order is the routing priority and must stay CRG → Graphify → CodeGraph →
Context7 → RTK.

Functions:

| Function | Returns |
|---|---|
| `detect(state)` | present, non-disabled capability names in registry order |
| `overrides(state)` | `state/'capability-overrides.json'`, mirroring `skill_overrides()` |
| `render_context_order(detected)` | the numbered list, contiguously renumbered |
| `render_budget_lines(detected, caps)` | only the cap lines whose tool is present |
| `capability_block(state, detected, caps)` | the whole replacement section |

`render_context_order` **always** keeps the two fixed endpoints — `1. PR/design
contract` first and `raw source reads only when needed to prove/implement
something` last — and renumbers whatever survives in between. With nothing
installed that is 2 lines, not 7.

### 3.2 `evidence_fingerprint()` — read this before writing the override file

`gates.py:31-36` is an explicit allowlist of files that change *what a
gate/validator is told*. `skill-overrides.json` is in it. By exactly the same
reasoning **`capability-overrides.json` must be added to that list**: disabling a
capability changes the prompt a validator receives, so evidence recorded under
the old set must not stay fresh.

Do **not** add `current-plan.json` or `current-run.md` — they are already outside
the allowlist and that is deliberate.

### 3.3 The skill

`skills/tool-routing/` — `skill.json`, `prompt.md`, `README.md`.

`prompt.md` holds only what is true regardless of which tools exist: progressive
disclosure, one tool per question, reuse cached repo state, raw source last.
Target ≤ 400 chars, matching the other bundled skills (310–381).

**It must not go through `select_skills()`.** In the `fast` profile the skills cap
is 1 — tool routing would displace `tdd` or `diagnosing-bugs`. Add
`"selectable": false` to its registry entry and have `select_skills()` skip
entries carrying it; `ai skill list` still shows it. It lives under `skills/` so
a repository can override it through the cascade `resolve_skill_file()` already
provides.

Register it in `skills/registry.json`.

### 3.4 Wiring

`ai_stack/lifecycle.py`, inside `build_prompt`:

| Line(s) today | Action |
|---|---|
| 100 `graph=str(...)` | delete if unused after the rest |
| 113 `Graphify graph: ...` | delete (silence rule) |
| 114 `Code Review Graph: ...` | delete (silence rule) |
| 132 `- Prefer one tool per question; do not call Graphify, CRG, ...` | reword generically: `- Prefer one tool per question; never ask two tools the same thing.` |
| 143-144 Context7 / Graphify cap lines | replace with `render_budget_lines(...)` |
| 150-157 `Context order:` block | replace with `render_context_order(...)` |
| 159-163 `External docs policy:` block | move into the Context7 capability; emit only when Context7 is detected |

Keep `crg_text` and the CRG impact/risk-elevation logic at lines 96-99 exactly as
is — that is real behaviour, not prompt text.

Record the detected set in `current-plan.json` (line 186) next to the existing
`crg` object, so `ai plan` output and any later consumer see the same set the
prompt was built from.

`ai_stack/repo.py:165` — `ai doctor`'s hardcoded probe list drops its tool names
and reads `CAPABILITIES` instead. Keep `git`, `python3`, `node`, `gh` and the
provider binaries: those are not capabilities.

### 3.5 Optional, only if the above lands clean

`ai_stack/crg.py:166` (the adversarial reviewer prompt) carries the *same*
hardcoded 6-item tool list with the same defect. `gates.py:139` is the mildest
case — it already says "when `.codegraph` exists". Treat both as a follow-up PR,
not this one. Note them in the PR description.

---

## 4. Order of work

1. Read `ai_stack/providers.py` end to end. Match its structure and its
   docstring style — the *why*, not the *what*.
2. Write `ai_stack/capabilities.py` with `detect` / `render_*` pure and testable
   (no I/O beyond `shutil.which` and the overrides file).
3. Write `tests/test_capabilities.py` before wiring anything in. See §5.
4. Create `skills/tool-routing/` and register it; add `selectable` handling to
   `select_skills()`.
5. Rewire `build_prompt`.
6. Add `capability-overrides.json` to the `evidence_fingerprint()` allowlist.
7. Rewire `ai doctor`.
8. Optional CLI: `ai capabilities list|enable|disable`, mirroring `ai skill`.
   Skip it if time is short — the registry is the deliverable.
9. Regenerate `docs/commands.md` via `python3 scripts/generate_command_reference.py`
   **only if** you added a CLI command.
10. README section, CHANGELOG entry, tick the two ROADMAP items.

---

## 5. Tests

New `tests/test_capabilities.py`:

- `detect()` with `shutil.which` patched to find nothing → `[]`.
- `detect()` finding all five → registry order preserved.
- A disabled capability is excluded even when its binary is present.
- `render_context_order([])` → exactly the two fixed endpoints, numbered `1.`
  and `2.`.
- Numbering is contiguous for every subset (parametrise over a few).
- **The silence test, the important one:** for each capability, build a prompt
  with only that one absent and assert its `label` appears nowhere in the
  output. This is the invariant the whole change exists to hold.
- `render_budget_lines` omits `docs_queries` when Context7 is absent and
  `graph_queries` when Graphify is absent.

Extend `tests/test_performance_budgets.py`: the prompt with zero capabilities is
strictly shorter than with all five, and both stay inside `context_chars` for
every profile.

Extend the fingerprint tests in `tests/test_gates.py`: writing
`capability-overrides.json` changes `evidence_fingerprint()`.

Do not delete existing prompt assertions — if one fails because it asserted on a
tool line that is now conditional, make the test install the capability rather
than weakening the assertion.

---

## 6. Verification before opening the PR

```bash
python3 -m ruff check ai_stack/ tests/
python3 -m mypy ai_stack/
python3 -m pytest tests/ -q          # baseline on main: 522 passed, 24 subtests
```

Then a real end-to-end run, because the last two PRs each had a bug the suite did
not catch and a manual walk did. Build a throwaway repo outside the checkout,
`ai init`, `ai start`, `ai plan`, and read the generated
`state/current-run.md` twice:

- with `PATH` stripped of all five binaries → no tool name appears anywhere;
- with at least one present → it appears, numbered contiguously.

---

## 7. Traps

- **Do not put framework state in the checkout.** `capability-overrides.json`
  goes in repo external state, like `skill-overrides.json`.
- **Do not make the tool-routing skill selectable.** §3.3 explains why; a `fast`
  profile with one skill slot is the failure case.
- **Do not detect inside a loop.** Call `detect()` once per `build_prompt` and
  pass the result down.
- **`enforce_budget` still applies.** The prompt only shrinks, so this should
  never trip — if it does, something is being emitted twice.
- **Preserve the `order` strings verbatim** for the all-installed case, so the
  diff is provably behaviour-preserving where tools exist.
- `ai_stack/tools.py` and `ai_stack/crg.py` keep their command implementations.
  This change only moves *detection and prompt rendering* into the registry.
