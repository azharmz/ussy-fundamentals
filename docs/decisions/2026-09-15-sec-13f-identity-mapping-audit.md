# Decision — SEC 13F Identity Mapping Audit

Date: 2026-09-15

Status: **CANONICAL US-ISIN→CUSIP PATH CONFIRMED / OPENFIGI DEMOTED TO FALLBACK AUDIT ONLY / LIVE COVERAGE ALREADY MEASURED**

## Correction

A repository audit found that the production 13F path already had a stronger identity bridge than the newly explored OpenFIGI path.

For project securities whose canonical `security_id` is a U.S. ISIN (`US` + 9-character CUSIP + ISIN check digit), the CUSIP used by Form 13F is recoverable deterministically as `security_id[2:11]`. No external identifier provider is required for those securities.

This is the canonical identity path for current production sponsorship evidence.

## Existing production evidence

`src/ussy_fundamentals/sec_13f_current_state.py` already implements `US_ISIN_BODY_TO_CUSIP9` mapping and processes Form 13F holdings with PIT filing/amendment lineage.

Canonical current-state run:

- workflow: `SEC 13F current PIT state`
- run: `34603142916`
- job: `103275091513`
- result: **SUCCESS**
- universe: **1,327** securities
- deterministic U.S.-ISIN identities: **1,010**
- non-U.S.-ISIN not evaluable by this deterministic path: **317**
- latest report period: **2026-06-30**
- latest-period mapped securities: **996**
- 13F filings processed: **9,731**
- fetch success rate: **100%**
- accepted-at completeness: **100%**
- amendment classification: **383/383**
- ambiguous lineage events: **0**
- data-quality gate: **PASS**

Thus latest-period coverage among deterministic U.S.-ISIN securities was `996 / 1010 ≈ 98.6%`. The remaining difference is not evidence of zero sponsorship; it means no evaluable mapped sponsorship state was present for those securities in that period.

## Canonical R2 publication already exists

The production sponsorship publisher also already ran successfully:

- workflow: `SEC 13F canonical sponsorship publish`
- run: `34663714292`
- job: `103471275697`
- result: **SUCCESS**
- status pointer: **READY**
- snapshot prefix: `institutional_sponsorship/snapshots/2026-09-12/run-34663714292`
- manifest: `institutional_sponsorship/snapshots/2026-09-12/run-34663714292/manifest.json`

The historical source processed **313,055** 13F filings across **53** official datasets. Historical state preserves uncertainty from ambiguous amendment lineages rather than fabricating certainty.

## OpenFIGI role after reconciliation

The newer `sec-13f-identity-mapping-v1.1` / OpenFIGI work is retained as research/audit evidence, especially for understanding venue-level FIGI duplication and possible future non-US or exceptional identity cases.

It is **not** required for the canonical U.S.-ISIN production path and must not replace a deterministic ISIN→CUSIP mapping with a network dependency.

If OpenFIGI is ever used as a fallback, it remains subject to the frozen safeguards:

- exact CUSIP input only;
- common-equity identity only;
- venue rows must converge on stable share-class/composite identity;
- no fuzzy issuer-name matching;
- no ticker-prefix guessing;
- no arbitrary first-result selection.

## PIT boundary

The ownership-information clock remains SEC filing availability. Current filing-level pipelines preserve `accepted_at`; historical bulk state uses its separately documented conservative historical-availability semantics. Quarter-end must never be treated as if the filing were already public then.

## CAN SLIM interpretation

This dataset is valid evidence for stock-level **I — Institutional Sponsorship**: manager count, reported holdings/shares/value, and changes through filing history.

It is **not** IBD Accumulation/Distribution Rating and it is **not** a real-time institutional buying/selling signal. It therefore does not by itself satisfy #51/#54 market-level `institutional accumulation/selling` evidence for the general-market classifier.

## Terminal decision

**STOCK-LEVEL CAN SLIM I DATA SOURCE: AVAILABLE / PIT-AUDITED / CANONICAL R2 SNAPSHOT LIVE**

**OPENFIGI: FALLBACK/IDENTITY-AUDIT TOOL ONLY, NOT A PRODUCTION PREREQUISITE FOR U.S.-ISIN SECURITIES**

**#54 MARKET-LEVEL INSTITUTIONAL DEMAND/SELLING: STILL SEPARATE AND UNRESOLVED**
