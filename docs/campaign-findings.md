# Validation Campaign Findings

Running log for the campaign in [`campaign-protocol.md`](campaign-protocol.md). One
section per task; numbers are measured, not estimated.

## Task 1 — promize, "notify participants when a promise is signed" (2026-09-26)

Profile `fast`, feature, MEDIUM risk. Diff ≈ 6 backend files, ~470 lines with tests.

**Value.** Every model-judged finding was real; 0 false positives (5 labeled).

| Gate | Finding | Label |
|---|---|---|
| contract (deterministic) | frontend files changed outside `must_not_change` | true positive, 0 tokens |
| contract | signer-name fallback exposed a UUID fragment; missing tests for criteria 1/4/5/6/7 | true positive |
| review | notification reads outside `try` → HTTP 500 after a committed signature and card hold | true positive |
| review | signer-controlled `display_name` inserted unescaped into other participants' emails | true positive |
| provenance | `Co-Authored-By` trailers on all three task commits | true positive |

Gates converged: re-reviews repeated the same findings instead of raising new ones.

**Cost.**

| Round | Gates run | Billable tokens |
|---|---|---|
| 1 | contract | ~50k |
| 2 | contract (no code change) | ~48k — avoidable |
| 3 | contract + review | ~117k |
| 4 | contract + review | ~175k (over the 160k `fast` budget) |
| 5 | summary/cleanup/ponytail/provenance (one call) | ~55k |

Builder (from Claude Code transcripts): 145 Sonnet requests (25.1M cache-read, 73k
output) for the implementation, then 33 **Opus** requests (2.4M cache-read, 20k output)
for the fixes — the fix rounds did not run on the `fast` builder.

**Friction** (more than half of ~3h wall-clock):

- Stale installed engine; missing venv/`node_modules`; ruff and typecheck already red on the base.
- Recorded base `main` 24k lines behind the real branch.
- `claude` off PATH (lazy-loaded nvm).
- Spanish ticket headings detected 0 criteria; `must_not_change`/risks copied by hand.
- Retry cap counted prerequisite-blocked attempts and failures on older code (fixed in #61).
- Gate log paths pasted into the builder by hand every round.
- Two nested `.git` directories in the repository (`backend/`, `frontend/`).

**Where the reviewer's tokens went** (review, round 3: 324k input, 83% cached): 9 model
calls each re-sending the conversation; ~15k-token prompt × 9 ≈ 135k, command output
carried forward ≈ 186k. Largest outputs: `codegraph explore` (48k and 22k characters)
and `git diff`/`status` re-run three times although the diff was already inlined.

**Changes made from these findings:** #61 and the "tighter builder ↔ gate loop",
"ASSESS bundle", "`ai start` preflight" and "campaign report" entries in the
[CHANGELOG](../CHANGELOG.md). From task 2 on, results are not directly comparable with
task 1: the engine changed mid-campaign (logged here, per protocol §5).

**Open:** the `fast` budget (160k) is left unchanged until tasks run on the new engine
show whether the reviewer changes bring a task like this one under it.

## Task 2 — promize, "the Stripe webhook requires a signature" (2026-09-26)

Profile `standard`, fix, MEDIUM risk. Diff: 2 files, +119/−9 (`api/main.py` and
integration tests). First task on the engine with #62/#63.

**Outcome.** `PR_READY` in a single model round; certified `correct` after human
review. No model-judged gate failed, so nothing to label.

**Cost.**

| Gate | Billable tokens |
|---|---|
| contract (shared ASSESS call) | 37.5k |
| review (its own call — bug, see below) | 46.7k |
| summary/cleanup/ponytail/provenance (one call) | 25.4k |
| **Total** | **~110k** (would be ~63k with #64) |

Builder (`builder_usage`, recorded by `ai close`): 27 Opus requests, 11k output,
1.9M cache-read — `standard` uses the default model.

**What worked:** the ticket filled all 7 criteria, 5 `must_not_change` and 2 risk
notes with Spanish headings; the baseline `checks` ran green before the builder;
the builder committed without attribution trailers, so `provenance` passed first time.

**Found and fixed:**

- `ai work` overwrote the ticket's objective with the task title — #63.
- Once `contract` passed, `review` left the shared ASSESS call and was judged again
  on its own (~47k) — #64.
- `security` was never required: detection matched paths only, and the change lived
  in `api/main.py`. Now also from diff content and the contract's risk notes — #65.
- A `regression` run was discarded because `ai finish` started while the builder was
  still writing (correct behaviour; operator note, not a stack bug).

Not comparable one-to-one with task 1: smaller change, different profile, new engine.

