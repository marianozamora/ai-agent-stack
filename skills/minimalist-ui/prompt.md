Build a warm, editorial, document-style interface.

Palette: white or bone (`#F7F6F3`) canvas, off-black text (`#111111`/`#2F3437`, line-height 1.6), muted gray secondary (`#787774`), borders `#EAEAEA`; color only as washed-out pastel tags (e.g. `#E1F3FE` with `#1F6C9F` text). Type: an editorial serif for hero headings and quotes with tight tracking (-0.02 to -0.04em), a characterful sans for UI (not Inter/Roboto/Open Sans), mono for code and keys.

Components: asymmetric bento grids with 1px `#EAEAEA` borders, 8-12px radius, 24-40px padding; solid `#111` primary buttons with 4-6px radius and no shadow; accordions as bottom-border rows with `+`/`-`; `<kbd>` keys. Banned: gradients, neon, heavy shadows (keep under 0.05 opacity), pill-shaped cards or primary buttons, large saturated sections, emojis, Lucide/Feather, placeholder names and AI copy cliches.

Motion is quiet: a 12px fade-up over 600ms `cubic-bezier(0.16, 1, 0.3, 1)` via IntersectionObserver, 80ms stagger, `scale(0.98)` on press, `transform`/`opacity` only.
