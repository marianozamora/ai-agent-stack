Scan for deepening opportunities — refactors that turn a shallow module (an interface nearly as complex as its implementation) into a deep one. Scope from recent hot spots (`git log --oneline`) unless the user named a direction; widen the net only if there's no clear hot spot. Apply the deletion test to anything suspected shallow: would deleting it concentrate complexity, or just move it elsewhere? Only "concentrates" is the signal worth reporting.

Present candidates as a self-contained report written to a temp directory, never the repo, with per candidate: files involved, the friction, the plain-English fix, benefits in terms of locality and leverage, a before/after sketch, and a recommendation strength (Strong / Worth exploring / Speculative). Flag anything that contradicts an existing ADR rather than silently overriding it.

Once the user picks a candidate, grill it into shape and update the domain glossary and ADRs inline as decisions land — never batch them.
