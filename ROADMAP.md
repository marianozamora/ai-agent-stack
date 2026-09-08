# Roadmap

Release history and shipped feature details live in [`CHANGELOG.md`](CHANGELOG.md). This file contains only work that is still planned or deliberately deferred.

## v1.0 — production workflow

- [ ] Explicit task lifecycle (`start`, `switch`, `close`) that prevents contract reuse across unrelated work.
- [ ] Fail-closed base resolution and richer risk signals for new, renamed and binary files.
- [ ] Per-task pipeline locking and explicit post-gate budget-overrun status.
- [ ] Driver interfaces for model providers and external tools.
- [ ] Capability/plugin interfaces.
- [ ] Multi-repository workspace orchestration.

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
