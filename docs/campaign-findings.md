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


### Task: #103 fail closed on missing secrets (fast, first `ai loop` run)

`PR_READY` after three `ai loop` runs and two manual `ai pipeline --resume --allow-overrun`
calls: 3 commits, 20 files, +466/-102, including a `service_role`-only `email_exists`
migration. Builder ~$2.23 over 7 sessions (Sonnet, `fast`); gates ~1.1M input tokens over
10 pipeline runs, most of them cached.

What the gates caught, all real: the `check-email` IP limit trusted the leftmost
`x-forwarded-for`, which a client controls behind Cloudflare, so rotating it bypassed the
limit while enumerating emails (`security`); the limiter fell back to a per-isolate
in-memory store without Upstash or on a Redis error, so the enumeration defence failed
open (`security`, then `review` and `contract`); a Supabase CLI version marker left in the
tree (`cleanup`). The operator first ruled the fallback out of scope, then chose to make
`check-email` fail closed in production when the gates kept raising it.

**Gaps found:**

- **Reviewer misconfiguration burned three builder rounds.** `~/.codex/config.toml` named
  a model the ChatGPT account cannot use. The reviewer exited 1, the gate reported
  `NEEDS_HUMAN`, the pipeline turned it into `FAILED`, and `ai loop` ran three more builder
  rounds (~$0.60) against a gate that never judged the code. Those errors also counted
  toward `contract`'s retry budget and left FAIL rows with no findings. A reviewer error
  should stop the loop at once, never count as a verdict, and `ai doctor` should catch an
  unusable reviewer model. Worked around with `ai providers set --reviewer-model`.
- **A scope decision only reaches the builder.** `ai loop --note` is appended to the
  builder prompt, so `review` and `contract` re-raised the finding the operator had ruled
  out of scope. The stack still has no way to tell the gates where the ticket stops
  (see #102).
- **The round cap fired on a passing verdict.** `security` was stopped at its limit of 3
  FAILs, two of them `cached` rows from older code, while the latest shared call had
  judged the current code PASS. The cap should consider the newest verdict before
  stopping.
- **Environment noise reran every model gate.** Reverting the CLI marker changed the
  tree fingerprint, so the final pipeline re-judged identical code (~250k input tokens).
- **The builder cannot read its own contract.** Every session tried to `cat` the
  contract, which lives in external state, and was denied; heredoc and `python3` edits
  were denied too (it fell back to Write).
- Reviewer variance again: `review` passed the rate-limit code in one run and failed the
  same code in the next.

### Task: #116 fail closed on rate limits without Redis (standard, `ai loop`)

The first run after #77. `PR_READY` in one round twice: the first run (builder $0.72,
~83k billable gate tokens) passed every gate; after an operator review of the diff, a
second run with `--note` (builder $0.58, ~91k) also passed in one round. 2 commits,
`fix/rate-limit-fail-closed`, PR danssme#117. The cheapest task of the campaign so far
(earlier `standard` tasks: ~450k per task).

**What the gates missed and a human caught** (both in the first run's diff, all gates
PASS):

- `scanTicket` was made fail-closed: a Redis outage during an event would stop staff
  scanning tickets at the door. The ticket listed "tickets" as an example of fail-closed,
  and the gates checked the code against that example rather than against what the
  call is for.
- Four callers (chat, mapbox proxy, contact, search) still read the client IP from the
  leftmost `x-forwarded-for`, the spoofing #103 fixed in `getDefaultIdentifier`. With
  fail-closed limits, a client could still rotate the header to evade them. Outside the
  contract's criteria, and neither `review` nor `security` raised it.

Two false negatives on a `PR_READY` that took one round. A clean first round is not
evidence the change is complete: the human review of the diff stays necessary.

**Stack behaviour after #77:** `--note` reached the gates (`contract` accepted
`scanTicket` as fail-open despite the ticket's example). No reviewer errors occurred, so
the new stop path was not exercised.

### Incident: #103 took email sign-in down in production

#115 (#103) was certified `PR_READY`, merged and auto-deployed at 14:04 UTC on
2026-10-03. `check-email` then returned 429 to every request for ~1h50m: email sign-in
and sign-up were unavailable until hotfix danssme#118 (`failClosed: false`) deployed at
15:58. Google sign-in kept working.

Cause: the operator chose "fail closed in production without Redis" for `check-email`
after the gates kept raising the in-memory fallback, on the assumption that Upstash was
configured in production. It was not: `UPSTASH_REDIS_REST_URL`/`TOKEN` were only
documented in a `wrangler.jsonc` comment, never set as Worker secrets. Nothing in the
stack checked it, and the generated PR summary listed `CRON_SECRET` and
`TICKET_ENCRYPTION_KEY` as deploy prerequisites but not Upstash.

The certification was wrong in the sense that matters: the change was ready by its
contract and broke production on deploy. #103 is relabeled `incorrect`. #117 (#116)
extends the same fail-closed behaviour to more endpoints and is held until Upstash is
configured.

**Lessons:**

- A fail-closed change is a deploy-time dependency. A contract that adds one must name
  the production configuration it needs in `risk_notes`, and the operator must verify
  it (`wrangler secret list`) before merging, not infer it from documentation.
- In a repository that deploys on merge, `PR_READY` is one step from production. The
  gates judged the code against its contract; none of them can see the target
  environment.

### Task: #95 enforce ownership in competition RLS (fast, `ai loop`)

PR danssme#119 after 3 `ai loop` runs (7 builder sessions, ~$3.40), 5 operator commits
and 7 pipeline runs (~400k billable gate tokens). The gates' judgment converged
(`contract`, `review`, `security` PASS); `summary` and the rest never ran.

What it cost and why:

- **Docker was down for the first run.** `regression`'s pgTAP step failed with its
  output sent to `/dev/null`; the gate recorded FAIL and `ai loop` paid for three
  builder rounds (~$1.13) on an environment the builder may not start. Fixed by
  `ai preflight` (#79).
- **The builder did not do what the note asked** (a pgTAP case), and `contract`
  reached its round limit; the operator wrote the missing case and the later fixes.
- **Deepening plus variance.** Each pipeline run found new, real issues in code earlier
  runs had passed: a revoked judge reinstating themselves, a score for a slot of another
  round, organizer-only participant inserts, confirmed participants visible in pending
  competitions. All were real; none were in the first verdicts.
- **An operator note became an impossible criterion.** A request aimed at the PR
  summary was enforced by `contract`, which runs before the summary exists.
- **The summary bundle outgrew the `fast` context cap** (12,671 > 12,000) because the
  context embeds the full verdicts of every earlier gate; with a 9-criterion contract
  it no longer fits. Recorded as a FAIL of the change until #79.
- An operator commit failed commitlint (header too long) unseen, and the gates judged
  the staged tree.
