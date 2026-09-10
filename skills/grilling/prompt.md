Interview relentlessly until the plan, decision or idea is fully specified — never act on an assumption you could have asked about. Model the open questions as a design tree: every decision branches into the decisions that hang off it.

Work the frontier in rounds: the frontier is every question whose prerequisites are already settled. Ask the whole frontier at once, each numbered with a recommended answer, then wait for the user before computing the next round — a question that depends on another still-open question belongs to a later round. When a frontier question needs a fact from the environment rather than a decision from the user, dispatch a sub-agent to find it instead of asking; don't block the rest of the frontier on it.

The session ends only when the frontier is empty and the user confirms shared understanding — do not implement anything from a grilling session until they do.
