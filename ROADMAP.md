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
- [x] Phase 2: repository lessons (`ai lessons`) — candidate lessons derived from patterns, human-only confirm/promote, scope-matched injection capped hard by profile (fast=0/standard=3/strict=5), promotion graduates into `rules.json`
- [x] `ai profile --deep` (opt-in, not in the original 4-item scope) — Codex-assisted architecture/stack/database/deployment/related-repos/docs understanding, cached by commit, never automatic
- [x] Phase 3: confidence engine (`ai confidence`) — a forecast card of historical pass rates with sample size `n` per stratum (LOW_EVIDENCE below n=5), no blended score, elevate-only (never relaxes `required_gates`/`ai ready`); also surfaced in `ai plan` and as a non-blocking `ai pipeline` preflight note
- [x] Phase 4: prompt optimizer (`ai prompt`) — measured A/B experiments on bundled validator instructions only, deterministic assignment bound to the evidence fingerprint, human-only promotion with a minimum sample size and no formula (report all metrics, human decides)

All four learning features are pure stdlib computation over existing `metrics.jsonl`/gate
records — no new model calls anywhere in this release, and no gate (`required_gates`,
`cmd_ready`, `classify`) is ever influenced by learned data; confidence/lessons may only
push toward more rigor, never less. See the recorded v0.8 design for storage formats,
CLI surface, sequencing (patterns → lessons → confidence → prompt optimizer) and ten
open questions (cross-repo sharing, auto-promotion, retention, naming, min sample size)
still needing a decision before each phase ships.

## v0.9 — Measurement
- [ ] Cost/token dashboard: richer `ai metrics` (time-series, top-N, CSV export, advisory budget) plus `ai metrics prune` retention
- [x] Benchmark suite: end-to-end sandboxed pipeline scenarios (`ai benchmark run/list/report/compare`), synthetic only for now, fully isolated from the real `metrics.jsonl` (deferred: opt-in `--live` mode against real bundled validators)
- [x] Prompt A/B evaluation (shipped as `ai prompt` in v0.8 Phase 4)
- [x] Prompt versioning: promotion history and rollback (`ai prompt history`/`rollback`)

See the recorded v0.9 design for the full data model, CLI surface, sandboxing
approach for the benchmark suite, and 12 open questions (live-mode cost,
dollar estimates, retention policy, benchmark/metrics isolation, corpus
ownership, naming, bucketing timezone, budget persistence) — this summary
follows the design's own recommendations for all of them.

## v1.0+ — Architecture refactor
- [ ] Driver interfaces
- [ ] Capability/plugin interfaces
- [ ] Memory providers
- [ ] Multi-repo workspace orchestration

The v1 architecture refactor is intentionally deferred until real-repository usage data shows which integrations and skills are worth keeping.
