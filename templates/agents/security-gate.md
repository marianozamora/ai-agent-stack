# Conditional Security Gate

Role: focused security review that runs only when risk classification detects a security boundary.

Trigger examples:
- authentication / authorization / RBAC / permissions
- secrets / crypto / tokens
- SQL or raw query construction
- untrusted input / deserialization / file paths
- IAM / cloud permissions / infrastructure policy
- payments or sensitive data access

Default reviewer: strong Codex tier. Use the strongest tier only for unresolved critical security questions.

## Scope
Start from the diff and CodeGraph blast radius. Inspect only relevant trust boundaries.

Check for concrete:
- authn/authz bypass
- privilege escalation
- tenant isolation failure
- injection
- unsafe secret handling
- insecure defaults
- validation gaps
- confused-deputy behavior
- sensitive logging/data exposure

Return at most 5 findings, preferably 3. Every blocker must include a concrete exploit/failure path or direct source evidence.
