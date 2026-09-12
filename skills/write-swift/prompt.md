Write Swift with progressive disclosure: start with the simplest, most static, single-threaded thing and add dynamism only with a stated reason.

Data: `struct`/`enum` and `let` by default; `class` only for identity, sharing or lifetime; enums for mutually exclusive state; copy-on-write for out-of-line storage; `~Copyable` for unique ownership. Errors: recoverable means `throw` (enums with associated values), programmer mistakes mean `precondition`; `guard` for exits; typed throws for internal code only.

Concurrency (Swift 6.2 model): stay on the main actor; `async` alone does not leave the caller's actor; use `@concurrent` for your CPU-heavy work only after Instruments shows a hang, `nonisolated` for library APIs, actors only to move state off the main actor; re-check state after every `await` and never hold a lock across one. Enable Approachable Concurrency and, for app modules, main-actor default isolation.

Prefer `some P` over `any P`, `async let` and bounded task groups over loose `Task`s, stop sharing instead of `@unchecked Sendable`, Swift Testing (`@Test`, `#expect`, `#require`), `Logger` over `print`. Migrate to Swift 6 per target, UI layer first, never combined with a refactor.
