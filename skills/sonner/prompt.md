Answer Sonner (React toasts) questions from this. Setup: exactly one `<Toaster />`, mounted near the root (Next.js `layout.tsx`); call `toast()` only from client code, never on the server.

Calls: `toast('Title', { description })`; `toast.success/error/info/warning`; `toast.loading` then update with the same `id`; `toast.promise(p, { loading, success, error })`; `action`/`cancel` buttons; `toast.custom(t => ...)` for headless JSX. `duration: Infinity` persists, `toast.dismiss(id)` closes, multiple toasters need an `id` plus `toasterId`.

Styling ladder: defaults (`richColors`, `invert`), then `style`, then `classNames` with `!important`, then headless `toast.custom` wrapped in your own API. `theme` defaults to light: pass `theme="system"` or the resolved theme.

Troubleshoot: never appears (no or unmounted Toaster, called on the server); appears twice (two Toasters, or a StrictMode effect: use a stable id); Tailwind ignored (`!important`); unstyled (import `sonner/dist/styles.css`); behind a modal (stacking context: move the Toaster to the root); stuck loading (the promise never settles).
