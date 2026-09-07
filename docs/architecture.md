# Architecture

AI Agent Stack is a zero-footprint overlay for existing repositories.

```text
~/.local/share/ai-agent-stack/           global engine
~/.config/ai-agent-stack/repos/<id>/     repo-specific state
~/work/repository/                       unchanged by the framework
```

The repository fingerprint is derived from the normalized `origin` URL (or the local root if no remote exists). Moving a checkout therefore does not normally lose its learned profile/rules.

## Context hierarchy

```text
PR / Design Contract
       ↓
Code Review Graph  — current diff, blast radius, flows, test gaps, minimal review set
       ↓
Graphify           — macro routes, communities, architecture graph
       ↓
CodeGraph          — exact symbol navigation when needed
       ↓
Context7           — external framework/library documentation only
       ↓
RTK                — compressed shell/git/test output
       ↓
Raw source         — only when implementation/evidence needs it
```

The hierarchy is conditional, not a mandate to call every tool. CRG is the default structural source during review; Graphify is for macro architecture; CodeGraph is for precise implementation navigation; Context7 is only for version-sensitive external APIs.

## Code Review Graph

The wrapper sets `CRG_DATA_DIR` to the external per-repo state directory. `ai impact` is deterministic and LLM-free. `ai review` starts from CRG's compact impact and launches Codex with a read-only sandbox. CRG evidence may elevate risk but never automatically lower a conservative heuristic risk.

## Zero-footprint invariant

The framework must not add tracked AI configuration, contracts, metrics, graph outputs, generated prompts, CRG databases, or MCP files to work repositories. Per-repo data belongs under the external config directory. `ai doctor` / `ai status` detect known contamination.

## Agent pipeline

Builder → deterministic checks → Regression → CRG impact → conditional Codex adversarial/security review → Cleanup → checks → provenance gate → Ponytail → optional design fidelity → PR summary.

Cleanup modifies only non-behavioral residue. Ponytail is read-only and judges project conventions before generic SOLID/FP preferences.

## Context7

CLI-first by default. Library IDs are cached in `context7-libraries.json`; query results are cached by library+question hash under `docs-cache/`. MCP can still be configured globally for interactive sessions, but the orchestrator does not require it.

## Graphify

The wrapper sets `GRAPHIFY_OUT` to the external per-repo state directory. This uses Graphify without placing `graphify-out/` in the checkout. `EXTRACTED` edges count as stronger evidence; `INFERRED` edges are discovery hints and cannot independently block a PR.

## v0.7 Skills Engine and token funnel

Skills are lightweight strategies. Routing reads only `skills/registry.json`; prompt bodies are loaded only after selection and are capped by profile (fast=1, standard=2, strict=3). Work-repository state is never used for framework configuration.

```text
Task
  -> deterministic task type
  -> compact skill metadata
  -> select <= profile cap
  -> load selected skill prompts only
  -> PR Contract
  -> CRG/Graphify/CodeGraph/Context7 only by need
  -> snippets
  -> raw source only with evidence
```

The token policy treats tests/static evidence as the arbiter and prevents model-to-model debate loops. Strict mode increases evidence and reviewer strength but still has bounded skills, files, findings, retries, and review rounds.


## Verified task lifecycle (0.9.0)

Repository preferences and intelligence caches are shared. Mutable contracts,
plans, reviews, handoffs and gate records live in `tasks/<task-key>/`, where the
key includes the checkout path and the explicit task ID (branch by default).

`ai gate` executes a validator and records its command, exit code, output hash,
structured verdict for semantic gates, and a fingerprint of the validated tree
and task inputs. Gates that modify those inputs fail and must be rerun.
`ai ready` evaluates required gates against the current fingerprint and emits a
machine-readable readiness artifact plus a terminal status. It never invokes an
LLM or treats a prior model statement as sufficient proof of readiness.

Character budgets are enforced before writing generated prompts or handoffs.
Model-internal tool/retry counts remain orchestration instructions. Installation
uses validated release directories and an active symlink, with rollback when
activation fails. CI covers Python 3.10/3.13 on Linux and macOS.

## Configured pipelines and metrics

