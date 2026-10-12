---
name: superpowers-verification-before-completion-psilon
description: Use before claiming completion when work affects production, security, data integrity, concurrency, migrations, external contracts, releases, or broad multi-component behavior, or when the user requests rigorous completion proof. Skip clear low-risk local edits. After the last relevant change, prove the exact outcome, target, and prerequisites with fresh evidence.
---

# Verification Before Completion

Use the description as the scope gate. Before a high-risk completion, commit, push, PR, or release claim, obtain fresh evidence after the last relevant change for that exact claim and target. Keep low-risk verification focused; do not repeat unchanged gates without a new risk.

## Check the Claim

1. Define the outcome, target, required gates, and load-bearing prerequisites from authoritative requirements.
2. Obtain the matching commands or observations. Inspect their relevant full output, exit status, failures, revision, and target coverage; search for disconfirming or limiting evidence.
3. Report the proved scope. Missing or contradictory evidence leaves the affected gate incomplete; continue independent authorized work. Do not replace missing proof with confidence, a plan checklist, or an agent's summary.

## Proof Boundaries

| Claim | Required proof | Not enough |
|---|---|---|
| Tests pass | Named test scope reports zero failures on the relevant revision | Old run; “should pass” |
| Lint/build passes | Exact command exits zero | Another gate; partial check |
| Bug fixed | Original symptom passes at the required target; prerequisites hold | Code changed; similar fixture |
| Regression test works | Admitted test distinguishes broken and fixed behavior | One passing run |
| Agent completed | Inspect its actual work and verify the assigned outcome | Agent report |
| Requirements met | Each authoritative requirement has matching target-boundary evidence | Tests or plan checklist alone |
| Release complete | Every agreed check, reviewed revision, merge, rollout, authenticated behavior, and business observation is evidenced where required | CI or a ready deployment alone |

A mechanism-level pass proves only the exercised scope, not another target's data, configuration, coverage, or deployment. Plans and reports locate evidence; they do not replace it. A documented decision cannot close a known defect or waive a required gate.

Add tests only when both the behavior and a plausible defect qualify under governing policy. For an admitted regression test, capture failure before the fix and pass after it when feasible; use a safe revert or mutation only when proportionate and safe for user work. Test exclusions apply to disposable checks too. Otherwise use direct inspection or relevant operational evidence.
