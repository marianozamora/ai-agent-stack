<!-- ai-agent-stack:start -->
## AI agent stack policy

Follow `.ai-review/policy.md`, `.ai-review/model-routing.md`, `.ai-review/profiles.yml`, `.ai-review/context-governor.yml` and `.ai-review/evidence-policy.yml` for non-trivial work.

### Before implementation
- Use `.ai-review/current-contract.yml` as the compact source of truth. Create it with `./bin/ai-contract init` when missing for non-trivial work.
- Read `.ai-review/project-profile.json` for cached tooling/style facts; do not rediscover them repeatedly.
- Prefer CodeGraph for structural context and RTK for command output.
- Respect context and agent-call budgets for the selected profile (`fast`, `standard`, `strict`).

### Model discipline
- Haiku: classification, regression scout, cleanup, PR summary and mechanical work.
- Sonnet: default builder and Ponytail.
- Opus: deep planning/debugging/arbiter only when evidence justifies escalation.
- Fable: exceptional long-horizon or multi-repo work.
- Escalate from concrete risk, dependency impact, ambiguity or failed attempts—not diff size alone.

### Evidence discipline
- tests/static analysis/reproduction > CodeGraph/source evidence > model reasoning
- findings below configured confidence thresholds are non-blocking unless high-impact security/data-loss investigation is warranted
- never resolve a model disagreement by adding more model loops when a deterministic test can decide it

### PR readiness gates
1. Contract and context plan.
2. Implement + relevant checks.
3. Regression scout.
4. Risk-based Codex correctness review; conditional security gate when triggered.
5. Fix only confirmed findings + verify.
6. `./bin/ai-diff-budget snapshot`, then Cleanup.
7. `./bin/ai-diff-budget check` + checks.
8. Ponytail read-only gate.
9. At most one targeted Ponytail correction loop.
10. PR Summarizer only after PASS; never mention AI provenance in PR text.

If a destructive/security/architecture disagreement remains after the configured budget, return `NEEDS_HUMAN`; do not enter an unbounded agent loop.
<!-- ai-agent-stack:end -->
