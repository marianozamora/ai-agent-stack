# AI Agent Stack

Reusable **token-aware AI development orchestration** for Claude Code + Codex.

It combines:
- **RTK** — compressed shell/git/test/tool output
- **CodeGraph** — structural context, callers/callees and blast radius
- **Caveman** — optional concise Claude behavior
- **Codex** — independent correctness/security challenger
- **PR Contract** — compact task source of truth
- **Context Governor** — hard context/call/escalation budgets
- **Project Profile cache** — avoids rediscovering repo conventions every PR
- **Regression Agent** — cheap behavior-regression scout
- **Cleanup Agent** — behavior-neutral final cleanup
- **Ponytail** — final read-only project-style/architecture/quality gate
- **PR Summarizer** — cheap final PR title/body generator
- **Telemetry** — optional local model/tool-call tracking

The design principle is: **minimum agents, maximum evidence**.

## Pipeline

```text
PR Contract
   ↓
Context Governor + CodeGraph + risk/profile routing
   ↓
Opus plan only when HIGH and useful
   ↓
Sonnet builder
   ↓
RTK deterministic checks
   ↓
Haiku regression scout
   ↓
Codex correctness review when warranted
   ↓
conditional security gate
   ↓
confirmed fixes only
   ↓
diff snapshot
   ↓
Haiku Cleanup
   ↓
diff-budget + checks
   ↓
Sonnet Ponytail
   ↓
Haiku PR Summarizer
   ↓
PR READY / NEEDS_HUMAN
```

## Profiles

| Profile | Intended use | Cross-model review | Context/call budget |
|---|---|---|---|
| `fast` | trivial/low-risk iteration | none by default | smallest |
| `standard` | normal daily development | one balanced review when needed | bounded default |
| `strict` | high-risk/release-critical | stronger review, full relevant suite | larger but bounded |

Example:

```bash
./bin/ai-review-plan main --profile standard
./bin/ai-pr-ready main --profile standard
```

## Agent/model roles

| Role | Preferred model | Responsibility |
|---|---|---|
| Classifier | Haiku | cheap risk/triage |
| Planner | Opus | difficult architecture/debug strategy only |
| Builder | Sonnet | normal implementation |
| Long horizon | Fable | exceptional multi-repo/long autonomous work |
| Regression | Haiku | max 3 concrete regression risks/test gaps |
| Correctness reviewer | Luna/Terra/Sol/Astra by risk | independent adversarial correctness review |
| Security gate | Sol; Astra only if unresolved and critical | only when a trust boundary is touched |
| Cleanup | Haiku | behavior-neutral removal of noise/AI residue |
| Ponytail | Sonnet | final read-only project-specific quality gate |
| PR Summarizer | Haiku | final PR title/body from final artifacts only |
| Arbiter | Opus | unresolved material reasoning conflict |

Exact provider model IDs intentionally remain configurable as model lineups change.

## One-time machine setup

Prerequisites: Git, Bash 3.2+, Python 3, and Node.js/npm for integrations that
install through npm. The Claude Code and Codex CLIs are optional unless you use
their corresponding workflows.

Check the current machine first:

```bash
./bin/ai-stack-doctor
```

### RTK

```bash
brew install rtk
rtk init --global
rtk init --global --codex
```

### CodeGraph

```bash
npm install -g @colbymchenry/codegraph
codegraph install --target=claude,codex --yes
```

### Caveman (optional, mainly Claude)

```bash
claude plugin marketplace add JuliusBrussee/caveman
claude plugin install caveman@caveman
```

### Codex plugin for Claude Code

Inside Claude Code:

```text
/plugin marketplace add openai/codex-plugin-cc
/plugin install codex@openai-codex
/reload-plugins
/codex:setup
```

If Codex CLI is missing:

```bash
npm install -g @openai/codex
codex login
```

## Install into a project

```bash
git clone https://github.com/YOUR-USER/ai-agent-stack.git
cd ai-agent-stack
./install.sh /path/to/project
cd /path/to/project
./bin/ai-project-profile
```

The installer preserves existing `CLAUDE.md` and `AGENTS.md` content using managed blocks.
It keeps transient contract/state/telemetry local through `.git/info/exclude` rather than editing the project's tracked `.gitignore`.

Installed structure:

```text
.ai-review/
  policy.md
  model-routing.md
  orchestration.yml
  profiles.yml
  context-governor.yml
  evidence-policy.yml
  project-profile.json
  contracts/pr-contract.yml
  agents/
    regression.md
    security-gate.md
    cleanup.md
    ponytail.md
    pr-summarizer.md
  lib/common.sh
bin/
  ai-contract
  ai-project-profile
  ai-check-plan
  ai-review-plan
  ai-diff-budget
  ai-pr-ready
  ai-log
  ai-metrics
  ai-stack-doctor
CLAUDE.md
AGENTS.md
```

## Daily usage

### 1. Create the compact PR contract

```bash
./bin/ai-contract init
```

Fill `.ai-review/current-contract.yml`:

```yaml
objective: "Add AUTO_SENT to allowed generationStatus values"
acceptance:
  - "AUTO_SENT is accepted"
  - "existing statuses remain valid"
must_not_change:
  - "event payload shape"
risk: low
test_plan:
  - "schema accepts AUTO_SENT"
  - "existing enum values still pass"
```

The contract prevents every agent from rereading the full ticket/chat.

### 2. Refresh cached project conventions when needed

```bash
./bin/ai-project-profile
```

This records cheap facts such as languages, package manager, linters, formatters, typecheck/test configs and style docs. Ponytail uses it as a cache, never as dogma.

### 3. Get risk/context/model routing

```bash
./bin/ai-review-plan main --profile standard
```

Risk is based primarily on domain/path and should be strengthened by CodeGraph blast radius. Large generated/test diffs do not automatically become HIGH.

### 4. Determine deterministic checks

```bash
./bin/ai-check-plan
```

Use CodeGraph blast radius to narrow LOW/MEDIUM tests. HIGH/strict should prefer the full relevant suite.

### 5. Final cleanup budget

Before Cleanup:

```bash
./bin/ai-diff-budget snapshot
```

After Cleanup:

```bash
./bin/ai-diff-budget check
```

By default a cleanup/quality pass cannot grow the behavioral diff by more than 100 lines or 15%. If it does, stop and inspect manually.

### 6. PR readiness

```bash
./bin/ai-pr-ready main --profile standard
```

A PR is ready only after contract, deterministic checks, applicable correctness/security gates, Cleanup, diff-budget and Ponytail pass.

## Evidence policy

Blocking evidence priority:

```text
test
> static analysis
> reproducible failure
> CodeGraph dependency evidence
> direct source evidence
> model reasoning
```

Default blocker confidence is `>= 0.80`. Style-only Ponytail blockers require `>= 0.90`. Lower-confidence security/data-loss concerns can trigger investigation but should not be stated as proven facts.

## Circuit breakers

No infinite Claude ↔ Codex ↔ Claude loops.

`fast`, `standard` and `strict` define hard budgets for:
- raw context files
- review files
- agent calls
- escalations
- review rounds

If budget is exhausted with an unresolved destructive/security/architecture problem:

```text
NEEDS_HUMAN
```

not “call a more expensive model forever”.

## Optional telemetry

Log a phase locally:

```bash
./bin/ai-log build sonnet pass
./bin/ai-log review terra pass "3 findings, 1 confirmed"
```

If a runner exposes token counts, pass them through environment variables:

```bash
AI_INPUT_TOKENS=12000 AI_OUTPUT_TOKENS=1800 ./bin/ai-log review terra pass
```

Summarize:

```bash
./bin/ai-metrics
```

Telemetry stays local by default.

## Updating an installation

Pull the latest template and rerun `./install.sh /path/to/project`. Managed
configuration and helper scripts are refreshed, the generated project profile
is preserved, and only marked blocks in `CLAUDE.md` and `AGENTS.md` are updated.
Review the target repository diff before committing.

## Separation of responsibilities

```text
Tests/linters = does deterministic evidence pass?
Regression    = what existing behavior might this break?
Codex         = is the solution correct/safe?
Security gate = is a touched trust boundary secure?
Cleanup       = is the diff mechanically clean?
Ponytail      = does it fit THIS project's quality/style/architecture bar?
PR Summarizer = package the final evidence into a clean PR description
```

Ponytail never imposes SOLID on a functional project or FP on an OO project. Project evidence wins over generic preference.

## Upstream projects

- RTK: https://github.com/rtk-ai/rtk
- Caveman: https://github.com/juliusbrussee/caveman
- CodeGraph: https://github.com/colbymchenry/codegraph
- OpenAI Codex plugin for Claude Code: https://github.com/openai/codex-plugin-cc

See `docs/architecture.md` for the complete flow.
