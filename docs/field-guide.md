# Field Guide

A one-page reference: how a task moves through the stack, the three profiles,
and how the learning system's data flows. For every command and flag, use the
generated [`commands.md`](commands.md). For the *why* behind
each design decision, see [`architecture.md`](architecture.md); for the full
prose walkthrough of each feature, see the main [`README.md`](../README.md).

## How a task moves through the stack

Every gate's PASS is bound to a fingerprint of the repo, index, base, plan,
rules and validator config; any change invalidates it. `ai ready` never
launches a model — it only recomputes that fingerprint against what was
actually recorded.

```mermaid
flowchart LR
    A["ai plan / ai run<br/>profile · base · --figma · --ticket-file"] --> B["Orchestration prompt<br/>rules + skills + lessons + contract"]
    B --> C["Implementation<br/>(Claude, outside this diagram)"]
    C --> D["ai gate NAME -- COMMAND<br/>one per required gate"]
    D -->|PASS| E{More required<br/>gates?}
    D -->|FAIL| D
    E -->|yes| D
    E -->|no| F["ai ready"]
    F -->|fresh + passed| G["PR_READY"]
    F -->|missing/stale| H["NEEDS_HUMAN"]
    F -->|any failed| I["FAILED"]
    J["ai pipeline"] -.->|runs every required gate,<br/>then ai ready, one call| D
```

## Three profiles, hard caps either way

Strict means stronger evidence and review, not unlimited agent debate. Every
number below is enforced, not advisory.

| Profile | Skills | Raw files | Review files | Findings | Context7 queries | Review rounds | Usage budget |
|---|---:|---:|---:|---:|---:|---:|---:|
| `fast` | 1 | 4 | 5 | 3 | 1 | 0 | 40,000 tok |
| `standard` | 2 | 8 | 10 | 3 | 3 | 1 | 120,000 tok |
| `strict` | 3 | 12 | 15 | 5 | 5 | 1 | 250,000 tok |

## Commands

The complete [command reference](commands.md) is generated from `argparse` and
checked in CI, so it cannot drift silently when commands or flags change.

## Learning system: what feeds what

Every phase reads the append-only audit log through a derived SQLite index and
writes its own freely-recomputable view. Nothing here ever feeds back into gating — only a
gate's own fresh, fingerprinted evidence does that. Confidence and lessons
may only push toward more rigor: a repository with a perfect historical pass
rate still runs every required gate for a brand-new task.

```mermaid
flowchart LR
    M["metrics.jsonl audit source<br/>+ SQLite query index"] --> P["ai failures<br/>(gate, finding) seen on ≥2 tasks"]
    P --> L["ai lessons<br/>candidate → human confirm → injected"]
    L -->|promote| RULES["rules.json<br/>always-injected, normative"]
    M --> C["ai confidence<br/>pass-rate forecast, n-gated"]
    M --> PR["ai prompt<br/>A/B on validator instructions"]
    PR -->|promote + history| PO["prompt-overrides.json<br/>prompt-history.jsonl"]
    C -.->|advisory only| PLAN["ai plan / ai pipeline<br/>never gates"]
    L -.->|advisory prompt context| PLAN
```

## Zero-footprint: nothing lands in your checkout

Rules, contracts, gate logs, metrics and every learned artifact live outside
the repository, keyed to its normalized `origin` URL.

```text
~/.local/share/ai-agent-stack/           # engine
~/.config/ai-agent-stack/repos/<id>/     # everything this tool ever writes, per repository
  rules.json · validators.json · lessons.json · patterns.json
  prompt-overrides.json · prompt-history.jsonl · metrics.jsonl · metrics.sqlite3 · benchmarks/
  tasks/<task-key>/  contracts/ · state/ · gates/ · review/ · handoffs/
~/work/repository/                       # your actual code — untouched
```

---

A searchable, visual version of this page (with a live command filter) is
also published as a Claude Artifact.
