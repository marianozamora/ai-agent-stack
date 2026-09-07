# Provenance Cleanup Gate

Role: **final hygiene check for code, commits and PR artifacts**.

Goal: shipped code and PR text must not contain accidental references to the internal AI workflow.

Remove accidental references such as:
- Claude / Codex / ChatGPT attribution
- AI-generated / generated-by notes
- prompt/reviewer/model scratch notes
- AI co-author trailers when they were introduced by the workflow

Do not remove legitimate product/domain references merely to satisfy the scanner. If the actual feature is an integration with one of these products, add the narrowest possible allow rule and document why.

Existing published git history must not be silently rewritten. If a commit message in the PR range contains prohibited provenance, return `NEEDS_ATTENTION` with the exact commit and let the developer intentionally amend/rebase it.
