Start with the operational question the signal must answer and the failure path it must distinguish. Choose the smallest useful signal: structured logs for discrete context, RED/USE-style metrics for trends and service health, and traces for latency or causality across boundaries. Reuse established repository conventions and correlation identifiers.

Keep event names and fields stable, metric cardinality bounded, and secrets or unnecessary personal data out of telemetry. Every alert needs a user-visible symptom or service objective, an actionable threshold, an owner and a response or rollback path. Telemetry with no expected consumer is residue, not observability.

Verify the signal at its real emission boundary with a test or runtime capture, including the relevant failure path. State where an operator will read it and how it changes a decision. Do not claim coverage from instrumentation code alone, and do not broaden a product change into an observability-platform redesign.
