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

## danssme — second repository, first paired control (2026-09-27)

danssme (Next.js + Supabase) joined the campaign with 16 tickets from a read-only
review (issues #90-#105). Setup needed: dependencies, a recorded base, validators
(`regression` = `pnpm test:run`, plus `supabase test db` when pgTAP tests exist), and a
fix to the schema-dump baseline migration so a local database could start at all.

### Task: #90 privilege escalation (standard, builder variant b)

`PR_READY` after 5 rounds. Four true positives, including one the builder's own fix
missed: `security` found that any signed-in user could still set their own
`profiles.stripe_account_id` (payout hijack), and `review` found `ai_credits_reset_at`
self-escalation. About 960k billable gate tokens, inflated by a bug: once `contract`
passed, `review` and `security` computed a different shared-call cache key and paid for
their own calls. Fixed in #68; the next task shows one call per round.

### Task: #102 hardening `/api/upload` — the same ticket with and without the stack

Both arms: Claude Sonnet 5 at `--effort high`, headless `claude -p`, same permissions.

| | No stack (B) | Stack (A) |
|---|---|---|
| Builder | 43 requests, ~$1.22 | 133 requests, ~$2.76 |
| Gates | one measurement pass, 56k billable | 8 rounds, one shared ASSESS call each |
| Tests | 1,261 passing | 1,258 passing |
| Shipped | ownership bypass (substring match) and MIME spoofing | both fixed, plus undeletable PDFs, a 4-segment ownership edge, an unused alias and contract text pasted into the commit message |

With every test green, the no-stack build shipped two security holes, one of them the
exact bug the ticket existed to close. The stack found 6 real issues, 0 false positives,
for about 2.3x the builder cost plus review.

**Weaknesses measured:**

- **Reviewer variance.** `review` and `security` passed code in one round and failed the
  unchanged code in a later round (undeletable PDFs, MIME spoofing). Each finding was real,
  but a single pass is not a reliable certification and the variance adds rounds.
- **Unbounded security deepening.** Each fix exposed a deeper variant (substring, then
  4 segments, then a PDF/HTML polyglot). The last one was out of the ticket's scope and
  was closed by an operator decision (drop PDF uploads), not by another round. The stack
  has no way to express that stopping point.
- **Environment:** a user-level `rtk` command wrapper needed its own permission in both
  arms; a leftover, ignored `route.ts.tmp` was mistaken for builder scratch and deleted,
  then restored (it was empty).

**Stack follow-ups (all closed in #70):** default `ai metrics label` to the latest failed
attempt; `ai validators propose` should prefer a `test:run`-style script over a watch-mode
`test`; `ai doctor` should flag tracked gitlinks without `.gitmodules`; a round cap or
explicit scope boundary for `security` (`model_rounds` per profile).

### Task: #91 lock down `event_registrations` RLS (standard, builder variant a)

`PR_READY` after 4 rounds, inside the new `model_rounds` limit (4 for `standard`), with
one shared ASSESS call per round (~90-150k billable each, ~450k in total; builder 103
Opus requests). The regression gate now rebuilds the local database before pgTAP
(`supabase db reset --local`); without it, `supabase test db` tested the old policies.

What the gates caught: free registration without status or capacity checks, a
participant counter reset to 0 on a failed count, staff roles able to update any column,
`processTransfer` reporting success without a transfer, a capacity race, `notifyOrganizer`
without an authorization check, and cross-event promoter access. The most valuable one
was not in the code under review: **a PR merged in parallel (#109) redefined the same
policies with a later migration timestamp, so merging #91 as written would have
silently undone it in production.** `contract` caught it by comparing the fresh-database
run with `origin/main`; every test on the branch passed.

First false positive of the campaign: a criterion stated the cancellation actions
"use `createAdminClient()`", a factual assumption of the ticket, not a requirement; the
code was correct. Fixing the criterion ended the loop. Ticket wording is part of the
contract's quality.

**Gaps found:**

- The builder only saw `contract`'s findings when `contract` failed first, although the
  same call had judged `review` and `security` (fixed in #71: cached findings are listed).
- Findings that `review`/`security` produced inside a shared call, in a round where the
  pipeline stopped at `contract`, are never recorded as gate attempts, so they cannot be
  labeled. On #91 those were the most important findings. Fixed in #74: they are
  recorded as `cached` gate rows when the pipeline stops before their gate.
- Coordination: two lines of work touched the same policies on the same day. Fixed in
  #73: `ai pipeline` stops with `NEEDS_HUMAN` when the base has commits the branch does
  not include, and asks for a rebase that renames any migration now out of order.