`validators.json` is shared per repository and included in evidence fingerprints.
`ai pipeline` preflights required validators using the same risk selection as
`ai ready`, then invokes the existing gate recorder sequentially. Cleanup is
validated first; semantic gates use JSON verdicts or an explicitly configured
exit-code adapter. Failure stops execution. Resume requires matching fingerprints
and intact log hashes. Final readiness is recomputed rather than inferred from
process completion.

`ai_stack/validators.py` implements the bundled semantic review protocol.
`ai validators install` adds missing semantic commands without replacing custom
validators. Reviewers run in Codex's read-only sandbox and inherit the enclosing
gate's process group, so its timeout terminates the reviewer and its child tools.
Structured output is validated independently of process success. Summary creation
is performed by the wrapper outside the checkout, before provenance review;
both gate records include the summary artifact hash. Missing prerequisites stop
bundled validators before model execution. Reviewer completion events supply
token counts, while missing cost data remains unreported.

`ai_stack/workflow.py` holds reusable configuration validation, process execution,
usage normalization and aggregation. The CLI retains repository/task resolution
and orchestration. Gate events include task key, task ID, profile, duration,
outcome and optional reported usage. Metrics do not estimate missing usage or
observe model-internal calls. Existing repository-level events are retained.

Each profile's `context_caps` also carries a `usage_tokens` runtime budget.
`ai pipeline` sums reported input/output tokens from executed and resumed gates
as it runs and stops before the next gate once the budget is met, recording a
`FAILED` pipeline event with the accumulated usage and the budget it hit.
`summarize()` aggregates per-pipeline usage separately from per-gate usage and
counts runs that stopped on a budget, so `ai metrics` shows end-to-end spend
across a whole run, not just per-gate figures.

`ai benchmark` runs a fixed set of realistic task fixtures (`BENCHMARK_TASKS` in
`ai_stack/cli.py`) through `classify`, `context_caps` and `select_skills` for
every profile, without touching git history, an active task or a model call.
It is a deterministic regression check on risk classification and skill
selection across profiles as those functions evolve.

## v0.8 "Learning" — Phase 0 (metrics enrichment)

Every `record_metric()` row now also carries `stack_version`, `risk` and
`task_type` read from the current plan, so later analysis can stratify outcomes
by those dimensions without re-deriving them. `cmd_gate()` additionally records
a 1-based `attempt` number (`gate_attempt_number()` counts prior `gate` events
for the same `task_key`+gate name) and a bounded `findings` list.

Findings are never stored as raw model text. `ai_stack/workflow.py` adds two
pure, stdlib-only helpers: `normalize_finding()` folds a finding string to a
comparable form (hex blobs, `path:line` locations and bare digit runs replaced
with placeholders, whitespace collapsed, truncated to 160 chars), and
`finding_signature()` is its sha256 prefix. `cmd_gate()` stores at most
`plan['caps']['findings']` `{"hash":..., "text":...}` records per gate, so the
same underlying issue reported with different line numbers or hex ids hashes
identically. This is a passive recording layer only: it adds no new model
calls, does not change what makes a gate pass, and is the substrate the
failure-pattern, lessons and confidence phases will read without needing a
second pass over raw verdict text.

## v0.8 "Learning" — Phase 1 (failure pattern database)

`detect_patterns()` in `ai_stack/workflow.py` is pure and stdlib-only: it groups
recorded gate findings by `(gate, hash)`, keeps only pairs seen across at least
2 distinct `task_key`s (a repeat within a single task is not a pattern), and for
each surviving pair computes `occurrences`, `distinct_tasks`, `first_seen`/
`last_seen`, breakdowns by `profile`/`risk`/`task_type`, a `scope_hint` (a
directory prefix shared by >=2 occurrences' path-looking tokens, via
`_shared_scope_hint()`), and `resolved_next_attempt` (how often the same
gate passed on the very next recorded attempt for that task, computed purely
from event ordering). The displayed `example` is one finding's own normalized
text — never a model-generated summary of the pattern, so there is nothing
synthesized to hallucinate.

