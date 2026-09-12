Actively sharpen the project's domain glossary as you design, not just read it. When a term conflicts with what `CONTEXT.md` already defines, call it out immediately and ask which meaning is intended. When language is vague or overloaded ("account"), propose the precise canonical term. Stress-test relationships with concrete edge-case scenarios rather than accepting an abstract description. When stated behavior and the code disagree, surface the contradiction rather than trusting either silently.

Update `CONTEXT.md` the moment a term resolves — don't batch it. `CONTEXT.md` is a glossary only: no implementation details, no scratch notes, no spec.

Offer an ADR only when all three hold: the decision is hard to reverse, it would surprise a future reader without context, and it came from a real trade-off between genuine alternatives. Skip it otherwise.
