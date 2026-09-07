# AI Agent Stack

## v0.7 Skills + token efficiency

Skills are strategies, not additional agents. The router reads only compact registry metadata and lazy-loads at most 1/2/3 skill prompts in fast/standard/strict.

```bash
ai skill list
ai skill list --task "login regression"
ai skill explain diagnosing-bugs
ai skill dry-run tdd
ai handoff "continue auth fix" --next "run focused regression test"
ai optimize
```

Token policy: classify first, progressive disclosure, one tool per question, diff-first review, cache before fetch, evidence before model debate, compact PASS outputs, and hard per-profile budgets. All mutable state remains under `~/.config/ai-agent-stack/repos/<repo-id>/`.


A **zero-footprint**, token-aware orchestration layer for Claude Code + Codex over existing repositories.

It combines:

- Claude routing: Haiku / Sonnet / Opus / Fable by role and risk
- Codex adversarial review tiers
- RTK for compressed terminal output
- CodeGraph for symbol-level code intelligence
- Graphify for architecture routes, communities and cross-file relationships
- Code Review Graph (CRG) for diff impact, blast radius, affected flows, test gaps and minimal review context
- Context7 for current, version-specific external library documentation
- Figma MCP + Design Contracts for design-driven tickets
- Cleanup + Ponytail final PR gates

## Core principle

**Nothing from the framework is committed to the repository you are working on.**

```text
~/.local/share/ai-agent-stack/          # engine
~/.config/ai-agent-stack/repos/<id>/    # rules, contracts, graph, docs cache, metrics
~/work/company-repo/                    # no framework files
```

## Install

```bash
git clone https://github.com/marianozamora/ai-agent-stack.git
cd ai-agent-stack
./install.sh
```

Ensure `~/.local/bin` is on `PATH`, then from any git repository:

```bash
ai init
ai doctor
```

Optional tools:

```bash
npm install -g ctx7                         # Context7
uv tool install graphifyy                   # Graphify
uv tool install code-review-graph             # Code Review Graph
npm i -g @colbymchenry/codegraph            # CodeGraph
brew install rtk                            # RTK (macOS)
```

## Daily usage

```bash
ai plan "implement ticket #1450"
ai impact --base main                      # deterministic structural impact
ai run  "implement ticket #1450"
ai review --base main                      # CRG context -> Codex read-only
ai run  "implement ticket #1450" --profile strict
ai run  "ticket with design" --figma "https://figma.com/design/..."
ai ready
```

The external repo state can be inspected with:

```bash
ai path
ai status
```

## Token budgets

The Context Governor enforces bounded defaults instead of unlimited context:

| Profile | Skills | Raw files | Review files | Findings | Context7 queries | Review rounds |
|---|---:|---:|---:|---:|---:|---:|
| fast | 1 | 4 | 5 | 3 | 1 | 0 |
| standard | 2 | 8 | 10 | 3 | 3 | 1 |
| strict | 3 | 12 | 15 | 5 | 5 | 1 |

Strict means stronger evidence/review, not unlimited agent debate.

## Per-repository rules

Rules are stored outside the checkout and automatically injected into orchestration context.

```bash
ai rules
ai rules add "Controllers stay thin; business logic belongs in services."
ai rules add --scope "src/frontend/**" "Reuse existing design-system components."
ai rules remove 2
```

Rule precedence:

```text
explicit repo rule
  > repository tooling/config
  > established architecture
  > nearby module convention
  > generic SOLID / FP advice
```

## Graphify — macro architecture

Graphify maps cross-file relationships and paths. The wrapper stores its output externally using `GRAPHIFY_OUT`.

```bash
ai graph doctor
ai graph build
ai graph query "show the auth flow"
ai graph path AuthService CommunityService
ai graph explain PermissionService
```

Use Graphify for *where in the architecture?* and CodeGraph for *which exact symbols/callers?*.


## Code Review Graph — review intelligence

CRG is used for **review-time structural evidence**, not as another general-purpose agent.
The wrapper forces its database and generated artifacts into the external per-repo state using `CRG_DATA_DIR`. It does **not** run `code-review-graph install` inside company repositories.

```bash
ai crg doctor
ai crg build
ai crg update --base main
ai crg detect --base main
```

High-level commands:

