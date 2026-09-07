<!-- ai-agent-stack:start -->
## Independent reviewer policy

Codex is the **correctness/security challenger**, not the final style reviewer. Ponytail owns final maintainability/style quality.

Follow the policy injected by the global ai-agent-stack. Per-repo state is external at `$AI_REPO_STATE`; the current PR contract is `$AI_REPO_STATE/contracts/current-pr.yml`.

### Context
1. Start with contract + diff.
2. Code Review Graph first for diff impact, blast radius, affected flows, tests and minimal review context.
3. Graphify for macro architecture paths/communities only when CRG is insufficient.
4. CodeGraph for exact symbol navigation only when materially better than CRG.
5. RTK for git/tests/logs/tool output.
6. Raw source only when required to prove a finding.
7. Never crawl broadly without dependency/failure evidence.
8. For Figma-origin UI changes, consume the compact Design Contract first. Query Figma MCP only to prove a material correctness/design-system issue; do not dump entire design files into review context.

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
