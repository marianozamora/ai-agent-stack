# Architecture

```text
                         TASK
                           │
                           ▼
                 cheap triage/context
                 Haiku + CodeGraph
                           │
                ┌──────────┼──────────┐
                │          │          │
               LOW       MEDIUM      HIGH
                │          │          │
                ▼          ▼          ▼
          Haiku/Sonnet   Sonnet    Opus strategy
                │          │          │
                │          │          ▼
                │          │        Sonnet
                └────┬─────┴──────────┘
                     ▼
            RTK tests/lint/typecheck
                     │
          ┌──────────┼──────────┐
          │                     │
         LOW               MEDIUM/HIGH
          │                     │
          ▼                     ▼
        DONE             Codex reviewer
                                │
                        CodeGraph blast radius
                                │
                                ▼
                        ≤ 5 concrete findings
                                │
                                ▼
                         Claude verifies
                                │
                                ▼
                              tests
                                │
                                ▼
                       Cleanup (Haiku)
                   behavior-preserving edits
                                │
                                ▼
                      RTK checks repeated
                                │
                                ▼
                    Ponytail (read-only)
                         Sonnet default
                          ┌─────┴─────┐
                          │           │
                        PASS         FAIL
                          │           │
                          ▼           ▼
                       PR READY   one targeted fix,
                                  Cleanup + checks,
                                  then final Ponytail
```

Reviewer routing is Luna for cheap targeted checks, Terra for normal adversarial
review, Sol for high-risk review, and Astra only for extreme unresolved cases.

For critical long-horizon work, escalate the planner/builder to Fable and the
reviewer to Astra only when justified by blast radius, irreversibility,
unresolved failures, or cross-repo scope.

Cleanup answers whether the diff contains temporary or mechanical residue and
may edit only when behavior is preserved. Ponytail answers whether the exact
final diff fits the project's architecture, conventions, and maintainability
bar. Project evidence takes precedence over generic SOLID or FP preferences.
