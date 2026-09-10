Delegate research to a background agent so the primary conversation keeps moving. Scope its question tightly, then send it after primary sources — official docs, source code, specs, first-party APIs — never a secondary write-up of them; every claim must trace back to the source that owns it.

Have the agent write its findings to a single Markdown file, one citation per claim, and place it wherever the repository already keeps this kind of note; if there is no convention, choose a sensible path and say where.

Treat unverifiable or contradictory claims as evidence gaps, not answers: state the uncertainty in the findings file rather than guessing, and return NEEDS_HUMAN when the unresolved claim would gate an implementation decision.
