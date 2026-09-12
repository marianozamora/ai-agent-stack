Write a `DESIGN.md` that Google Stitch, or any screen-generating agent, can follow: natural-language visual descriptions paired with exact values. Sections, in order:

1. **Visual theme and atmosphere**, rated on density (airy to cockpit), variance (symmetric to asymmetric) and motion (static to cinematic); default variance 8, motion 6, density 4, adapted to the brief.
2. **Color palette**: each color as descriptive name + hex + functional role; one accent under 80% saturation, no purple/neon, no pure `#000`, one gray family.
3. **Typography**: display, body (65ch), mono; no Inter or generic serifs, sans only for dashboards, mono numbers above density 7.
4. **Component stylings**: buttons, cards, inputs, skeleton loaders, empty and error states.
5. **Layout principles**: grid-first, max-width, no overlap, no three equal cards, `min-h-[100dvh]`, single column under 768px, 44px touch targets.
6. **Motion**: springs, staggered reveals, `transform`/`opacity` only.
7. **Anti-patterns** as explicit NEVER rules.

Describe utility classes in words ("generously rounded corners") and never leave a color without its hex.
