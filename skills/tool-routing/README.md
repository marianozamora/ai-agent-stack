# tool-routing

Category: **efficiency**
Estimated context cost: **tiny**

Stages: confirm listed → reuse cache → escalate only on failure

Not selectable: this skill never competes for one of a profile's skill slots
(`ai skill list` still shows it, `ai skill explain tool-routing` still works). It
is loaded by `ai_stack/capabilities.py` directly, and only when at least one
external context tool (Code Review Graph, Graphify, CodeGraph, Context7, RTK) is
actually detected on this machine -- when none are, there is nothing to route
between and the guidance would be dead weight. Override it per-repository the
same way any other skill is overridden: create a `tool-routing` skill in the
repository's own external state and it shadows this one.
