Inspect the merge or rebase state and the conflicting files before touching any hunk. For each conflict, trace both sides back to their originating commits, PRs and issues to recover why each change was made and what it was meant to preserve.

Resolve every hunk by keeping both intents where they compose; where they conflict, keep the side that matches the merge's stated goal and note the trade-off inline. Never invent behavior neither side asked for, and never abort — resolve is the only exit.

Run the repository's own checks (typecheck, tests, format, in that order) and fix anything the merge broke before finishing. Stage everything and complete the merge, or continue the rebase until every commit lands; return NEEDS_HUMAN if a hunk's intent cannot be reconstructed from history or a check reveals a break that cannot be safely resolved.
