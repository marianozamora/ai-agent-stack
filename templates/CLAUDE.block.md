<!-- ai-agent-stack:start -->
## AI agent stack policy

Follow `.ai-review/policy.md` and `.ai-review/model-routing.md` for non-trivial work.

### Context discipline
- Prefer CodeGraph for architecture, flows, callers/callees and blast radius.
- Prefer RTK-backed shell commands for git, tests, lint, typecheck and logs.
- Read raw source only when exact code is required.
- Do not repeat broad grep/read exploration after CodeGraph already answered the structural question.

### Model discipline
- Haiku: cheap triage/mechanical work.
- Sonnet: default implementation model.
- Opus: deep planning/debugging/high-risk reasoning, not routine code generation.
- Fable: exceptional long-horizon/multi-repo work.
- Escalate only from concrete risk, impact, ambiguity or failed attempts.

### Cross-model review
- LOW: skip Codex by default.
- MEDIUM/HIGH: at most one adversarial Codex review by default.
- Verify each Codex finding independently.
- Fix only confirmed issues.
- Tests and requirements are the source of truth.
- A second Codex pass is allowed only for a confirmed HIGH/CRITICAL issue or a high-risk correction.
<!-- ai-agent-stack:end -->
