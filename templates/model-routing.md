# Model Routing

The names below are **roles**. Map them to the exact model IDs available in your Claude/Codex installation.

## Claude

### CHEAP — Haiku
Use for:
- ticket triage
- risk classification
- summarizing logs/test failures
- simple mechanical edits
- locating likely affected areas after CodeGraph context
- commit/release summaries

Do not use for important architecture decisions by default.

### DEFAULT BUILDER — Sonnet
Use for most implementation work:
- APIs and services
- frontend/backend features
- tests
- localized refactors
- normal integrations
- ordinary debugging

This should handle the majority of development tasks.

### DEEP — Opus
Use selectively for:
- architecture and design decisions
- difficult root-cause analysis
- auth/RBAC/security-sensitive reasoning
- concurrency/transactions
- distributed systems
- risky migrations
- large refactor strategy

Prefer **Opus plans, Sonnet implements** when the implementation itself is straightforward.

### LONG-HORIZON — Fable
Use exceptionally for:
- long autonomous multi-step work
- multi-repo migration
- major architecture replacement
- large greenfield module from a detailed spec
- tasks where maintaining coherence over many connected steps matters more than per-token cost

Do not make this the daily coding default.

## Codex reviewer tiers

Keep exact model IDs configurable because the Codex lineup changes.

### CHEAP REVIEWER — Luna
Use for:
- sanity checks
- obvious regression checks
- targeted test-gap review
- low-cost second opinion

### BALANCED REVIEWER — Terra
Default for MEDIUM changes:
- business logic
- API changes
- integrations
- normal regressions/backwards compatibility

### STRONG REVIEWER — Sol
Default for HIGH changes:
- auth/security
- data integrity
- migrations
- concurrency
- retries/idempotency
- destructive or rollback-sensitive behavior

### EXTREME REVIEWER — Astra
Only for critical unresolved/high-blast-radius work or when strong Claude + strong Codex still disagree on a material issue.

## Recommended routing table

| Risk | Planning | Builder | Reviewer | Review count |
|---|---|---|---|---:|
| LOW | Haiku/Sonnet | Haiku or Sonnet | none; Luna only if justified | 0 |
| MEDIUM | Sonnet | Sonnet | Terra | 1 |
| HIGH | Opus when needed | Sonnet | Sol | 1 |
| CRITICAL | Opus/Fable | Sonnet/Fable | Astra only if justified | 1, max 2 if justified |

## Anti-patterns

Avoid:
- Opus/Fable for every implementation
- strongest Codex model for every diff
- Claude and Codex both rewriting the same feature independently
- more than two cross-model loops
- escalating models before cheap tests/context gathering
- feeding both agents the entire repository


## Final PR agents

### CLEANUP — Haiku by default
A behavior-preserving mutating pass after correctness fixes. Remove temporary/AI residue, obvious comments, dead local scaffolding, debug output and safe lint/format noise. Escalate to Sonnet only if deciding whether something is safe to remove requires non-trivial code reasoning.

### PONYTAIL — Sonnet by default
The final **read-only PR quality gate**. Review the final diff against the project's actual conventions and architecture. Check maintainability, code style, cohesion/coupling, appropriate SOLID or functional-programming principles, tests, types and complexity. Use Opus only when the quality question itself is architectural.

Ponytail must not impose SOLID on a functional codebase or functional patterns on an OO codebase. Project evidence wins over generic preferences.

### REGRESSION SCOUT — Haiku
Cheap read-only pass after implementation. It uses contract + diff + CodeGraph blast radius to identify at most 3 plausible existing behaviors that may regress and the smallest tests that would prove/disprove them.

### PR SUMMARIZER — Haiku
Runs only after PR readiness passes. Reads the contract, final diff/stat and actual checks; produces PR title/body without replaying chat history or mentioning AI provenance.

### CONDITIONAL SECURITY GATE — Sol by default
Not a permanent extra agent call. Trigger only when the diff crosses a real
trust boundary (auth/RBAC/tenant isolation, secrets/crypto, SQL/untrusted input,
IAM, payments/sensitive data). Use Astra only for an unresolved critical case.
