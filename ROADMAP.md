# Roadmap

## v0.7 — Workflow automation and measurement (shipped)

- [x] Bundled validators: contract, cleanup, provenance, quality (ponytail), summary and review; security and design when applicable.
- [x] Reusable JSON and exit-code validator adapters
- [x] Sequential pipeline with preflight and evidence-aware resume
- [x] Task-scoped gate outcomes, durations and reported usage
- [x] Real-task benchmark fixtures and profile comparisons
- [x] End-to-end model usage capture and runtime budgets

`ai validators install` completes semantic validator configuration while preserving project-specific `checks` and `regression`. Configuration stays outside the repository. Live semantic review requires authenticated Codex and populated task contracts; tests use a fixture reviewer to verify orchestration without model calls.

`ai benchmark` compares risk classification, task type and skill selection across
`fast`/`standard`/`strict` for a fixed set of realistic task fixtures, with no
active task or model call required. `ai pipeline` enforces a per-profile runtime
token budget (`usage_tokens` in `context_caps`) against usage reported by
executed and resumed gates, stopping the run before exceeding it; `ai metrics`
reports the aggregated per-pipeline usage and how many runs hit their budget.

## v0.7 — Skills + Token Efficiency
- [x] Lazy Skills Engine
- [x] Diagnosing Bugs
- [x] Handoff
- [x] Prototype
- [x] Wayfinder
- [x] TDD
- [x] Codebase Design
- [x] To Tickets
- [x] Writing for Agents
- [x] Token Efficiency Policy
- [x] Progressive context + hard per-profile budgets
- [x] External task/cache/fingerprint state

## v0.8 — Learning
- [x] Phase 0: metrics enrichment (`risk`, `task_type`, `attempt`, normalized/hashed findings) — the substrate the phases below read
- [x] Phase 1: failure pattern database (`ai failures`) — `(gate, finding hash)` pairs observed across >=2 tasks, cached and rebuildable, excluded from the evidence fingerprint
- [ ] Repository lessons
- [ ] Confidence engine
- [ ] Prompt optimizer with measured outcomes

All four learning features are pure stdlib computation over existing `metrics.jsonl`/gate
records — no new model calls anywhere in this release, and no gate (`required_gates`,
`cmd_ready`, `classify`) is ever influenced by learned data; confidence/lessons may only
push toward more rigor, never less. See the recorded v0.8 design for storage formats,
CLI surface, sequencing (patterns → lessons → confidence → prompt optimizer) and ten
open questions (cross-repo sharing, auto-promotion, retention, naming, min sample size)
still needing a decision before each phase ships.

## v0.9 — Measurement
- [ ] Benchmark suite
- [ ] Cost/token dashboard
- [ ] Prompt versioning and A/B evaluation

## v1.0+ — Architecture refactor
- [ ] Driver interfaces
- [ ] Capability/plugin interfaces
- [ ] Memory providers
- [ ] Multi-repo workspace orchestration

The v1 architecture refactor is intentionally deferred until real-repository usage data shows which integrations and skills are worth keeping.
