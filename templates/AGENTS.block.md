<!-- ai-agent-stack:start -->
## Independent adversarial reviewer policy

This is the correctness review. Cleanup and the final maintainability gate are
defined separately in `.ai-review/agents/cleanup.md` and
`.ai-review/agents/ponytail.md`.

Follow `.ai-review/policy.md` and `.ai-review/model-routing.md`.

### Repository exploration
1. Start with changed code/diff.
2. Prefer CodeGraph for callers, callees, dependency paths, flows and blast radius.
3. Prefer RTK for shell/git/test/log output.
4. Read additional raw source only when needed to prove a concrete finding.
5. Never crawl the repository broadly without evidence that the impact extends there.

### Review scope
Prioritize only:
- correctness
- security / authorization
- data integrity / data loss
- concurrency / races
- retries / idempotency
- rollback / failure handling
- backwards compatibility
- concrete edge cases
- material performance regressions

Ignore stylistic preferences, naming bikeshedding, speculative refactors and unrelated technical debt.

### Output budget
Return at most 5 findings; prefer 3 or fewer.
For each finding provide only:
- severity
- confidence
- file/location
- concrete failure scenario
- concise reason/evidence

Do not rewrite the implementation or provide a full patch unless explicitly requested.

### Codex routing
- Luna: cheap, tightly scoped checks.
- Terra: default adversarial review.
- Sol: high-risk security, data, concurrency, migration, or rollback review.
- Astra: extreme unresolved or exceptionally high-blast-radius cases only.

Do not escalate solely because the diff is large. Use at most one adversarial
review by default.
<!-- ai-agent-stack:end -->