`ai_stack/cli.py`'s `cmd_failures()` calls `rebuild_patterns()` on every
invocation — list, `show`, `export` and `rebuild` alike all recompute from
`metrics.jsonl` on the spot, the same no-stale-cache convention `cmd_metrics()`
already follows. The result is also persisted to the repo-scoped
`patterns.json` as a snapshot for external inspection, but no command trusts
that file over a fresh read. `patterns.json` is intentionally **excluded** from
`evidence_fingerprint()` — it is a derived, disposable view over `metrics.jsonl`,
and recomputing it must never invalidate a running task's gate evidence.
`ai failures show <id>` and `ai failures export` read the same fresh result; export
emits only anonymized `{gate, hash, occurrences}` records to STDOUT, with no
example text, scope hint, repo identifier or `--out` flag, keeping cross-run
sharing a manual, explicit, redaction-safe copy/paste rather than a file a
gate-launched model could write into the checkout.

## v0.8 "Learning" — Phase 2 (repository lessons)

`lessons.json` (repo-scoped, replacing the unused `observations.json` from
earlier releases) holds empirical, derived entries — distinct from `rules.json`,
which stays the normative, human-authored store. `derive_lessons()` in
`ai_stack/cli.py` calls `rebuild_patterns()` and creates one `candidate` lesson
per pattern (`by_pattern` keyed on `pattern_id`), copying the pattern's own
normalized example text verbatim. Re-running `derive` only refreshes an
existing `candidate`'s counts; a `confirmed`, `rejected` or `retired` entry is
never mutated, so rejection is sticky and a confirmed lesson's text is stable
even as its pattern keeps accumulating new occurrences.

`select_lessons()` filters to `status == "confirmed"`, keeps only lessons whose
`scope` glob (`fnmatch`) matches a file in the current `collect_scope()` result
(or has no scope), ranks by `(observations desc, last_seen desc, id)` for a
total deterministic order, and takes the profile's `LESSON_TOP_K`
(`fast`: 0, `standard`: 3, `strict`: 5). `render_lessons()` then hard-truncates
the assembled block to `caps['context_chars'] // 10` before `build_prompt()`'s
own `enforce_budget()` check runs, so an accumulating lesson store can never be
the reason `ai plan` starts failing. `build_prompt()` writes exactly what it
injected to `task_state(state)/'state/lessons.json'` (with a digest), which is
what `cmd_validate()`'s prompt points a validator at — framed as "advisory
prior observations, never evidence for a PASS" — and what `evidence_fingerprint()`
now includes. Because that file is written only at plan time, confirming or
deriving a lesson mid-task does not invalidate in-flight evidence; re-planning
does. `lessons.json` and `patterns.json` stay out of the fingerprint for the
same reason `ai failures` recomputes freely: their own derivation must never
invalidate a running task.

Curation is human-only by construction, not just convention: `cmd_lessons()`
refuses `add`, `confirm`, `reject`, `retire` and `promote` whenever `AI_GATE`
or `AI_TASK_DIR` is set in the environment — the same guard `cmd_validate()`
already uses to stop a model from certifying its own gate. Every command that
creates a confirmed lesson or changes an existing one's status is covered, not
just the ones with the most obviously adversarial names — `add` creates a
`confirmed` entry immediately, and `retire` can make an inconvenient
lesson disappear from future prompts, so both are as sensitive as `confirm`.
`promote` appends the lesson's text
to `rules.json` with `source: "lesson"` and retires the lesson, so a durable
observation graduates into the one always-injected normative store instead of
lessons and rules becoming two competing prompt-injection paths.

## Deep repository profile (`ai profile --deep`)

`profile_repo()` (called automatically by `ai plan`/`ai run` on first use) is
free, static file-presence detection — languages by extension, package
managers by lockfile, whether a formatter/linter/typechecker/test config
exists. It never reads content, so it cannot say what the architecture is,
what a docs file actually claims, or whether the repo talks to another one.

`ai_stack/validators.py`'s Codex invocation was generalized from
`model_verdict()` into `run_codex_json(executable, root, review_dir, name,
prompt, schema, checker, timeout=None)` — the same read-only/ephemeral/
schema-validated safety contract, decoupled from the gate-specific PASS/FAIL
`SCHEMA`. `model_verdict()` is now a thin wrapper over it for the gate
protocol. `cmd_profile()` in `ai_stack/cli.py` is the second caller: it has no
enclosing `ai gate` process group, so it is the one caller that must pass an
explicit `timeout` (default 600s via `--timeout`); `run_codex_json` persists
diagnostics to `review/<name>-events.jsonl` even on a timeout, not only on a
completed-but-failed run.

