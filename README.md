# AI Agent Stack

A zero-footprint, token-aware workflow for using Claude Code and Codex over existing repositories.

The stack prepares bounded implementation context, records fresh validation evidence and certifies whether a change is ready for human review. Framework state stays outside the repository being changed.

Current release: see [`VERSION`](VERSION). Changes between releases are recorded in [`CHANGELOG.md`](CHANGELOG.md).

## Quickstart

```bash
git clone https://github.com/marianozamora/ai-agent-stack.git
cd ai-agent-stack
./install.sh
```

Ensure `~/.local/bin` is on `PATH`. Then, inside a Git repository:

```bash
ai init
ai doctor

# Configure project-specific deterministic checks once.
ai validators set --adapter exit-code --evidence "Tests passed" checks -- npm test
ai validators set --adapter exit-code --evidence "Regression suite passed" regression -- npm run test:regression
ai validators install

# Run a task.
ai plan "implement ticket #1450" --task-id 1450
ai run "implement ticket #1450" --task-id 1450
ai pipeline --resume --task-id 1450
ai ready --task-id 1450
```

Use `ai plan` when another agent will consume the generated prompt, or `ai run` to launch Claude directly. `ai pipeline` executes configured gates; `ai ready` only evaluates recorded, fresh evidence and never launches a model.

See the generated [command reference](docs/commands.md) for every command and flag, and the [field guide](docs/field-guide.md) for the complete lifecycle diagrams.

## Deployment

`ai deploy` is detection-only by default and never mutates anything. Documenting and running a deploy is opt-in and explicit:

```bash
ai deploy                      # detect Docker/CI/PaaS/infra/scripts/docs tooling (unchanged, back-compat)
ai deploy plan                 # ask Codex (read-only) for a dev runbook + a suggested command
                               # review the runbook written under external state, e.g.
                               # ~/.config/ai-agent-stack/repos/<id>/deploy-runbook.md
ai deploy set --evidence "service responds on :8080" dev -- ./deploy.sh dev
ai deploy run dev              # dry run: prints what would execute, executes nothing
ai deploy run dev --execute    # actually deploys; blocked inside a gate/validator environment
```

A deploy is not a gate: it never runs as part of `ai pipeline` and never gates PR readiness. The runbook is Codex's read-only, best-effort *suggestion* — nothing from it is ever auto-written into the deploy configuration; a human reviews it and declares the real command with `ai deploy set`. All runbook and run-record state lives outside the target repository, under external state (`ai path`/`ai status`), the same as every other framework artifact.

## Workflow

```mermaid
flowchart LR
    A["plan / run"] --> B["implementation"]
    B --> C["configured gates"]
    C --> D{"fresh evidence?"}
    D -->|yes| E["PR_READY"]
    D -->|missing| F["NEEDS_HUMAN"]
    D -->|failed| G["FAILED"]
```

Every gate result is bound to a fingerprint of the repository, index, base, plan, contracts, rules and validator configuration. A relevant change invalidates prior evidence. Resume only reuses evidence whose fingerprint and artifact hashes still match.

The default gate order is:

```text
cleanup → checks → regression → contract → review → security
        → ponytail → design → summary → provenance
```

Review, security and design are included according to profile, risk and task inputs.

## Profiles

| Profile | Skills | Raw files | Review files | Findings | Usage budget |
|---|---:|---:|---:|---:|---:|
| `fast` | 1 | 4 | 5 | 3 | 40,000 tokens |
| `standard` | 2 | 8 | 10 | 3 | 120,000 tokens |
| `strict` | 3 | 12 | 15 | 5 | 250,000 tokens |

Strict means stronger evidence and review, not unlimited context or agent debate.

## Curated upstream skills

The bundled skill router includes compact, trigger-gated adaptations of selected
workflows from [`addyosmani/agent-skills`](https://github.com/addyosmani/agent-skills):
source-driven development, deprecation/migration, and observability. Their full
upstream prompts are deliberately not injected; the stack keeps its own context
caps, routing, gates and zero-footprint lifecycle.

The exact upstream commit, paths, local prompt hashes and license are recorded in
`skills/upstreams.json`. Inspect the local pin without network access, or compare
it with the remote branch explicitly:

```bash
ai skill upstream
ai skill upstream --check
ai skill upstream --check --json
```

An available upstream update is advisory. Updating the pin or a curated prompt
remains a reviewed source change; the command never downloads or rewrites skills.

## State and privacy boundary

```text
~/.local/share/ai-agent-stack/           # installed engine
~/.config/ai-agent-stack/repos/<id>/     # contracts, gates, rules, metrics, caches
~/work/company-repo/                     # no framework state committed here
```

`metrics.jsonl` remains the human-readable audit source. A disposable `metrics.sqlite3` index makes task/gate queries incremental and is rebuilt automatically after the JSONL file is rewritten or removed.

Optional integrations include Context7, Graphify, CodeGraph, Code Review Graph, Figma MCP and RTK. Missing optional tools degrade to explicit status rather than silently becoming evidence.

## Guarantees and limits

- Gate evidence is reproducible and invalidated when its inputs change.
- Semantic validators run through a configured reviewer (Codex by default) read-only and require structured verdicts; see `ai providers` to inspect or swap the builder/reviewer.
- Learned lessons require human confirmation and cannot relax required gates.
- Token/context limits are profile-bound; missing usage remains unreported rather than estimated.
- `PR_READY` means the configured evidence is fresh. It is not permission to merge or deploy without the repository's normal human and CI controls.

## Development

Fast checks used for pull requests:

```bash
ruff check .
mypy ai_stack
python3 scripts/generate_command_reference.py --check
python3 -m pytest -q --ignore=tests/test_workflow.py
bash tests/package_smoke.sh
bash tests/smoke.sh
```

The subprocess-heavy matrix runs after merges, weekly and on demand:

```bash
python3 -m pytest -n auto -q --ignore=tests/test_workflow.py
python3 -m pytest -q tests/test_workflow.py
python3 -m unittest discover -s tests
```

Architecture and design rationale live in [docs/architecture.md](docs/architecture.md). Planned work lives in [ROADMAP.md](ROADMAP.md).
