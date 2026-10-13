---
name: test-audit
description: "Invoke whenever writing, changing, reviewing, or sweeping tests. Authoring gate for new tests plus audit workflow for low-value, implementation-coupled, or duplicative tests and the test-only production seams they demand."
---

# Test Audit

The owner's global Tests rules decide every test: both value gates, the
exclusions, and independent sources for cases and expectations. This skill adds
only what those rules leave open.

**Unit of accounting.** A parameterized declaration is one entry. Mark rows
separately only when their decisions differ.

**Red tests.** In an audit without repair authority, a failing test that
exposes a product defect stays red and is reported as a finding. The audit is
complete when its findings are reported; repair and release are separate
deliveries, named as incomplete, never a reason to hold the audit open. With
repair authority, fix the defect at its owner in its own commit and re-run the
test.

**Sweeps.** For one subsystem's whole test surface (a baseline SHA, lanes,
ledger marks across many files), follow [CAMPAIGN.md](CAMPAIGN.md).

**Report.** Per test or row: retain, fix, consolidate, or delete (the campaign
ledger marks R, F, C, D); the contract clause or defect it guards; for a
consolidate or delete, the proof that survives without it. Before closing, name
the owning test for every contract clause in scope; a clause with none is a
finding, unless the owner's global Tests rules exclude testing it. Count fixed and
skipped; a skipped item names what blocks it.
