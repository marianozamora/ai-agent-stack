# Model Guidance

What the stack takes from each vendor's current guidance for the models it drives, and
how the prompt changes that guidance implies are measured instead of assumed. Sources
are listed at the end; re-check them when a model changes.

## What is applied by default

| Setting | Value | Why |
|---|---|---|
| `fast` builder | `claude --model sonnet --effort high` | Sonnet 5's guidance keeps `high` for most work and `xhigh` for the hardest agentic tasks; Claude Code otherwise runs at `xhigh`, more than a small `fast` change needs. Sonnet 5 respects effort strictly, so `low`/`medium` would risk under-thinking a moderate change. |
| `standard`/`strict` builder | Claude Code defaults | The model is the user's own. Opus 5.5 at its default `medium` already matches Opus 5 at `high` on coding with about half the tokens; lower effort before adding "think less" prose. |
| Builder commits | `includeCoAuthoredBy: false` | The provenance gate rejects attribution trailers. |
| Reviewer (Codex) effort | user default; `contract` capped at `medium`; `fast` capped at `medium` | Codex's guidance: `medium` as the all-round default, `high`/`xhigh` for the hardest tasks. The cap only ever lowers the user's setting, so `security` and `review` keep the default. |
| Reviewer prompt | diff inlined, batched reads, scoped `codegraph explore` | Codex's guidance: plan every read first and issue them as one parallel batch; each agent turn re-sends the conversation. |

Override per repository with `ai providers set --fast-builder-model`, `--fast-builder-effort`
(`''` = CLI default) and `--reviewer-effort GATE=LEVEL`; `ai providers show` prints what applies.

## What is offered as an experiment

Prompt rewrites change behaviour for every task, so they ship as **variant `b`** of an
experiment slot and compete with the current text (`a`) through `ai prompt experiment`.
Variant `a` of each slot renders exactly what the stack sent before.

| Slot | Variant `b` | Guidance behind it |
|---|---|---|
| `builder.policy` | The builder's working policy as prose that carries its reasons: the contract as the definition of done, why unrequested edits cost twice, what happens after the builder finishes. No strategy coaching ("classify first", "one tool per question") and no limits in the text that the code does not enforce. | Claude's prompt guidance for current models: prompts written for earlier models are often too prescriptive and reduce quality; prohibition lists and strategy coaching anchor behaviour; state the real constraint with its reason; control depth with effort, not prose. |
| `validator.review` | Code-review framing (bugs, risks, regressions, missing tests), one batched read with `rg`, findings most severe first with `path:line` and the triggering input, non-blocking notes in the evidence, and an explicit "no blocking issue" when so. | Codex Prompting Guide: review mindset, severity-ordered findings with file/line references, state explicitly when nothing is found, maximise parallel reads. |
| `validator.security` | Only the trust boundaries the change touches, one batched read, exploit path with `path:line` and who can reach it, categories checked and why the rest do not apply. | Same, applied to the security gate. |

Run one at a time (one experiment per repository):

```bash
ai prompt experiment start builder --variants a,b --min-samples 6
ai prompt report                      # per-variant gate pass rate, first-attempt pass rate, tokens
ai prompt promote builder b --confirm # human-only, and only once each variant has the samples
```

A builder variant is judged by the gates that follow it: every gate row records the
task's `builder.policy` variant, so the report compares first-attempt pass rates and
reviewer tokens across builder prompts. Measure during the validation campaign
([`campaign-protocol.md`](campaign-protocol.md)); do not promote on fewer samples than
the experiment's minimum.

## Deliberately not adopted

- **An `AGENTS.md` for Codex.** Codex reads durable guidance from `AGENTS.md`, but writing
  one into the reviewed repository would break the zero-footprint invariant; the stack
  passes its instructions in the prompt and keeps repository rules in external state.
- **"Report everything, filter later" for the reviewer.** Claude's guidance recommends it
  for Claude-as-reviewer recall, and it suits a pipeline with a separate filter. Here a
  `PASS` may not carry findings, so findings stay blockers and the rest goes to evidence.
- **Raising reviewer effort per gate.** The per-gate setting only lowers the user's
  default by design; raising it is a user choice (`--reviewer-effort security=high`).

## Sources

- Claude: model migration and prompt-audit guidance bundled with the Claude API skill
  (`shared/model-migration.md` — Sonnet 5, Opus 5.5, Fable 5.1 sections; `shared/prompt-audit.md`).
- Codex: [Codex Prompting Guide](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide),
  [Codex best practices](https://developers.openai.com/codex/learn/best-practices),
  [Custom instructions with AGENTS.md](https://developers.openai.com/codex/guides/agents-md).
