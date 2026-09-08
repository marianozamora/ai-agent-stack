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
