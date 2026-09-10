# Roadmap

Release history and shipped feature details live in [`CHANGELOG.md`](CHANGELOG.md). This file contains only work that is still planned or deliberately deferred.

## v1.0 — production workflow

- [x] Short path for the normal case (`ai start` / `ai work` / `ai finish`).
- [x] Explicit task lifecycle (`start`, `switch`, `close`, `current`, `tasks`) that prevents contract reuse across unrelated work.
- [ ] Remove the deprecated branch-derived task identity fallback.
- [x] Fail-closed base resolution.
- [ ] Richer risk signals for new, renamed and binary files.
- [x] Per-task pipeline locking and explicit post-gate budget-overrun status.
- [x] Driver interfaces for model providers (builder/reviewer, `ai providers`).
- [x] Driver interfaces for external tools (Context7, Graphify, CodeGraph, RTK) — `ai_stack/capabilities.py`, `ai capabilities`.
- [x] Capability/plugin interfaces. (Per-repository skills already cascade over the bundled ones; providers already had driver interfaces; the capability registry above closes the remaining tool-side gap.)
- [ ] Multi-repository workspace orchestration.
- [x] Instrumentation for a real-usage validation campaign (`ai metrics label`, `ai metrics --campaign`).
- [ ] Actually run the validation campaign (20–30 real tasks, human-labeled) using the tools above and act on its recommendations.

## Measurement follow-ups

- [ ] Validate the SQLite metrics index against larger real-project histories and add repair diagnostics to `ai doctor`.
- [ ] Add an explicitly opt-in live benchmark mode; synthetic benchmark runs remain the default.
- [ ] Decide retention policy from real usage before introducing automatic pruning.

## Deferred pending usage evidence

- Cross-repository lesson sharing.
- Automatic lesson or prompt promotion.
- Live ticket-provider integrations and credential management.
- Memory-provider abstractions beyond repository-local external state.

These remain deferred until real-repository usage shows that their operational value exceeds their complexity and security cost.
