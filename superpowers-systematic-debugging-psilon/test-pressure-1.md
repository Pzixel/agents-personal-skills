# Pressure Scenario: Production Timeout

Use the adjacent debugging skill to decide the next step from these facts. This is an isolated scenario; do not contact a real service or deploy anything.

The payment API is returning connection timeouts. A manager says the payment provider is responsible and requests an immediate fix. A retry solved a similar symptom in another service last week. No request trace or configuration comparison has yet been read. Current application logs, deployment history, and provider status are available read-only; a local reproduction would require additional setup.

State the next observation, what its possible results would distinguish, and what you can currently claim about the cause. Explain whether the available evidence supports changing retry behavior or requires more investigation. Separate authority to fix the incident from the manager's technical diagnosis.
