# Decision — SEC 13F Identity Mapping Audit

Date: 2026-09-15

Status: **EXACT MAPPING PATH APPROVED / FUZZY MAPPING FORBIDDEN / LIVE COVERAGE NOT YET MEASURED**

Contract: `sec-13f-identity-mapping-v1`

## Question

Can a Form 13F holding identified by CUSIP be mapped into the project's stock/security identity without guessing from issuer name or loosely matching ticker text?

## Findings

1. Form 13F information tables carry `CUSIP` as a security identifier. Current SEC Form 13F technical specifications also allow an optional `FIGI` field in the information table.
2. SEC's public ticker association files map CIK/company/ticker/exchange, but do not provide a general CUSIP-to-ticker bridge. Therefore SEC-only identity resolution is insufficient for arbitrary 13F holdings.
3. OpenFIGI's public Mapping API explicitly supports `ID_CUSIP` and returns FIGI plus security metadata including ticker, market sector, security type and exchange code. Unauthenticated usage is rate-limited but available without a paid data contract.
4. A CUSIP may map to an instrument outside the project's tradable universe. This is not an error; it is `NOT_EVALUABLE` for project-level sponsorship evidence.
5. Mapping must be exact and unique. Issuer-name similarity, ticker prefixes, manual substitutions, and after-the-fact share-class guesses are not permitted.

## Frozen identity policy

A 13F holding may be attached to a project security only when all of the following are true:

- the filing holding has a non-empty CUSIP;
- an audited identifier provider returns a result for that exact CUSIP;
- the result carries a FIGI and ticker;
- the returned instrument is in the Equity market sector;
- exactly one eligible provider result remains;
- the returned ticker exactly matches exactly one project security identity.

Otherwise the holding remains `NOT_EVALUABLE` for project-security aggregation.

The first approved provider candidate is `OPENFIGI` for identity translation only. OpenFIGI is not the ownership source and does not replace SEC filing provenance.

## PIT and provenance

The ownership observation's availability clock remains the SEC filing `accepted_at` timestamp from `sec-13f-institutional-sponsorship-v1`.

Identifier-provider metadata must preserve at least:

- input CUSIP;
- returned FIGI;
- returned ticker;
- provider identity;
- provider observation/fetch timestamp;
- mapping contract version.

The mapping provider timestamp does not move the 13F holding backward in time and cannot be used to backdate an ownership observation before its SEC `accepted_at` timestamp.

## Explicitly forbidden

- issuer-name fuzzy matching;
- ticker-prefix matching;
- guessing share class;
- mapping a debt/warrant/option result into the common stock merely because issuer names match;
- resolving multiple eligible results by arbitrary first-match behavior;
- treating absence from the project universe as zero institutional ownership;
- representing this identity mapping as IBD Accumulation/Distribution evidence.

## Implementation

`src/ussy_fundamental/sec_13f_identity_mapping.py` implements the provider-agnostic exact mapping gate. `tests/test_sec_13f_identity_mapping.py` covers exact success, ambiguity, non-equity results, missing FIGI/CUSIP, and project-universe identity failures.

## Next gate

Run a small descriptive coverage audit against a deterministic sample of current project securities / 13F CUSIPs using OpenFIGI. Measure only:

- exact unique mapping rate;
- ambiguous mapping rate;
- non-equity mapping rate;
- outside-universe mapping rate;
- provider failures/rate-limit behavior.

Do not yet build a full 13F production ingestion pipeline.
