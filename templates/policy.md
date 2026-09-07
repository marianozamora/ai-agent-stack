# AI Review Policy

Goal: maximize independent review value while minimizing duplicate context and token usage.

## Context acquisition order

Use the cheapest context source that can answer the question:

1. **CodeGraph** for structural questions, callers/callees, dependency paths, code flows and blast radius.
2. **RTK** for git, tests, lint, type checking, shell searches, logs, containers and cloud CLI output.
3. **Raw source reads** only when exact implementation details are necessary or CodeGraph is insufficient.

Do not re-verify good CodeGraph output with broad grep/read loops without a concrete reason.

## Risk levels

### LOW
Examples: copy, formatting, comments, isolated styling, test-only edits, trivial mappings and behavior-neutral config.

Action: cheap/local checks only. No cross-model review by default.

### MEDIUM
Examples: normal business logic, API behavior, integrations, non-destructive data handling, meaningful localized refactors.

Action: Sonnet builder + one Terra adversarial review after checks pass.

### HIGH
Examples: authentication, authorization/RBAC, payments, migrations/schema, destructive operations, concurrency, queues/retries/idempotency, secrets/crypto, IAM/infra, rollback-sensitive changes and data integrity.

Action: Opus planning when useful, Sonnet implementation, then one Sol adversarial review.

### CRITICAL / LONG-HORIZON
Examples: multi-repo migrations, major architecture replacement, irreversible/high-blast-radius changes, unresolved production incidents, or long autonomous work with many connected steps.

Action: escalate to Fable/Opus and Astra only when the evidence warrants it. This must be exceptional.

## Token budget

- Diff-first review.
- One adversarial round by default.
- Maximum 5 findings; prefer 3.
- No broad repository crawl.
- No full alternative implementation in the first review.
- No style/naming feedback unless tied to a concrete defect.
- No repeated ticket explanation when the requirement is already in context.
- Prefer a reproducible test or direct code evidence to model debate.
- Second review only after a confirmed high/critical finding or when the fix changes a high-risk invariant.

## Escalation rules

Escalate because of evidence, not vibes.

Examples of valid escalation signals:
- auth/security/data-integrity boundary
- migration or destructive operation
- concurrency / retry / idempotency behavior
- wide CodeGraph blast radius
- cross-service or cross-repo change
- two failed implementation/debugging attempts
- ambiguous root cause after cheap investigation
- architectural tradeoff with material rollback cost

Do not escalate merely because a diff has many generated/test lines.

## Final PR-readiness pipeline

Correctness and final code quality are separate gates:

1. Implement the solution.
2. Run relevant tests, lint and type checking.
3. Run at most one risk-appropriate Codex correctness review.
4. Fix confirmed findings and verify behavior.
5. Run Cleanup for behavior-preserving mechanical edits.
6. Verify again because Cleanup mutates the diff.
7. Run Ponytail as the read-only final quality gate.
8. On failure, allow one targeted fix round, then Cleanup, checks and Ponytail once more.

A PR is ready only when relevant checks pass, no HIGH/CRITICAL correctness issue
remains, Cleanup passes, and Ponytail reports `PONYTAIL: PASS` and `PR_READY: YES`.
Ponytail must follow project conventions, avoid unrelated refactors, and must
not become a second correctness review.
