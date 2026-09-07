<!-- ai-agent-stack:start -->
## Independent reviewer policy

Codex is the **correctness/security challenger**, not the final style reviewer. Ponytail owns final maintainability/style quality.

Follow `.ai-review/policy.md`, `.ai-review/current-contract.yml`, `.ai-review/context-governor.yml` and `.ai-review/evidence-policy.yml`.

### Context
1. Start with contract + diff.
2. CodeGraph for callers/callees/dependency paths/blast radius.
3. RTK for git/tests/logs/tool output.
4. Raw source only when required to prove a finding.
5. Never crawl broadly without dependency/failure evidence.

### Review scope
Prioritize correctness, security/authorization, data integrity/loss, races, retries/idempotency, rollback/failure handling, backwards compatibility, concrete edge cases and material performance regression.
Ignore style preferences, naming bikeshedding, speculative refactors and unrelated debt.

### Evidence and output budget
- At most 5 findings; prefer 3.
- Default blocker confidence >= 0.80.
- Lower-confidence security/data-loss risks may be flagged for investigation, not asserted as fact.
- Every finding needs severity, confidence, file/location, concrete failure scenario and concise evidence.
- Do not provide a full patch unless explicitly requested.
- A test/reproduction that disproves a finding closes it.
<!-- ai-agent-stack:end -->
