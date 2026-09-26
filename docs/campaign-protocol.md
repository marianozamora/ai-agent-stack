# Validation Campaign Protocol

The question this campaign answers: does the stack make real work cheaper or better,
and which parts of it earn their tokens? `ai metrics label` and `ai metrics --campaign`
are the instruments (see the [field guide](field-guide.md#running-a-real-usage-validation-campaign));
this document is how to use them without fooling ourselves.

Why now: before this campaign the stack had ~2 real tasks recorded. The promize pilot
(2026-09-08) ended in 5 `FAILED` pipelines and 0 `PR_READY`, with the `cleanup` gate
spending ~1.15M input tokens over 4 rounds that raised different findings each time.
#56 targets that non-convergence, but it was measured on a single diff.

## 1. Questions and decision rules

Fixed before the first task. Do not move them after seeing data.

| Question | Measured by | Decision |
|---|---|---|
| Is `PR_READY` trustworthy? | Certification label (`--correct` / `--incorrect`) after human review or merge | Any `--incorrect` (with ≥5 labeled) is a correctness bug; fix before anything else |
| Does each model-judged gate find real problems? | `--true-positive` / `--false-positive` on every `FAIL` | FP rate > 20% (≥5 labels) → advisory. **0 true positives over ≥8 `FAIL`s → remove** |
| Do gates converge? | Attempts to `PASS` per gate | Median > 2 attempts on a gate → that gate is the problem |
| What does it cost? | Gate tokens (`--campaign`) + builder cost (manual, §4) | Profile median > 80% of its token budget → recalibrate |
| Is it better than not using it? | Paired tasks (§3) | Stack ≤ baseline in $, **or** it caught ≥1 real bug the baseline missed |

The first four are the thresholds `campaign_report()` already applies, or direct
extensions of them. The last one exists because the stack cannot be compared only
with itself.

## 2. Scope

- **20 real tasks over 2–3 weeks**: ~14 in promize, ~6 in danssme.
- **Task-type mix**: ~5 bugfix, 5 feature, 4 refactor, 3 UI/design, 3 chore/docs.
- **Profiles**: alternate per task, **`fast` on odd tasks, `standard` on even ones**.
  Use `strict` only for tasks touching payments or security.
- **Only work that was going to happen anyway.** No tasks invented for the campaign.

## 3. Baseline arm

**4 of the 20 tasks are done twice**, in separate worktrees:

- **A** — with the stack (`ai work` + `ai finish`).
- **B** — plain Claude Code with your usual prompt, no gates.

Compare total $, human minutes, and bugs found (before merge or in the following
2 weeks). Pick 4 medium-sized tasks of different types.

## 4. Per-task procedure

```bash
# 0. Start with a real contract
ai start <id> --ticket-file <ticket-or-spec.md>
ai clarify                       # NEEDS_HUMAN → fix the contract before going on

# 1. Build + gates
ai work --profile <fast|standard> --task-id <id>
ai finish                        # pipeline --resume + ready

# 2. After EVERY model-judged gate FAIL, within 24h
ai metrics label <gate> --task-key <key> --true-positive  --note "what it was"
ai metrics label <gate> --task-key <key> --false-positive --note "why not"

# 3. After your review / merge, and again at +2 weeks if a bug surfaces
ai metrics label --task-key <key> --correct|--incorrect --note "..."
```

Find `<key>` with `ai metrics --all-tasks --by task --json`.

Record what the stack cannot see in [`campaign-log-template.csv`](campaign-log-template.csv)
(copy it outside the repo, one row per task):

| Column | Meaning |
|---|---|
| `task_key` | From `ai metrics --all-tasks --by task` (blank for arm B) |
| `repo` | `promize` / `danssme` |
| `task_type` | `bugfix` / `feature` / `refactor` / `design` / `chore` |
| `profile` | `fast` / `standard` / `strict` (blank for arm B) |
| `arm` | `A` (stack, paired), `B` (baseline, paired), `solo` (stack, unpaired) |
| `builder_usd` | From `/cost` when the Claude session ends |
| `human_minutes` | Your hands-on time, roughly |
| `gate_rounds` | Total gate attempts before `PR_READY` or giving up |
| `outcome` | `PR_READY` / `NEEDS_HUMAN` / `FAILED` / `abandoned` |
| `allow_overrun` | `y` / `n` |
| `bug_post_merge` | `y` / `n`, filled at +2 weeks |
| `note` | Anything that explains an outlier |

`builder_usd` is manual because metrics record gate usage only, and Codex reports no
`cost_usd`; gate cost has to be derived from tokens.

## 5. Rules that keep the data clean

1. **No stack changes during the campaign.** A new stack version invalidates evidence
   and mixes cohorts. Fix blocking bugs only, and log the date.
2. **No `--allow-overrun`** unless unavoidable; mark it in the log when used.
3. **Label within 24h.** After that you no longer remember whether a finding was real.
4. **A gate that loops gets stopped and logged.** That is data too.

## 6. Check-ins

- **Every Friday**: `ai metrics --campaign --since 7d` — 10 minutes, spotting
  disasters only. No decisions yet.
- **Midpoint (task 10)**: a gate with 0 true positives and ≥5 false positives may go
  advisory early.
- **Early exit**: if `cleanup` fails to converge again in the first 5 tasks despite #56,
  that is the answer; stop and act on it.

## 7. Final analysis

From `ai metrics --campaign --json` plus the CSV, write up:

- **Gates**: which stay required, which go advisory, which are removed — with cost per
  real finding for each.
- **Default profile**: `fast` or `standard`.
- **Baseline**: does the stack pay for itself.
- **ROADMAP**: what moves in and what gets frozen. Anything that contributed nothing
  is frozen.