```bash
ai impact --base main          # no LLM; blast radius/risk/test-gap summary
ai impact --base main --refresh
ai review --base main          # compact CRG impact -> Codex `exec -s read-only`
ai review --base main --build  # build CRG first if missing
ai review --no-launch          # only prepare the bounded review prompt
```

The risk engine allows CRG evidence to **elevate** a heuristic risk level, never lower it automatically. This keeps structural evidence conservative. Review scope should start with CRG's minimal/brief impact and only expand to Graphify, CodeGraph or raw source when needed.

Tool routing:

```text
Architecture / subsystem route  -> Graphify
Exact symbol navigation         -> CodeGraph
Diff / blast radius / test gaps -> Code Review Graph
External library documentation  -> Context7
Shell/tests/log output           -> RTK
```

## Context7 — current external docs

Context7 is **CLI-first** to keep documentation retrieval deterministic and bounded.

```bash
ai docs doctor
ai docs detect                         # dependency versions detected in repo profile
ai docs library nextjs "middleware"  # resolve and cache Context7 ID
ai docs query nextjs "How does middleware work in this installed version?"
```

Or query an exact ID directly:

```bash
ai docs query /vercel/next.js "App Router middleware behavior"
```

Resolved library IDs are cached per repo. Docs query results are cached by query hash, so repeated reviews do not repeatedly spend Context7 calls/context. Use `--refresh` when you explicitly want new docs.

Context7 is only used when a task depends on **external API/framework knowledge**. It should not be called to understand project-specific behavior.

## Figma

```bash
ai run "implement checkout screen" --figma "<frame-url>"
```

The orchestrator uses Figma as an input to a compact Design Contract. Raw design context should not remain in the prompt after extraction. Ponytail additionally checks material design fidelity.

## Profiles

| Profile | Typical use | Raw files | Reviews | Context7 queries |
|---|---|---:|---:|---:|
| `fast` | trivial/local | 4 | 0 | 1 |
| `standard` | normal feature | 8 | 1 | 3 |
| `strict` | high-risk | 12 | 1 | 5 |

## Final PR pipeline

```text
Implementation
   ↓
checks
   ↓
Regression
   ↓
Codex adversarial/security (conditional)
   ↓
Cleanup
   ↓
checks
   ↓
provenance gate
   ↓
Ponytail quality gate
   ↓
design fidelity (when applicable)
   ↓
PR summary
   ↓
PR_READY / NEEDS_HUMAN / FAILED
```

Cleanup removes unnecessary comments/debug residue and accidental Claude/Codex/AI provenance from newly generated source/docs/PR text. Existing commit history is **never silently rewritten**.

## Zero-footprint check

```bash
ai doctor
ai status
```

Known framework artifacts tracked inside the work repository cause the zero-footprint check to fail.

## Why Graphify + CodeGraph + CRG + Context7?

```text
Graphify  = map of the city's roads
CodeGraph = GPS down to the exact function
CRG       = impact scanner for the current diff and execution flows
Context7  = current manual for the external vehicle/API
RTK       = compressed telemetry
Claude/Codex = decisions and implementation/review
```

The goal is to feed models the **smallest authoritative context** that can answer the current question.

## License

This project is licensed under the [MIT License](LICENSE).
Third-party tools mentioned in this repository are distributed separately under their respective licenses.

## Verified workflow (0.8.1)

Select a task identity when working on multiple tickets in the same checkout:

```bash
export AI_TASK_ID=ticket-1450
ai plan "implement ticket #1450" --profile standard
ai path
# Implement the task, then run the actual project checks:
ai gate checks -- npm test
ai gate regression -- npm run test:regression
ai ready
```

Use the check commands provided by your project. `ai gate` executes the command
without an implicit shell, records its exit code and output outside the checkout,
and binds evidence to the repository contents, index, HEAD, base revision, plan,
contracts and repository rules. Put options before the gate name, for example
`ai gate --timeout 120 checks -- npm test`.

Required gates are `checks`, `regression`, `contract`, `cleanup`, `provenance`,
`ponytail` and `summary`. `review` is also required for standard/strict or elevated
risk, `security` for security-sensitive changes, and `design` for Figma tasks.
Run your project-specific validator or review adapter through each named gate.
For gates other than checks/regression, its final stdout line must be a JSON object
such as `{"status":"PASS","evidence":["Acceptance criteria verified against test results"]}`.
A failed review must emit `FAIL` or exit nonzero. A successful model process alone
is insufficient. The framework checks recorded evidence and freshness; the chosen
validators remain responsible for the accuracy of their conclusions.

