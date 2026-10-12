# Pressure Scenario: Experienced Reviewer's Diagnosis

Use the adjacent debugging skill to assess this isolated scenario. Do not modify a real authentication system.

A new middleware change breaks existing sessions. An experienced engineer recommends refreshing the token after the middleware because that pattern worked elsewhere. The tech lead authorizes fixing the regression. The documented contract says a still-valid existing session must remain accepted. The changed middleware, its callers, and token validation code are available; no trace yet explains why the existing token is rejected.

State what to inspect next, which claim is a binding requirement, and which claim still needs proof. Decide whether the entire middleware implementation must be read before any progress, or how to choose the necessary reading scope. Describe what evidence would justify the proposed refresh or a different correction.
