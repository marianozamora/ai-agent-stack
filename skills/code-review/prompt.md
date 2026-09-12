Review the diff between `HEAD` and a fixed point the user names (ask if they didn't) along two independent axes, each run as a **parallel sub-agent** so neither pollutes the other's context:

- **Standards**: does the diff violate anything this repo documents (`CONTRIBUTING.md`, a coding-standards file)? A documented rule always overrides the baseline below. Fowler baseline smells, always a judgement call, never a hard violation: Mysterious Name, Duplicated Code, Feature Envy, Data Clumps, Primitive Obsession, Repeated Switches, Shotgun Surgery, Divergent Change, Speculative Generality, Message Chains, Middle Man, Refused Bequest.
- **Spec**: does the diff match the originating issue/spec? Report missing requirements, scope creep beyond what was asked, and requirements that look implemented but aren't. Quote the spec for each finding. If no spec is found, skip this axis and say so.

Report the two axes under their own headings; never merge or rerank across them — a change can pass one and fail the other, and combining them hides that. Close with one line per axis: finding count and the worst issue in it.