`DEEP_PROFILE_SCHEMA`/`check_deep_profile()` define a distinct contract from
the gate `SCHEMA`: there is no PASS/FAIL, because this is descriptive context
generation, not a review. The model must cite file paths for every claim and
list anything unverified or inferred in `confidence_caveats`; the saved
`project-deep-profile.json` additionally carries a fixed `caveat` string, so
nothing consuming this file can mistake it for verified evidence. It is keyed
by `analyzed_commit` and skips the model call entirely when unchanged and
`--refresh` was not passed — the same freshness-without-restating-the-check
pattern `ai failures`/`ai pipeline --resume` already use. `build_prompt()`
references the file by path (never inlines it), so the injection costs no
context budget until a task actually reads it. This file is deliberately kept
out of `evidence_fingerprint()` — like `project-profile.json` before it, it is
informational context, not gate evidence.

## v0.8 "Learning" — Phase 3 (confidence engine)

`outcome_stats()` in `ai_stack/workflow.py` is pure and stdlib-only
(`statistics.median`, no third-party stats). It groups gate events by gate
name for a given stratum filter (`profile`/`risk`/`task_type`) and returns,
per gate, its own sample size `n`; below `min_n` (default 5) the row is
`{"gate":..., "n":..., "sufficient": False}` with no rate fields at all — never
a rate computed on too few observations. A sufficient row adds `pass_rate`,
`first_attempt_pass_rate`, `median_attempts` (per distinct `task_key`, from
each task's highest recorded `attempt`) and `median_usage_tokens`. There are
deliberately no confidence intervals or significance tests.

`confidence_card()` in `ai_stack/cli.py` is the single place that assembles a
card from `outcome_stats()` plus `rebuild_patterns()` (patterns whose
`scope_hint` glob-matches a file in the current `collect_scope()` result) plus
`required_gates()` plus the profile's `usage_tokens` budget, and is reused by
all three consumers so there is exactly one computation, not three: `ai
confidence` (the full card), `cmd_planrun()` (three summary lines: weakest
required gate, matched pattern count, projected spend vs. budget), and
`cmd_pipeline()`'s preflight (a `print()` note when projected spend exceeds
budget — never a `SystemExit`, so it cannot block a run).

Confidence is structurally incapable of relaxing gating: `classify()`,
`required_gates()` and `cmd_ready()` take no confidence input, and nothing
here writes into `evidence_fingerprint()`'s inputs or a gate record. Where the
card's `weakest_gate` and `projected_usage_tokens` are surfaced, the only
actionable direction is toward a stricter profile — the same elevate-only
asymmetry `elevate_risk()` already applies to Code Review Graph impact.

## v0.8 "Learning" — Phase 4 (prompt optimizer)

