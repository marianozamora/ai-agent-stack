Design a site that feels like an agency build. Before code, choose one vibe (ethereal glass for tech: near-black with mesh-gradient orbs; editorial luxury: warm cream, variable serif, 3% grain; soft structuralism: white or silver with diffused shadows) and one layout (asymmetric bento, z-axis cascade, editorial split), each with an explicit single-column collapse under 768px.

Banned: Inter/Roboto/Arial, thick Lucide/FontAwesome icons, generic 1px gray borders and harsh shadows, edge-glued sticky navs, symmetrical three-column grids.

Signature details: double-bezel cards (an outer shell with a hairline ring and padding, an inner core with its own highlight and a concentric smaller radius), pill CTAs with the trailing icon in its own circle, `py-24` to `py-40` sections, tiny uppercase eyebrow pills, a floating glass-pill nav that morphs into a staggered full-screen menu.

Motion uses custom cubic-beziers and IntersectionObserver fade-ups, never scroll listeners; `transform`/`opacity` only, `backdrop-blur` only on fixed elements, grain on a fixed `pointer-events-none` layer, `min-h-[100dvh]`, systemic z-indexes.
