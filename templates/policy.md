# AI Review Policy

Goal: maximize correctness and PR quality while minimizing duplicated context, model calls and token spend.

## 1. PR Contract first

For non-trivial work, create or update `$AI_REPO_STATE/current-contract.yml` before implementation.
The contract is the compact source of truth for:
- objective
- acceptance criteria
- things that must not change
- expected affected areas
- test plan
- rollout/rollback constraints

Agents should reference the contract instead of replaying long ticket/chat history.

## 2. Context Governor

Use the cheapest context source that can answer the question:
1. **CodeGraph** for symbols, callers/callees, dependency paths, flows and blast radius.
2. **RTK** for git, tests, lint, typecheck, logs, containers and cloud CLI output.
3. **Raw source reads** only when exact implementation details are required.

Default context caps are in `$AI_REPO_STATE/context-governor.yml`.
Do not expand context without one of the allowed evidence-based reasons.
Do not reread information already present in the current context.

## 3. Evidence before model opinion

Evidence priority:
1. deterministic test
2. static analysis / compiler / lint
3. reproducible failure
4. CodeGraph dependency evidence
5. direct source evidence
6. model reasoning

A model finding should not block a PR solely because it sounds plausible.
Default minimum confidence is 0.80; security/data-loss findings may be investigated at lower confidence because impact is higher.
Tests or concrete reproduction beat model disagreement.

## 4. Risk levels

### LOW
Copy, formatting, comments, isolated styling, test-only edits, trivial mappings and behavior-neutral config.
Action: cheap/local checks. No cross-model review by default.

### MEDIUM
Normal business logic, API behavior, integrations, non-destructive data handling, meaningful localized refactors.
Action: Sonnet builder + focused regression scout + one balanced Codex adversarial review.

### HIGH
Auth/RBAC, payments, migrations/schema, destructive operations, concurrency, queues/retries/idempotency, secrets/crypto, IAM/infra, data integrity, wide blast radius.
Action: Opus planning when useful, Sonnet implementation, regression scout, strong Codex review and conditional security gate.

### CRITICAL / LONG-HORIZON
Multi-repo migrations, major architecture replacement, irreversible/high-blast-radius changes, unresolved production incidents or many connected autonomous steps.
Action: Fable/deep role exceptionally; strongest reviewer only when justified.

## 5. Profiles

Use `$AI_REPO_STATE/profiles.yml`:
- `fast`: low-risk iteration; no adversarial review by default.
- `standard`: daily default; one balanced review when risk requires it.
- `strict`: high-risk/release-critical; stronger checks and larger but still bounded context.

Profile changes budgets, not engineering standards.

## 6. Agent ownership

- **Builder** may change behavior to satisfy the contract.
- **Regression** is read-only and proposes only concrete regression risks/tests.
- **Correctness reviewer** is read-only.
- **Security gate** is read-only.
- **Cleanup** may mutate only behavior-neutral noise.
- **Ponytail** is read-only and is the final quality/style/architecture gate.
- **PR Summarizer** is read-only and sees only final artifacts.

Agents must not take over another agent's responsibility.

## 7. Token and loop budgets

- Diff-first review.
- One adversarial round by default.
- Maximum 5 findings; prefer 3.
- No broad repository crawl.
- No full alternative implementation in first review.
- No repeated ticket explanation.
- Prefer tests/evidence over model debate.
- Max calls/escalations come from the selected profile.
- If the budget is exhausted with an unresolved HIGH/CRITICAL issue, stop with `NEEDS_HUMAN`; do not silently escalate forever.

## 8. Diff budget

Cleanup and quality-fix passes must remain targeted.
Default cleanup budget: at most 100 changed lines and at most 15% of the original behavioral diff, whichever is more restrictive when measurable.
If cleanup exceeds the budget, stop and report rather than creating a refactor disguised as cleanup.

## 9. Conditional security gate

Do not run a heavyweight security review on every PR.
Trigger it only for actual trust boundaries such as auth/RBAC, secrets, SQL/untrusted input, IAM, sensitive data, payments or tenant isolation.

## 10. Test-impact routing

Use CodeGraph blast radius plus nearby test layout to choose the cheapest relevant tests.
Run full suites for HIGH/CRITICAL changes, release-critical paths, or when targeted coverage cannot establish confidence.

## 11. Project profile cache

`$AI_REPO_STATE/project-profile.json` caches cheap facts such as languages, linters, formatters, test frameworks and repository guidance.
Regenerate it when tooling/config changes materially.
Ponytail may use it to avoid rediscovering conventions every PR, but explicit repo guidance and nearby code always win.

## 12. Final PR-readiness pipeline

1. Contract.
2. Context/risk plan.
3. Implement.
4. Relevant deterministic checks.
5. Regression scout.
6. Risk-based adversarial correctness review.
7. Conditional security gate.
8. Fix only confirmed findings and verify.
9. Cleanup.
10. Cleanup diff-budget check.
11. Verify after cleanup.
12. Ponytail.
13. If Ponytail fails, one targeted quality-fix loop only.
14. PR Summarizer after PASS.

A PR is ready only when:
- contract is satisfied
- relevant checks pass
- no unresolved HIGH/CRITICAL correctness issue remains
- security gate passed or was not required
- `CLEANUP: PASS`
- cleanup stayed within budget
- `PONYTAIL: PASS` and `PR_READY: YES`


## Figma-origin tickets
- Auto-detect Figma URLs or enable explicitly with `ai run --figma <url>`.
- Use remote Figma MCP and `$AI_REPO_STATE/figma.yml`.
- Build `$AI_REPO_STATE/current-design-contract.yml` before implementation.
- Prefer metadata + variables + Code Connect before targeted design context.
- Do not retain raw MCP payloads after the Design Contract is populated.
- Ponytail owns material design fidelity; avoid pixel-nitpicking.

## Provenance hygiene
- Cleanup removes accidental Claude/Codex/ChatGPT/AI-generated residue from changed deliverables.
- The `provenance` gate (`ai gate provenance`) checks added code/docs, commit messages in the PR range, and generated PR artifacts.
- Never silently rewrite existing git history. If a commit message fails, return `NEEDS_ATTENTION` with the commit hash.
- Legitimate product references must use a narrow `$AI_REPO_STATE/provenance.allow` regex rather than globally disabling the gate.


## Token efficiency

- Skills are lazy-loaded after deterministic routing; never load the full skills catalog into an agent prompt.
- Fast/standard/strict cap active skills at 1/2/3, raw files at 4/8/12, and findings at 3/3/5.
- Prefer metadata and graph summaries before snippets; prefer snippets before complete files.
- Route one structural question to the most appropriate engine instead of querying every graph tool.
- Reuse Context7 mappings/docs, project profile, Graphify graph, CRG DB and semantic fingerprints before fetching/rebuilding.
- Tests and static evidence arbitrate disagreement. Do not spend turns on agent debates.
- A passing reviewer/gate returns a compact PASS result.
- Retry a failed approach only within profile budget; then escalate or require human input.