`ai ready` never launches a model. It returns `PR_READY` only when every required
gate has fresh successful evidence; missing/stale evidence returns `NEEDS_HUMAN`
and a failed gate returns `FAILED`, both with a nonzero exit status. Perform cleanup
before recording final gates. Any change during a gate invalidates its result;
rerun against the final state. Re-run affected checks after fixes.

Artifacts live under `repos/<repo-id>/tasks/<task-key>/`. The key includes the
checkout path and task ID. The default ID is the branch (HEAD for detached
checkouts); `--task-id` overrides `AI_TASK_ID`. Rules, skills preferences and tool
caches remain shared per repository. Existing 0.7.0 artifacts are left intact;
run `ai plan` to initialize the new task state. `ai path` now prints that task's
artifact directory. Use different IDs for concurrent tasks in the same checkout.

Generated orchestration/review prompts and serialized handoffs are rejected when
they exceed the selected character budget. Shorten the input or explicitly select
a larger profile; no acceptance criteria are silently discarded. Limits on tools,
retries and agent calls inside an independently launched model remain instructions
for that model, not runtime counters enforced by this CLI.

Installation validates a separate release before switching the active symlink.
Failed activation restores the previous installation; previous releases are kept
under `~/.local/share/ai-agent-stack-releases/`. Only the runtime files and license
are copied. Reinstalling from the installed directory is supported.

Run the same checks as CI locally:

```bash
python3 tests/validate.py
python3 -m unittest discover -s tests -v
bash tests/smoke.sh
```

## Reusable validators and sequential pipeline

Configure each validator once per repository. Commands are argument arrays,
executed from the checkout without an implicit shell. Configuration is stored
externally in `repos/<repo-id>/validators.json`, shared across tasks.

```bash
# Options precede the gate name. Use the real commands from your project.
ai validators set --adapter exit-code --evidence "Unit tests passed" checks -- npm test
ai validators set --adapter exit-code --evidence "Regression suite passed" regression -- npm run test:regression
ai validators install
ai validators show

ai pipeline --dry-run
ai pipeline
ai pipeline --resume
ai metrics
ai metrics --json
ai metrics --all-tasks --json
```

The test commands above are project-specific examples. `ai validators install`
adds bundled contract, cleanup, review, security, ponytail, design, summary and
provenance validators. It preserves custom commands, including checks/regression,
and refreshes previously installed bundled commands after an upgrade.
The reusable adapters are `json` (default) and `exit-code`. The JSON adapter
requires a final stdout line containing `status: "PASS"` and a nonempty `evidence`
array, even for checks/regression. The exit-code adapter translates a successful
command into that verdict using the explicitly configured evidence description.
Use it for tools whose exit status certifies the described check; it does not
perform a semantic review on its own. A nonzero exit always fails either adapter.
Remove a configuration with `ai validators remove NAME`.

Validators receive `AI_TASK_ID`, `AI_TASK_DIR`, `AI_REPO_STATE`, `AI_GATE` and
`AI_BASE`. They can read task contracts, plans and prior gate logs from those
external directories. Bundled semantic validators launch Codex; custom commands
control their own execution.

Pipeline preflight requires every applicable validator before running anything.
Order is cleanup → checks → regression → contract → review → security →
ponytail → design → summary → provenance, omitting conditional gates when not
required. Cleanup here is a read-only check: apply cleanup fixes before running
the pipeline. Any validator that changes repository/task inputs fails and stops
the sequence. Timeouts terminate the process group on Linux/macOS. There are no
automatic retries. Final certification still goes through `ai ready`.

`--resume` reuses successful evidence only when its fingerprint and log hash are
current. Changes to validator configuration invalidate prior evidence, as do code,
contracts and rules changes. Summary and provenance records also bind the generated
summary hash, so editing or deleting that artifact invalidates their evidence.
Run one pipeline per task at a time.

Bundled validators require an installed, authenticated Codex CLI supporting
`exec --output-schema`, `--output-last-message` and `--json`. They use the
configured default model with a read-only sandbox and no approval escalation.
The protocol follows [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).
Each required semantic gate makes one reviewer invocation; provider usage/billing
applies. Token counts are captured from completion events; cost is not estimated.

