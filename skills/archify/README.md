# archify

Category: **diagramming**
Estimated context cost: **medium**

Stages: ensure local checkout → choose diagram type → author candidate → validate → deliver

Reference-only integration of [`tt-a1i/archify`](https://github.com/tt-a1i/archify): a self-contained Node.js CLI for architecture/workflow/sequence/dataflow/lifecycle diagrams, not a text prompt. Unlike this stack's other curated skills, archify's ~8MB package (bin, renderers, schemas) is **not vendored** — this skill's `prompt.md` only tells the agent how to locate or clone the pinned upstream checkout, run it, and relay its own update-check notices. Requires Node >=18 and network access to clone on first use; returns NEEDS_HUMAN otherwise.
