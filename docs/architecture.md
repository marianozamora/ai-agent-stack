# Architecture — v0.6

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
