Build motion by deciding in order; stop at the first failed gate.

1. **Frequency**: keyboard-initiated or 100+/day actions get no animation; tens/day only near-imperceptible motion; occasional gets standard; rare/first-run may delight.
2. **Purpose**: name it — feedback, spatial consistency, state indication, preventing a jarring change, explanation, or delight (rare tier only). No name, no animation: say so and offer the static alternative.
3. **Cheapest tool**: CSS transition, `@starting-style`, CSS animation, WAAPI; a motion library only for springs, gestures or exits.
4. **Properties**: `transform`/`opacity` only; never `scale(0)` (start at 0.95 + opacity 0); popovers scale from their trigger, modals stay centered.
5. **Timing**: ease-out to enter/exit, never ease-in on UI; reuse existing tokens, else `cubic-bezier(0.23, 1, 0.32, 1)`. UI stays under 300ms (press 100–160, tooltip 125–200, dropdown 150–250). Springs for gestures, bounce 0.1–0.3.
6. **Interruption**: transitions, not keyframes, for anything re-triggered quickly; exit the way it entered.
7. Ship `prefers-reduced-motion` (gentler, not zero) and `(hover: hover) and (pointer: fine)` gating with it.

Write the code, then one line each: gate result, ingredients, what still needs a feel-check.
