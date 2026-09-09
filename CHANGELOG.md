# Changelog

## Unreleased

- **Removed the dead `bin/ai-*` scripts and unused `templates/`.** Two generations of this design were coexisting, and the old one actively contradicted the stack's central invariant: `templates/CLAUDE.block.md` instructed the builder to run `./bin/ai-contract init` and `./bin/ai-diff-budget snapshot`, both of which wrote `.ai-review/` into the working repository -- exactly what `core.contamination()` detects and what makes `ai ready` fail with `FAILED: zero-footprint check failed`. `bin/ai-contract` also read `$ROOT/.ai-review/contracts/pr-contract.yml`, a file nothing in the stack ever writes there. `bin/ai-project-profile`, `bin/ai-diff-budget` and `bin/ai-provenance-scan` each duplicated logic that already lives in `ai_stack/` (`core.profile_repo()`, `core.collect_scope()`, the `provenance` gate). Of the 31 tracked files under `templates/`, only `profile/`, `benchmarks/`, `policy.md` and `orchestration.yml` are read by any Python module; removed everything else -- `templates/agents/*`, `templates/contracts/*`, `templates/lib/`, `CLAUDE.block.md`, `AGENTS.block.md`, `model-routing.md`, and 10 unused YAML config templates. `bin/ai` (the real entry point) is untouched.

- **`cli.main()` now maps unexpected git/OS failures to `NEEDS_HUMAN` instead of a bare traceback.** `args.func(args)` ran unguarded, but `core.run()` raises `RuntimeError` for any failing git invocation (a mid-rebase repo, a stale `index.lock`, permissions) and IO code raises `OSError`. Left unhandled, that surfaced as a Python traceback and exit 1 -- indistinguishable from a legitimate `FAILED` gate to anything scripting against this CLI, when the stack's whole contract is to emit `PR_READY`/`FAILED`/`NEEDS_HUMAN` with an interpretable exit code. Both exceptions now print `NEEDS_HUMAN: <message>` to stderr and exit 3 (distinct from 1 for `FAILED`/a failed gate and 2 for an argparse usage error). `SystemExit` -- the stack's own protocol, raised throughout via `raise SystemExit(...)` -- passes through untouched; a bare `except Exception` was deliberately avoided so a `KeyError` from a stale plan schema stays a visible traceback instead of being relabeled as an environment problem it isn't.