Before running, fill the external `contracts/current-pr.yml` printed by `ai path`
with concrete acceptance criteria and constraints. For design tasks, populate the
design contract and provide verifiable design evidence. A reviewer must return
`NEEDS_HUMAN` for missing evidence. An empty acceptance list is rejected before
review. Cleanup is a read-only residue review, contract maps requirements to
evidence, review checks correctness, security inspects trust boundaries, ponytail
checks local conventions and design verifies material requirements.

Summary and provenance require all preceding applicable gates to have fresh
successful evidence. The summary wrapper writes `state/pr-summary.md` externally;
provenance then reviews that draft, changed deliverables and commit messages,
distinguishing legitimate integration names from accidental attribution. Nothing
is published or rewritten. Reviewer output is independently checked: process
success alone, malformed JSON, missing output, PASS with blockers, and empty
evidence cannot pass. These are model reviews, not deterministic proofs.
Diagnostics are retained in the task's `review/*-events.jsonl` files.

Run a bundled validator individually through the gate recorder, for example
`ai gate contract -- ai validate contract`. The `validate` entry point requires
the gate environment so execution remains covered by its timeout and fingerprint.

Metrics are scoped to the current checkout and task by default. They report gate
attempts, passes, failures, elapsed gate time, repeated attempts and pipeline
successes. Repeated attempts include intentional reruns, not just failed retries.
`--all-tasks` aggregates repository events; older events without task IDs remain
visible only in that aggregate. Optional usage comes from the validator's final
JSON line, for example:

```json
{"status":"PASS","evidence":["Acceptance criteria verified"],"usage":{"input_tokens":1200,"output_tokens":180,"cost_usd":0.004}}
```

Every recorded gate event also carries `risk`, `task_type`, `stack_version`, a
1-based `attempt` number and a bounded list of normalized, hashed `findings`
(never raw model text — see `normalize_finding`/`finding_signature` in
`ai_stack/workflow.py`). Nothing consumes these yet; they are the recorded
substrate for the upcoming v0.8 failure-pattern, lessons and confidence
features, added with no new model calls and no change to gate outcomes.

Each profile also carries a runtime token budget (`fast` 40000, `standard` 120000,
`strict` 250000 reported input+output tokens per pipeline run). `ai pipeline`
accumulates reported usage across executed and reused gates and stops before
starting the next gate once that budget is met, returning `NEEDS_HUMAN` instead
of continuing to spend on further reviewer calls. Gates without reported usage
(most exit-code adapters) do not count against the budget. `ai metrics` reports
`pipeline_budget_exceeded` and the aggregated `pipeline_usage` totals alongside
per-gate usage; unreported usage remains `null`, never estimated.

```bash
ai benchmark
ai benchmark --json
```

```bash
ai failures
ai failures --gate cleanup --min 3 --json
ai failures show pat_9f2c1a4b7d30
ai failures rebuild
ai failures export
```

`ai failures` reports failure patterns: a `(gate, finding hash)` pair observed
across at least 2 distinct tasks. This is a verifiable fact about the recorded
log (`metrics.jsonl`), not a diagnosis — the displayed `example` text is the
model's original finding and is never asserted to be true. Every `ai failures`
command recomputes patterns from `metrics.jsonl` on the spot (like `ai metrics`)
and writes the result to `patterns.json` as a persisted snapshot; `rebuild` runs
the same computation and prints a summary. This file is excluded from the
evidence fingerprint, so recomputing it never invalidates an in-flight task's
gate evidence. `export` prints anonymized
`{gate, hash, occurrences}` counts to STDOUT only — no example text, no scope
hint, no repository identifier, and no `--out` flag, so a model running inside
a gate cannot write it into the checkout.

`ai benchmark` runs a fixed set of realistic task fixtures (bug fix, schema
migration, UI copy change, integration work, a Figma-driven design task and a
ticket breakdown) through `fast`/`standard`/`strict` and prints the resulting
risk classification, task type, selected skills, context budget and usage
budget per profile. It needs no active task or model call, so it stays useful
as a fast regression check on classification and skill-selection behavior
across profiles as the stack evolves.

Usage totals include only reported nonnegative values and show how many attempts
reported each field. Missing usage is shown as unreported, never estimated or
treated as free. These metrics cover gate commands, not the independently
launched implementation model. Internal model tool/retry budgets remain prompt
instructions.