Scoped to bundled validator instructions only (`INSTRUCTIONS` in
`ai_stack/validators.py`), never `build_prompt()`'s orchestration prompt —
that prompt's outcome is mediated by a separately launched implementation
model and a human, so a measured pass-rate delta downstream would not be
attributable to the prompt wording. Variant `a` is always `INSTRUCTIONS[name]`
itself, resolved in code with no file dependency; an alternate variant is a
human-authored `templates/prompts/validator.<name>/<variant>.md` (`templates/`
is already in `install.py`'s copy list). `variant_text()`/`available_variants()`
in `ai_stack/cli.py` are the only two places that know this resolution rule.

At most one experiment runs per repo (`prompt-experiments.json`,
`{"active": {"slot", "variants", "started_at", "min_samples_per_variant"}}`).
`assign_prompt_variants(state, cache_key)` is called once, at plan time, from
`build_prompt()`: a promoted override (`prompt-overrides.json`) always wins
for its slot; otherwise the active experiment's slot is assigned by
`int(sha256(cache_key+slot), 16) % len(variants)` — deterministic from
`task_cache_key()` (already computed for skill-selection caching), so the
split is reproducible and a task keeps its variant across `--resume` without
persisting a random draw. The result is snapshotted to
`task_state(state)/'state/prompt-assignment.json'` — one file per task, not
per validator call — and added to `evidence_fingerprint()`'s file list, same
treatment as `state/lessons.json`.

`cmd_validate()` reads that snapshot for its own slot to pick which text to
send Codex. `cmd_gate()` independently reads the same snapshot to attach
`{slot: {variant, sha}}` to its `prompt_variants` metric field — decoupled
from `cmd_validate()` entirely, so metrics recording stays solely `cmd_gate()`'s
job as it already was for every other field. `variant_stats()` in
`ai_stack/workflow.py` groups gate events by `(variant, sha)`, not just
`variant`: if a variant file's body changes mid-experiment (a stack upgrade),
results split into a new group instead of silently pooling two different
prompts, and `ai prompt report` surfaces that as an explicit warning.

`ai prompt promote` mirrors `ai lessons confirm`/`promote`'s human-only guard
(refuses when `AI_GATE` or `AI_TASK_DIR` is set), additionally refuses below
`min_samples_per_variant` for every variant in the experiment, and always
prints the full per-variant comparison — including `by_task_type`/`by_risk`
breakdowns so confounding is visible — before requiring an explicit
`--confirm`. There is no promotion formula (pass rate vs. token cost vs.
first-attempt rate): the command reports all of them and a human decides. A
successful promotion writes `prompt-overrides.json` (repo-scoped, added to
`evidence_fingerprint()` alongside `validators.json` for the same reason:
changing what a validator is told changes what its evidence means) and stops
the experiment if it was promoting that experiment's own slot.

## v0.9 "Measurement" — Phase 1 (cost/token dashboard)

`ai_stack/workflow.py` gains three pure, stdlib-only functions: `parse_window(spec)`
turns `'30d'`/`'12w'`/`'YYYY-MM-DD'` into an epoch cutoff; `bucket_ts(ts, granularity)`
turns a timestamp into a UTC `'YYYY-MM-DD'` or ISO `'YYYY-Www'` label (UTC chosen for
reproducibility over local-time convenience); `usage_report(rows, *, group_by, since,
until, top)` aggregates gate/pipeline rows by a time bucket or a row dimension
(`gate`/`profile`/`task_type`/`task`). It follows `summarize()`'s honesty rule exactly:
unreported usage stays `None`, counted separately via `reported_attempts`/
`unreported_attempts`, never zero-filled — a row missing usage data must never look
cheaper than a row that reported zero.

`cmd_metrics()` in `ai_stack/cli.py` dispatches to `usage_report()` when `--by` is
given, instead of `summarize()`'s flat report; `--json`/`--format json` are kept as
aliases so existing callers (including the test suite) are unaffected. `--format csv`
writes via the stdlib `csv` module to STDOUT only — no `--out` flag, the same
constraint `ai failures export` already established, so a model running inside a gate
cannot write a file into the checkout; malformed-event counts move to STDERR in CSV
mode so STDOUT stays a clean pipeable table. The one rendering flourish — an ASCII `#`
bar scaled to the bucket with the most tokens, for `--by day`/`week` only — is two
lines of `int()` math with no dependency and no attempt to replace the printed number,
which is always present regardless of the bar.

`--budget N` prints a single advisory line (spend in the window vs. `N`) and never
raises `SystemExit` — the same non-blocking shape as `cmd_pipeline()`'s existing
projected-spend note. This is deliberate: nothing in this codebase gates on historical
or learned data, and a repo-level spend figure is exactly the kind of number that would
be tempting to enforce. The budget is not persisted; it is a flag on the report, not a
stored threshold that could later gate `ai plan`/`ai pipeline` unprompted.

`ai metrics prune --older-than SPEC --confirm` is the first destructive command over
`metrics.jsonl` and is `require_human()`-guarded — a deliberate asymmetry with
`ai lessons prune`, which is unguarded because it only ever deletes `candidate`
lessons a model could not have made authoritative on its own. Pruning `metrics.jsonl`
deletes the substrate `detect_patterns()`/`outcome_stats()`/`variant_stats()` read, so
a model running inside a gate must not be able to erase the record of its own
recurring failures. It prints what it would remove before requiring `--confirm`, and
rewrites the file with only the kept rows — there is no separate archive; a human who
wants one should copy `metrics.jsonl` first.
