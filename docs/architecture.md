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


## Verified task lifecycle (0.8.2)

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
refuses `confirm`, `reject` and `promote` whenever `AI_GATE` or `AI_TASK_DIR`
is set in the environment — the same guard `cmd_validate()` already uses to
stop a model from certifying its own gate. `promote` appends the lesson's text
to `rules.json` with `source: "lesson"` and retires the lesson, so a durable
observation graduates into the one always-injected normative store instead of
lessons and rules becoming two competing prompt-injection paths.
