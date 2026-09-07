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
