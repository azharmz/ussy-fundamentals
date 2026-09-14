# SEC 13F Institutional Sponsorship Sidecar — Preregistration

Date: 2026-09-14
Status: PREREGISTERED / SOURCE APPROVED FOR SPONSORSHIP EVIDENCE / NOT AN ACCUMULATION-DISTRIBUTION RATING

## Purpose

Add a point-in-time institutional-sponsorship evidence sidecar for CAN SLIM `I` using SEC Form 13F. This is deliberately separate from the existing issuer-fundamental C/A pipeline and separate from #54 market-leadership price evidence.

## Authoritative source boundary

Primary source: SEC EDGAR Form 13F filings / SEC Form 13F structured data.

SEC states that Form 13F datasets are extracted from the XML-based portion of Form 13F submissions, are presented as filed, and are updated quarterly. Form 13F is required for institutional investment managers meeting the statutory reporting threshold and is generally due within 45 days after quarter end.

Therefore this source is suitable for delayed institutional sponsorship/ownership evidence, not real-time institutional accumulation.

## PIT clock

For every observation, the earliest usable timestamp is the actual EDGAR filing availability timestamp (`accepted_at` when available; otherwise an explicitly documented filing-availability timestamp).

The reporting-period end MUST NOT be treated as the information-availability date.

Example: a holding for quarter ended 2026-06-30 filed/accepted on 2026-08-05 is not available to a 2026-07-31 decision.

## Required lineage

Preserve at minimum:

- manager CIK
- accession number
- form type (`13F-HR`, `13F-HR/A` as applicable)
- reporting period
- filing date
- `accepted_at` / availability timestamp
- issuer name as filed
- CUSIP as filed
- title/class as filed
- value as filed
- shares / principal amount as filed
- share/principal type
- put/call when present
- investment discretion / voting fields when present
- source URL or SEC source identifier
- parser/version

## Amendment semantics

`13F-HR/A` must not be silently appended as if it were an unrelated new manager holding. Amendments require accession-level lineage and explicit amendment semantics. The effective PIT view at time T must use only filings/amendments available by T.

No future amendment may rewrite what the system would have known before that amendment became public.

## Evidence outputs allowed

After security identity resolution is validated, a per-security PIT sidecar may expose descriptive evidence such as:

- observable reporting-manager count
- aggregate reported shares
- aggregate reported value
- change versus the previous comparable PIT snapshot
- new reporting managers
- exited reporting managers
- evidence completeness / identity-resolution status

These are evidence fields, not automatic PASS/FAIL thresholds.

## Explicitly forbidden interpretations

This sidecar MUST NOT be represented as:

- IBD Accumulation/Distribution Rating
- real-time institutional buying/selling
- complete beneficial ownership
- proof that every reported manager is currently accumulating
- proof that absence from 13F means no institutional ownership
- a deterministic #51 `leadership_confirming` or `weakening_confirmed` boolean by itself

No numeric sponsorship threshold is introduced in this decision.

## Identity-resolution gate

13F holdings are security-identified primarily through filed security identifiers such as CUSIP, whereas the USSY production universe is ticker/security-id oriented. Production aggregation is therefore blocked until a reproducible PIT-safe security identity mapping is audited.

Ticker/name fuzzy matching is not authorized as a silent production mapping.

## Architecture

```text
SEC EDGAR 13F
  -> immutable filing/accession layer
  -> PIT amendment-aware holdings view
  -> security identity mapping gate
  -> institutional sponsorship evidence sidecar

Issuer fundamentals (10-Q/10-K)
  -> existing C/A pipeline

These remain separate evidence channels.
```

## Initial implementation decision

Do not download or rebuild the entire historical 13F corpus merely to unblock CAN SLIM v1. First implement and validate the semantic/PIT contract and identity-resolution gate. A prospective or bounded historical ingestion cycle may follow only after the mapping audit is defensible.
