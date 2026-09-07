# Roadmap

## Next release — Workflow automation and measurement

- [x] Bundled validators: contract, cleanup, provenance, quality (ponytail), summary and review; security and design when applicable.
- [x] Reusable JSON and exit-code validator adapters
- [x] Sequential pipeline with preflight and evidence-aware resume
- [x] Task-scoped gate outcomes, durations and reported usage
- [ ] Real-task benchmark fixtures and profile comparisons
- [ ] End-to-end model usage capture and runtime budgets

`ai validators install` completes semantic validator configuration while preserving project-specific `checks` and `regression`. Configuration stays outside the repository. Live semantic review requires authenticated Codex and populated task contracts; tests use a fixture reviewer to verify orchestration without model calls.

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
- [ ] Repository lessons
- [ ] Failure pattern database
- [ ] Confidence engine
- [ ] Prompt optimizer with measured outcomes

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
