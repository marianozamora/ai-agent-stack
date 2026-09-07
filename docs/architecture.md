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
                              DONE
```

Reviewer routing is Luna for cheap targeted checks, Terra for normal adversarial
review, Sol for high-risk review, and Astra only for extreme unresolved cases.

For critical long-horizon work, escalate the planner/builder to Fable and the
reviewer to Astra only when justified by blast radius, irreversibility,
unresolved failures, or cross-repo scope.
