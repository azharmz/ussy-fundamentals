# Decision — Automated Incremental SEC Fundamental Update

Date: 2026-09-14
Status: **ADOPTED — EXISTING CAPABILITY, HARDENING REMAINS**
Repository owner: `azharmz/ussy-fundamentals`

## Decision

`ussy-fundamentals` owns the production lifecycle for SEC-derived fundamental updates. The target operating model is event/delta driven rather than a periodic full-universe rebuild:

```text
scheduled SEC discovery
→ compare current SEC filing state with the published baseline
→ identify only impacted securities
→ run the PIT SEC pipeline only for impacted securities
→ merge the delta into the current baseline
→ audit/rebuild reports
→ publish a new immutable R2 snapshot
→ advance the stable current pointer only after a successful publication
```

The information boundary remains `accepted_at`: a filing must not be visible to a point-in-time consumer before SEC acceptance.

## Existing implementation confirmed

The repository already contains `.github/workflows/incremental-fundamentals-update.yml` with:

- manual dispatch;
- scheduled execution (`15 6 * * 1-6`);
- canonical-universe freeze before update planning;
- current baseline download from R2;
- impacted-security planning;
- SEC PIT processing only for impacted securities;
- delta merge;
- audit/report rebuild;
- immutable R2 publication when an update is needed;
- diagnostic artifact publication.

`src/ussy_fundamentals/incremental_plan.py` already detects:

- securities added to the canonical universe;
- securities removed from the canonical universe;
- a newer supported SEC filing than the latest accepted filing represented in the baseline;
- domestic forms `10-Q`, `10-Q/A`, `10-K`, and `10-K/A`.

The current planner therefore already implements the core automatic discovery/update mechanism discussed on 2026-09-14.

## PIT rule

For any decision date/time `T`, fundamental information is eligible only when:

```text
accepted_at <= T
```

Fiscal period end alone is never sufficient to establish information availability.

## Production-hardening roadmap

The remaining work is not to rebuild the updater from scratch. It is to make the existing mechanism more explicit and operationally auditable:

1. **Accession-level processing ledger / watermark**
   - preserve the last processed accession(s) or equivalent immutable filing identity per issuer;
   - make reruns demonstrably idempotent;
   - distinguish a genuinely new filing from a previously processed filing with the same fiscal period.

2. **Amendment lineage**
   - retain `10-Q/A` and `10-K/A` as new PIT events rather than silently rewriting the historical state;
   - preserve original accession, amendment accession, acceptance timestamps, and parser version.

3. **Parse failure quarantine and retry**
   - one issuer/parser failure must not corrupt or block an otherwise valid baseline;
   - failed filings should remain visible as explicit operational debt and be retryable.

4. **Operational observability**
   - record discovery time, filing/accession identity, accepted time, update reason, processing result, publication run ID, and resulting snapshot/pointer lineage;
   - preserve a clear `NOOP` outcome when no issuer requires an update.

5. **Failure-safe publication**
   - never move the canonical pointer to a partially built or failed snapshot;
   - retain the previously valid production snapshot on discovery, SEC, parsing, merge, audit, or publication failure.

## Scope boundary

This decision concerns the fundamental data lifecycle only. CAN SLIM C/A semantics remain owned by the CAN SLIM research layer. The updater must publish PIT-safe fundamental facts and provenance; it must not change C/A thresholds or reinterpret CAN SLIM theory.

## Terminal intent

Once the hardening items above are verified, the desired operational status is:

`AUTOMATED INCREMENTAL SEC UPDATER / PRODUCTION-READY`

Until then, the accurate status is:

`AUTOMATED INCREMENTAL SEC UPDATER / IMPLEMENTED — HARDENING & OPERATIONAL AUDIT REMAIN`
