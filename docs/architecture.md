# Architecture

The stack is deliberately **tool-heavy and agent-light**. Deterministic routing/context tools should decide as much as possible before an expensive model is called.

```text
                         PR CONTRACT
                              │
                              ▼
                        ORCHESTRATOR
                 ┌────────────┴────────────┐
                 ▼                         ▼
          Context Governor             Risk Engine
        CodeGraph + RTK + cache       profile + paths
                 │                         │
                 └────────────┬────────────┘
                              ▼
                      Planner if HIGH
                           Opus
                              │
                              ▼
                     Builder — Sonnet
                              │
                              ▼
                 deterministic checks
                              │
                              ▼
                 Regression — Haiku
                              │
                     concrete test gaps
                              │
                 ┌────────────┴────────────┐
                 │                         │
                LOW                   MEDIUM/HIGH
                 │                         │
                 │                         ▼
                 │                  Codex correctness
                 │                         │
                 │              security gate if triggered
                 │                         │
                 └────────────┬────────────┘
                              ▼
                    confirmed fixes only
                              │
                              ▼
                      diff snapshot
                              │
                              ▼
                      Cleanup — Haiku
                              │
                              ▼
                      diff budget check
                              │
                              ▼
                    lint/typecheck/tests
                              │
                              ▼
                    Ponytail — Sonnet
                     read-only quality
                         ┌────┴────┐
                         │         │
                       PASS      FAIL
                         │         │
                         │    one targeted fix
                         │         │
                         └────┬────┘
                              ▼
                      PR Summarizer
                          Haiku
                              │
                              ▼
                    READY / NEEDS_HUMAN
```

## Core invariants

- Contract, tests and direct evidence outrank model opinion.
- CodeGraph decides what code deserves context; RTK compresses command output.
- Context expansion requires evidence.
- Builder owns behavior changes; Cleanup owns mechanical noise; Ponytail is read-only.
- Security review is conditional, not a permanent tax on every PR.
- Cleanup/quality passes are bounded by diff budget.
- Agent loops have hard circuit breakers. Unresolved high-impact disagreements become `NEEDS_HUMAN`.
- PR Summarizer receives only final artifacts and never leaks AI provenance into the PR.
