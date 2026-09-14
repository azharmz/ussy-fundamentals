# USSY Fundamentals Roadmap

Last updated: 2026-09-14

## Production data lifecycle

| Workstream | Status | Notes |
|---|---|---|
| SEC PIT acquisition and normalization | **IMPLEMENTED** | Uses SEC submissions/companyfacts with `accepted_at` PIT boundary. |
| Canonical-universe integration | **IMPLEMENTED** | Reads frozen/current canonical universe from `ussy-data` R2 contract. |
| Incremental SEC filing discovery | **IMPLEMENTED** | Detects newer supported domestic filings and universe membership changes. |
| Impacted-symbol-only recompute | **IMPLEMENTED** | Re-runs the PIT pipeline only for impacted securities, then merges into baseline. |
| Immutable R2 publication | **IMPLEMENTED** | Publishes a new snapshot only when update work is required. |
| Scheduled automatic updater | **IMPLEMENTED** | GitHub Actions schedule currently defined in `incremental-fundamentals-update.yml`. |
| Accession-level watermark / idempotency ledger | **PLANNED HARDENING** | Formalize immutable filing identity and rerun safety beyond timestamp comparison. |
| Amendment lineage audit | **PLANNED HARDENING** | Preserve 10-Q/A and 10-K/A as separate PIT events with original/amendment lineage. |
| Parse-failure quarantine / retry | **PLANNED HARDENING** | Keep one issuer failure from blocking or corrupting the valid production baseline. |
| Incremental operational observability | **PLANNED HARDENING** | Persist discovery/result/run/pointer lineage and explicit NOOP outcomes. |
| Production-readiness audit | **PLANNED** | Verify the automatic updater end-to-end before CAN SLIM v1 is called fully production-ready. |

## Target steady-state flow

```text
scheduled SEC discovery
      ↓
new supported filing / universe change?
      ↓
NO → explicit NOOP
YES
      ↓
process only impacted securities
      ↓
PIT validation (`accepted_at`)
      ↓
merge delta into current baseline
      ↓
audit + reports
      ↓
immutable R2 snapshot
      ↓
advance stable pointer atomically
```

Canonical decision: `docs/decisions/2026-09-14-automated-incremental-sec-update.md`.
