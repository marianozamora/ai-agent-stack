# Changelog

## 0.8.5

- Add `ai prompt`: measured experiments on bundled validator instructions only (never the orchestration prompt). Deterministic, non-random variant assignment (`hash(task_cache_key + slot)`) snapshotted once at plan time and bound to the evidence fingerprint; at most one experiment per repo. `ai prompt report` groups outcomes by `(variant, sha)` so a mid-experiment text edit is flagged, not silently pooled. `ai prompt promote` refuses below the minimum sample size, refuses without `--confirm`, always shows the full comparison first, and — like `ai lessons confirm/promote` — refuses to run inside a gate/validator environment so a model can never promote its own prompt.
- This completes v0.8 "Learning": metrics enrichment, failure patterns, repository lessons, deep repository profiling, confidence reporting, and now prompt experiments — all pure stdlib computation or explicit opt-in model calls, none of it able to relax gating.

## 0.8.4

- Add `ai confidence`: a forecast card of historical gate pass rates for the current change's stratum (profile/risk/task type), always showing sample size `n` and marking gates below `n=5` as `LOW_EVIDENCE` instead of computing a rate on too little data. No blended score, no confidence intervals. Purely advisory — `classify()`, `required_gates()` and `ai ready` never read it, and confidence can only ever suggest more rigor, never less. Also surfaces as three summary lines in `ai plan`/`ai run` and a non-blocking preflight note in `ai pipeline` when projected spend exceeds the profile's usage budget.

## 0.8.3

- Add `ai profile --deep`: an explicit, opt-in, Codex-assisted read of the repository (architecture pattern, actual stack, database/schema, deployment/CI-CD, links to other repositories, and real summaries of key docs), distinct from the free static `ai profile` (languages/tooling by file presence). Cached by analyzed commit; never runs automatically, so no task pays for it unless asked. Output is explicitly labeled as an unverified model interpretation (`caveat` + `confidence_caveats`), referenced by path (not inlined) from the orchestration prompt.
- Generalize `ai_stack/validators.py`'s Codex invocation into `run_codex_json()`, shared by the gate validator protocol and `ai profile --deep`; add an explicit `timeout` for callers with no enclosing `ai gate` process group.

## 0.8.2

- Add `ai lessons`: derive empirical `candidate` lessons from failure patterns, injected into prompts only after human `confirm` (scope-matched, capped hard per profile: fast=0/standard=3/strict=5, sub-budgeted before the orchestration prompt's own budget check). `confirm`/`reject`/`promote` refuse to run inside a gate/validator environment, so a model can never curate its own future context. `promote` graduates a durable lesson into `rules.json` and retires it. `lessons.json` replaces the unused `observations.json`.

## 0.8.1

- Add `ai failures`: detect failure patterns (a `gate`+finding-hash pair seen across at least 2 distinct tasks) from recorded metrics, with `show`, `rebuild` and an anonymized STDOUT-only `export`. Pure stdlib computation over `metrics.jsonl`; no model call, no gating effect, and the cached `patterns.json` is excluded from the evidence fingerprint so recomputing it never invalidates in-flight task evidence.

## 0.8.0

- Enrich every recorded gate event with `stack_version`, `risk`, `task_type` and a 1-based `attempt` number, and every metric event with `stack_version`/`risk`/`task_type` from the current plan.
- Capture semantic gate findings as normalized, hashed records (`finding_signature`/`normalize_finding` in `ai_stack/workflow.py`), bounded by the profile's findings cap, so repeated findings can be recognized across attempts and tasks without storing raw model text.
- This is groundwork for v0.8 "Learning" (failure patterns, repository lessons, confidence reporting, prompt experiments); no new commands or gating behavior yet, and no new model calls.

## 0.7.3

- Add `ai benchmark`, comparing risk classification, task type and skill selection across profiles for a fixed set of realistic task fixtures, with no active task or model call required.
- Add a per-profile runtime token budget (`usage_tokens`); `ai pipeline` accumulates reported gate usage and stops before exceeding it instead of continuing to spend on further reviewer calls.
- Report end-to-end pipeline usage totals and budget-exceeded counts in `ai metrics`.

## 0.7.2

- Bundle read-only semantic validators for contract, cleanup, review, security, quality, design, summary and provenance; install them while preserving custom commands.
- Validate structured reviewer responses and capture reported review tokens.
- Generate the external PR summary before provenance review and bind both gates to its artifact hash.

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