- **Review a commit or a PR in isolation: `ai review --commit <sha>` / `--pr <n>`.** Every command that reads a diff used to require the target already checked out — reviewing a specific commit or someone else's PR meant checking it out first, disturbing your own working tree. Both flags build the review in a disposable, detached `git worktree` (new `core.temp_worktree()`, always removed on exit, even on error) so the caller's checkout and branch are never touched; verified by asserting `HEAD`, `git status` and `git worktree list` are unchanged before and after. `--commit <sha>` diffs the commit against its parent, falling back to git's well-known empty-tree object for a root commit (which `collect_scope()`'s ref-verification guard now special-cases, since an empty tree is a tree object, not a commit-ish, and would otherwise be rejected even though `git diff` accepts it fine). `--pr <n>` needs `gh` (already one of the optional tools `ai doctor` checks for): resolves the PR's base branch via `gh pr view`, fetches its head via GitHub's `refs/pull/<n>/head` (works for forks too, without adding a remote), and verifies the fetched base the same way every other base is verified. An adhoc review's artifact goes to its own `state/adhoc-reviews/<commit-or-pr>/` directory, never into the active task's own `current-review.md` — reviewing an unrelated target must not clobber it.
- **Real-usage validation campaign instrumentation: `ai metrics label`, `ai metrics --campaign`.** This is instrumentation for running the stack against real tasks and measuring the results — not the campaign itself; actually running 20–30 real tasks with real Claude/Codex calls and human labeling each `FAIL` within 24 hours is a usage study a human directs, and nothing here fabricates that data. `ai metrics label <gate> --task-key K [--attempt N] --true-positive|--false-positive` records a human's judgment on one recorded gate attempt (`require_human()`-guarded, refuses to label a `PASS` since the verdict schema already forbids a `PASS` from carrying findings) via a new append-only `gate_label` event; `record_metric()`'s append now shares a common `metrics.append_event()` primitive with it. `ai metrics --campaign [--since SPEC] [--json]` reads `task_start`/`plan`/`gate`/`pipeline`/`task_close`/`gate_label` events and reports, stratified by `task_type` and `profile`: time to first `PR_READY`, retries per gate, tokens, each labeled gate's false-positive rate, and `findings_raised` (distinct findings a task's own failed attempts raised before passing) — shipped with an explicit caveat that this is a volume signal, never a claim about what a human ignored, since the stack cannot tell an override from a genuine fix without a label. Emits plain-language recommendations from thresholds fixed before any campaign runs: a gate's false-positive rate over 20% (minimum 5 labeled attempts) suggests making it advisory; a task type's p90 time-to-ready over 3x its median names the dominant gate by duration; a profile's median tokens over 80% of its budget suggests recalibrating `context_caps`.
- **Provider abstraction for the builder and reviewer: `ai providers show|set|doctor`.** Claude (implementation) and Codex (review) were hard-coded in three places: `lifecycle.cmd_planrun`'s launch, `gates.cmd_validate`'s Codex invocation, and `repo.cmd_profile --deep`'s. New `ai_stack/providers.py` factors both roles behind two narrow interfaces — a *builder* (`available()`, `launch(prompt, root, env)`) and a *reviewer* (`available()`, `verdict(root, review_dir, name, prompt, schema, checker, timeout)`) — so a different agent can fill either without changing `gates.py`, `lifecycle.build_prompt`, or any command's output in the default configuration. `ClaudeBuilder`/`CodexReviewer` wrap the exact pre-existing `os.execvpe`/`run_codex_json` calls (the latter moved to `providers.py` verbatim; `validators.py` re-exports it so its existing test coverage needed no changes); a new `CommandReviewer` runs any configured command that reads the prompt on stdin and returns one JSON line — the same protocol the `json` validator adapter already defines. The reviewer contract is not negotiable: read-only, schema-conforming, usage-reporting from the provider, never estimated. `CommandReviewer.read_only` is `False` because the stack cannot prove an arbitrary command's sandboxing, and `ai providers doctor` says so plainly rather than treating a custom reviewer as equivalent to Codex's sandbox. Selection is `AI_BUILDER`/`AI_REVIEWER` env override > `repo.json`'s `providers` object > the `claude`/`codex` defaults. Verified end to end: a real gate pipeline reaches `PR_READY` through a `CommandReviewer` with the `codex` binary removed from `PATH`, proving no hidden dependency on the default reviewer leaked into `gates.py`.
- **Per-task pipeline locking and an explicit `BUDGET_EXCEEDED` status.** `ai pipeline` and `ai gate` now take a per-task lock (`pipeline.lock`, created `O_CREAT|O_EXCL`), so two runs on the same task can no longer interleave and overwrite each other's evidence. The lock is re-entrant for the process that holds it, since `ai pipeline` calls `ai gate` in-process. A lock whose owning pid is gone *on this host* is reclaimed with a notice; a lock from another host is never assumed dead — a shared external state directory would otherwise silently double-run — and needs `--force-unlock`. `ai status` lists running pipelines and `ai current` marks the active task as RUNNING.
- Crossing the usage budget is now its own outcome rather than a `FAILED` pipeline: the gates that ran may all have passed and the rest simply never ran. `ai pipeline` also checks the budget *after* each gate, so the message names the gate that actually crossed it instead of the innocent one that would have come next, lists what was not run, and points at `--allow-overrun`. The outcome is recorded in the pipeline metric (`status`, `overrun_gate`, `overrun_tokens`) and in a new per-task `state/pipeline-run.json` that `ai current` reports. `readiness.json` keeps its meaning as the certification record and is untouched. `pipeline_budget_exceeded` now counts the explicit status, still counting pre-existing `FAILED` rows from before it existed, and never counting a run that finished deliberately with `--allow-overrun`.
- **A short path for the normal case: `ai work` and `ai finish`.** `ai start <id> --ticket-file t.md` / `ai work` / `ai finish` / `ai close` now cover an ordinary task end to end. Both new commands are compositions, not new machinery: `ai work` builds the same namespace `ai run`/`ai plan` receive and hands off to `cmd_planrun` (taking the objective from the task's title and the base from the task), and `ai finish` preflights the required validators and then calls `cmd_pipeline`, which already ends in `ai ready`. The preflight turns "configure validators for checks, regression" into the exact commands that fix it. A task's own base now outranks the repository default everywhere, so `ai start --base release` keeps one task on a different branch without a flag on every command.
- **Fixed: the proposed pytest command failed its own gate.** A gate whose command writes an untracked file changes the evidence fingerprint mid-run, so a bare `pytest` never passed `regression` in a repository that does not gitignore its artifacts. The proposal is now `python3 -B -m pytest -q -p no:cacheprovider` — `-B` for the `__pycache__/*.pyc` files that actually caused it, `-p no:cacheprovider` for `.pytest_cache/`.
- **Autoconfigured check gates: `ai validators propose [--apply]`.** New `ai_stack/detect.py` reads the repository's real tooling — Ruff, mypy, pytest; a lockfile-appropriate `pnpm`/`yarn`/`bun`/`npm` for lint, typecheck and test scripts (plus `tsc --noEmit` via the right runner); Clippy and `cargo test`; `golangci-lint` or `go vet`, and `go test` — and proposes `checks` and `regression` validators for it. Several tools for one gate run fail-fast in sequence under an explicit `sh -c`, composed only from the fixed rule table and never from repository content. A tool that is detected but not installed is reported and never proposed, and a stricter tool only supersedes the one it subsumes when it is actually installed, so a repo is never left with no check at all. `ai init` now shows the proposal but writes nothing: `validators.json` holds commands `ai pipeline` later executes, so applying one stays a separate, explicit act. `--apply` never overwrites an already-configured gate.
- **Explicit task lifecycle: `ai start`, `ai switch`, `ai close`, `ai current`, `ai tasks`.** Task identity used to fall back to the current branch, so two unrelated tickets worked on one branch shared `contracts/current-pr.yml` — and since `ensure_contract()` only rewrites `objective:`, the second ticket silently inherited the first one's acceptance criteria, `must_not_change` constraints and risk notes. `ai start <id>` now creates a task with its own fresh contract and makes it active; starting over an active task, restarting an existing one, or reopening a closed one each require an explicit flag (`--switch` / `--resume`) instead of happening by accident. Identity precedence is `--task-id` > `AI_TASK_ID` (set by `ai gate`, so a running gate stays pinned to the task that launched it) > the active task > the branch; the branch fallback still works but warns once per process and is deprecated. The active pointer is keyed by absolute checkout path, so worktrees sharing a repository never share a task. `ai gate` and `ai pipeline` refuse to record new evidence against a closed task. `task.json` is merged rather than rewritten, so lifecycle fields survive the `task_state()` call every command makes.
- **Breaking: fail-closed base resolution.** `collect_scope()` used to verify `--base` with `git rev-parse --verify` and silently fall back to `HEAD` when it failed. In any repository whose default branch was not literally `main` (the old `--base` default), that produced an empty scope — which drove `classify()` to LOW risk, dropped the `review` gate out of `required_gates()`, and let `ai ready` certify `PR_READY` over a diff no gate had ever seen. New `core.resolve_base()` refuses an unverifiable base and names the refs the repository actually has (`origin/HEAD`, `origin/<name>`, main/master/develop/trunk), suggesting the closest match — most often `origin/<name>` for a branch that only exists on the remote. `--base` no longer defaults to `main`: it defaults to the base recorded by `ai init` (which now autodetects and stores `default_base` in `repo.json`, and accepts `ai init --base <ref>`), and every command that takes a base resolves it at a single point in `cli.main()`. `ai status` and `ai doctor` report the active base. `repo.json` is now merged rather than rewritten on each `repo_state()` call, so durable per-repo settings survive.
- Add `ai deploy plan/show/set/remove/run`: a Codex-generated, read-only dev runbook that only ever *suggests* a command, plus a declared, dry-run-by-default execution path reusing the `validators.json` command+evidence+timeout pattern.
- A deploy is deliberately not a gate: it stays out of `core.GATES`, `workflow.ORDER` and `ai pipeline`, only `--execute` runs anything, `require_human()` blocks it from inside a gate/validator, and all its state lives outside the target repository. `ai deploy` with no subcommand keeps its existing detection-only output.
- Make mypy blocking, split the fast PR suite from the full subprocess matrix, and test installation and execution from a built wheel.
- Add a rebuildable SQLite index for metrics while retaining `metrics.jsonl` as the append-only audit source.
- Generate the command reference from argparse and reduce version/history duplication across README and roadmap.

## 0.9.4

- Split `ai_stack/cli.py` (2003 lines) into 11 domain modules — `core.py` (git/JSON/state primitives, risk policy), `metrics.py`, `skills.py`, `prompts.py`, `learning.py`, `crg.py`, `tools.py` (Context7/Graphify/Figma), `repo.py`, `benchmark.py`, `lifecycle.py`, `gates.py` — leaving `cli.py` as a ~130-line entry point (`parser()` + `main()`). Pure mechanical refactor: no CLI output, exit code, file format, or evidence-fingerprint computation changed; all 45 tests pass unchanged. Bare same-directory imports throughout (`from core import ...`), matching the existing `workflow`/`validators` convention, since `ai_stack/` is a flat script directory, not a package — `install.py`, `bin/ai` and `tests/validate.py` needed zero changes.
- Fixed one real bug the split surfaced: `ai benchmark run`'s sandboxed scenarios resolved the CLI to re-exec via `Path(__file__)`, which after the split pointed at `benchmark.py` instead of `cli.py`. Now uses `STACK_ROOT/'ai_stack/cli.py'`, the same idiom `ai validators install` already uses for the bundled validator commands.

## 0.9.3

- Add `ai ticket check` / `ai plan --ticket-file`: paste a ticket's own text (from Jira, GitHub Issues, Linear, anywhere) and analyze it locally with regex, no network and no model call. Detected acceptance criteria fill an empty contract using the exact same emptiness check the `contract` gate already uses (never overwrites a human-authored list); a detected Figma link is adopted like an inline task URL already is; mentioned blockers/dependencies are reported as advisory only, permanently, since there's no live source to verify one is still actually open. Scoped down from a larger live-fetch/Jira-API design after discussion — see ROADMAP.md.

## 0.9.2

- Add `ai benchmark run/list/report/compare`: a 5-scenario corpus (`templates/benchmarks/pipeline/*.json`) run end-to-end through the real `ai plan` -> `ai pipeline` -> `ai ready` flow, each in a fully isolated sandbox (fresh temp git repo, `HOME`/`XDG_CONFIG_HOME` redirected, `AI_GATE`/`AI_TASK_DIR` stripped) so a benchmark run can never touch the real `metrics.jsonl` or contaminate `ai confidence`/`ai prompt`'s sample counts. Every gate's verdict is scripted synthetically — no model calls, zero cost. `compare` refuses across differing `corpus_digest`s. The pre-existing `ai benchmark` (routing/skill-selection comparison) is unchanged as the bare command.
- This closes v0.9 "Measurement"'s scope for now; a `--live` mode against real bundled validators is deferred to when it's actually wanted.

## 0.9.1

- Add prompt promotion history and rollback: `ai prompt history [--slot NAME]` and `ai prompt rollback NAME --confirm`. Every `promote`/`reset`/`rollback` appends to a repo-scoped, append-only `prompt-history.jsonl`, binding a promotion to the exact evidence (`variant_stats()` output) that justified it, so the decision survives even after `ai metrics prune`. `reset` and `rollback` are now `require_human`-guarded, same as `promote`.
- `variant_stats()` gains a `by_stack_version` confounder bucket, surfaced in `ai prompt report` and in every promotion's recorded evidence.
- This closes the "prompt versioning" residual from v0.8 Phase 4; v0.9's remaining item is the benchmark suite.

## 0.9.0

- Add a dimensional usage report to `ai metrics`: `--by day|week|gate|profile|task_type|task` with `--since`/`--top`, `--format csv` (STDOUT-only, matching `ai failures export`), and an advisory (never blocking) `--budget` line. New pure `parse_window()`/`bucket_ts()`/`usage_report()` in `ai_stack/workflow.py` follow `summarize()`'s existing honesty rule: unreported usage stays `null`, never zero-filled.
- Add `ai metrics prune --older-than SPEC --confirm`, the first destructive operation on `metrics.jsonl`; `require_human`-guarded since it deletes the substrate `ai failures`/`ai confidence`/`ai prompt report` read.
- Roadmap: close "Prompt A/B evaluation" as satisfied by v0.8's `ai prompt`; re-scope the remaining "versioning" gap (promotion history, rollback) as its own v0.9 item.

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
