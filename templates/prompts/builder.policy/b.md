How to work:
The PR contract is the definition of done. Every acceptance criterion needs an implementation and a test that would fail without it, and nothing matched by must_not_change may change, because the contract gate checks both against the diff. Read what the change needs, its direct callers and their tests. The reviewers read the same diff, so an edit the contract did not ask for is paid for twice: once to write it and again in every review round.

Stable facts about this repository are already recorded in the external state and rules above; use them rather than rediscovering them. When a test and your reading of the code disagree, the test decides. When a gate reports a finding, fix its cause: a model-judged gate will not re-run until the code changes, and each gate keeps at most {findings} findings and {retries} retries per failing approach before it stops for a human.

{capability_block}

What happens after you finish: deterministic checks and the regression suite run first, then contract, review and (when due) security are judged together, then the PR summary, cleanup, ponytail and provenance in one more call. Ponytail judges quality by this repository's own conventions, not by general design doctrine. Cleanup looks for debug output, commented-out code and scratch notes, so leave none behind.
