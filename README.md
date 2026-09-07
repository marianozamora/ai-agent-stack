# AI Agent Stack

Reusable, token-efficient Claude Code + Codex workflow with:

- **RTK** for compact shell/tool output
- **CodeGraph** for structural repository context and blast-radius analysis
- **Caveman** for concise Claude responses
- **Codex plugin for Claude Code** for independent read-only reviews
- risk-based **model routing** so expensive models are used only when justified

The goal is simple: **use cheap context and cheap models first, then escalate by evidence.**

## Recommended routing

| Work | Claude role | Codex role |
|---|---|---|
| Mechanical / triage | Haiku | Luna when a check is justified |
| Normal implementation | Sonnet | Terra reviewer |
| High-risk design/debugging | Opus for strategy, Sonnet for implementation | Sol reviewer |
| Long-horizon / extreme | Fable | Astra only in exceptional cases |

Exact model IDs change over time. This repo intentionally stores **roles/tier names**, not hard-coded provider model IDs.
See [`templates/model-routing.md`](templates/model-routing.md).

## Prerequisites

- Git
- Bash 3.2 or newer
- Python 3 (used only to safely refresh existing managed instruction blocks)
- `gh` for publishing your own copy
- Node.js/npm and the Claude/Codex CLIs as required by the optional integrations below

## One-time machine setup

Run the helper to see what is installed:

```bash
./bin/ai-stack-doctor
```

Install the components you want. Current upstream setup:

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

### Caveman for Claude Code

```bash
claude plugin marketplace add JuliusBrussee/caveman
claude plugin install caveman@caveman
```

Caveman on Codex is optional. The reviewer policy is already intentionally terse, so installing Caveman there may add instruction overhead without much benefit.

### Codex plugin for Claude Code

Inside Claude Code:

```text
/plugin marketplace add openai/codex-plugin-cc
/plugin install codex@openai-codex
/reload-plugins
/codex:setup
```

If the Codex CLI itself is missing:

```bash
npm install -g @openai/codex
codex login
```

## Install into any repository

```bash
git clone https://github.com/YOUR-USER/ai-agent-stack.git
cd ai-agent-stack
./install.sh /path/to/project
```

The installer preserves existing `CLAUDE.md` and `AGENTS.md` content and adds managed blocks.
It creates:

```text
.ai-review/
  policy.md
  model-routing.md
bin/
  ai-review-plan
  ai-stack-doctor
CLAUDE.md
AGENTS.md
```

If CodeGraph is available and the project has no graph yet, initialize it once:

```bash
cd /path/to/project
codegraph init
```

## Daily workflow

```text
Ticket
  ↓
cheap classification/context
  ↓
Claude builder
  ↓
RTK tests/lint/typecheck
  ↓
risk classification
  ├─ LOW    → finish
  ├─ MEDIUM → one focused Codex review
  └─ HIGH   → strong adversarial Codex review
                 ↓
          Claude verifies findings
                 ↓
          tests are the arbiter
```

From the target repository, run:

```bash
./bin/ai-review-plan main
```

The script gives a risk level, model-role recommendation, and a compact Codex review command.

## Token rules

1. Use **CodeGraph before grep/read loops** for architecture, callers/callees and blast radius.
2. Use **RTK** for shell, git, tests, lint, logs, Docker/Kubernetes/AWS output.
3. Read raw files only when exact implementation details are required.
4. Review the **diff**, not the whole repository.
5. One cross-model review by default.
6. Maximum **5 findings**; prefer 3.
7. No style review unless style creates a real correctness/maintenance risk.
8. Codex reports evidence; Claude decides and implements fixes.
9. Re-review only the correction when a second pass is actually justified.
10. Never use an expensive model merely because the task is large in lines; use risk, dependency impact and failed attempts as escalation signals.

## Updating an installation

Pull the latest template and rerun `./install.sh /path/to/project`. Files under
`.ai-review/`, both helper scripts, and only the marked blocks in `CLAUDE.md` and
`AGENTS.md` are refreshed. Existing content outside those blocks is preserved.

Review the resulting diff before committing; project-specific policy always wins.

## Philosophy

```text
CodeGraph = what code deserves context
RTK       = what output deserves context
Claude    = builder / planner
Codex     = independent challenger
Tests     = arbiter
```

## Upstream projects

- RTK: https://github.com/rtk-ai/rtk
- Caveman: https://github.com/juliusbrussee/caveman
- CodeGraph: https://github.com/colbymchenry/codegraph
- OpenAI Codex plugin for Claude Code: https://github.com/openai/codex-plugin-cc
