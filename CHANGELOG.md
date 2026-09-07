# Changelog

## Unreleased

- Add reusable JSON/exit-code validators stored outside work repositories.
- Add sequential `ai pipeline` with preflight, fail-fast execution and fresh-evidence resume.
- Record task-scoped gate duration, outcomes, repeated attempts and reported token/cost usage.
- Invalidate evidence when validator configuration changes; terminate validator process groups on timeout.

## 0.7.1

- Require fresh recorded gate evidence before certifying PR_READY.
- Isolate task artifacts by task ID and checkout; preserve shared repository preferences.
- Reject oversized orchestration/review context and handoffs.
- Stage and verify installations with rollback on activation failure.
- Add Linux/macOS CI and regression tests for gates, budgets, isolation and installation recovery.

## 0.7.0
- Added lazy Skills Engine with 8 strategies.
- Added hard Token Efficiency Policy and per-profile context/skill/finding budgets.
- Added deterministic task classification and skill routing.
- Added `ai skill list|explain|enable|disable|dry-run`.
- Added compact external `ai handoff`.
- Added `ai optimize` prompt/context audit.
- Added semantic repository fingerprints, task cache keys, and plan metrics.
- Reduced strict review rounds to one by default; rigor escalates evidence/model strength, not unlimited debate.
- Preserved zero-footprint work repositories.

## 0.6.0

- Add Code Review Graph as the Review Intelligence Engine.
- Add zero-footprint CRG storage via `CRG_DATA_DIR`.
- Add `ai crg doctor/build/update/status/detect`.
- Add deterministic `ai impact`.
- Add `ai review`, which feeds bounded CRG impact into Codex in read-only sandbox mode.
- Allow CRG structural evidence to elevate, but never automatically lower, heuristic risk.
- Add CRG-aware context routing to the orchestrator and doctor/status output.

## 0.5.0

- Reworked installation into a global zero-footprint overlay.
- Per-repository state now lives under `~/.config/ai-agent-stack/repos/<repo-id>/`.
- Added external repository profiles and scoped user rules.
- Added Graphify integration with external `GRAPHIFY_OUT` storage.
- Added Context7 CLI-first docs intelligence with per-repo library-ID and query caching.
- Added docs budgets to fast/standard/strict profiles.
- Added zero-footprint contamination checks.
- Preserved Figma, Cleanup, Ponytail, provenance, risk routing and adversarial-review policies in the orchestration prompt.

## 0.4.0

- Unified orchestration entrypoint and Figma workflow.
- Added provenance cleanup and final gates.

## 0.3.0

- Add PR Contract as compact task source of truth.
- Add Context Governor with fast/standard/strict budgets.
- Add evidence/confidence policy and `NEEDS_HUMAN` circuit-breaker rules.
- Add project-profile cache generator for Ponytail and routing.
- Add cheap Regression Agent and conditional Security Gate.
- Add behavior-neutral Cleanup diff-budget snapshot/check.
- Strengthen Ponytail as project-specific read-only quality gate.
- Add PR Summarizer that consumes only final artifacts.
- Add deterministic check-plan discovery.
- Add optional local telemetry and metrics.
- Fix review scope so uncommitted working-tree changes are included.
