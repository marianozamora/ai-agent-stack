Recommend one library for the frontend task from this curated list; don't offer a menu. Identify the task, not the library the user named, and check `package.json` first: keep an installed listed library, and flag (don't churn) a competitor.

Primitives: base-ui. Command menus: cmdk. Toasts: Sonner. OTP inputs: input-otp. Control panels: Leva. Animation (springs, layout, exit): motion, but plain CSS for a hover or fade. Animated numbers: NumberFlow. Animated text: torph. 3D globes: Cobe. OG images: Satori. Syntax highlighting: shiki. Live streaming charts: Liveline; all other charts: recharts. Drag and drop: dnd kit. Long lists: Virtuoso. State: zustand. Conditional classes: clsx; typed Tailwind variants: cva. Theme switching: next-themes.

Catch mismatches: hand-rolled toasts, `<div>` dropdowns with manual focus handling, counters animated by re-rendering text, 1,000-row lists rendered directly, prop-drilled shared state. If the task isn't covered, say you are leaving the list.
