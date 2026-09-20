# TradingView adjusted-EPS historical comparability result — 2026-09-19

Status: **CLOSED NEGATIVE RESULT**

Run: 35418652227
Artifact: 10577010543
Digest: sha256:deb66d5cf547cf666c935b5ade1a2c4466f34b6b35142f957f2ae21242d2c66b

## Result

The scanner accepts current `earnings_per_share_fq`, but `earnings_per_share_fq_h` returned no historical arrays for all 247 audited unsupported-FPI symbols.

Observed:
- audited symbols: 247
- symbols with non-empty reported/adjusted EPS history: 0
- structural live candidates in this run: 92
- candidates with non-empty financial diluted EPS history: 92
- candidates with non-empty reported/adjusted EPS history: 0

Therefore quarter-by-quarter historical comparability of TradingView's As-reported/adjusted EPS cannot be established from this scanner surface.

## Decision

1. Close this scanner-history path. Do not add more GitHub Actions runs trying variants of `earnings_per_share_fq_h` without new evidence that such a field is populated.
2. TradingView current `earnings_per_share_fq` remains useful only as a LIVE semantic candidate.
3. Do not use current adjusted EPS to manufacture historical YoY growth.
4. Financial diluted EPS history remains CURRENT_REVISED / NON-PIT and cannot replace the PIT SEC/issuer pipeline for historical backtests.
5. Production core remains unchanged/frozen.
6. For unsupported FPI production coverage, the next useful work is source acquisition for authoritative issuer-reported quarterly sales/EPS with publication dates, not further scanner-field probing.

Note: candidate counts can vary between scanner fetches as current provider data changes. This run produced 92 under the experimental total-revenue/current-field gate; it does not supersede production counts.
