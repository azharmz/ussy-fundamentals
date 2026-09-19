# TradingView FPI semantic validation — 2026-09-19

Status: **RESEARCH ONLY / FAIL CLOSED**

## Purpose

Validate the live TradingView fields used by the unsupported-FPI audit before any production fallback is permitted.

Candidate fields:

- EPS: `earnings_per_share_diluted_fq`
- Revenue: `revenue_fq`
- Comparison-only earnings field: `earnings_per_share_fq`

Historical TradingView arrays remain CURRENT_REVISED and are not approved for PIT backtesting.

## Positive controls

| Symbol | TV earnings EPS | TV diluted financial EPS | TV revenue | Official evidence | Result |
|---|---:|---:|---:|---|---|
| ARM | 0.4500 | 0.2505 | 1.289bn | GAAP diluted EPS 0.25; non-GAAP diluted EPS 0.45; revenue 1.289bn | diluted EPS and revenue align |
| ALC | 0.8425 | 0.0000 | 2.790287bn | diluted EPS 0.00; core diluted EPS 0.84; net sales 2.782bn | diluted EPS aligns; revenue close but provider-standardized |
| AS | 0.2200 | 0.1820 | 1.6326bn | diluted EPS 0.18; adjusted diluted EPS 0.22; revenue 1.6326bn | diluted EPS and revenue align |
| KYIV | 0.3300 | 0.3335 | 339m | IFRS basic/diluted EPS 0.33; total revenue 339m | diluted EPS and revenue align |

These controls support treating `earnings_per_share_diluted_fq` as the statement-oriented diluted EPS candidate rather than `earnings_per_share_fq`.

## Counterexample: BLSH revenue semantics

TradingView sample:

- `earnings_per_share_fq`: 0.094275
- `earnings_per_share_diluted_fq`: -1.7844
- `revenue_fq`: 92.6m

Bullish Q2 2026 official results report:

- diluted EPS: -1.78
- adjusted revenue (non-IFRS): 92.6m
- digital asset sales: 32.6bn
- net loss: -280.0m

Therefore the TradingView diluted EPS field aligns with the statement-oriented EPS, but `revenue_fq` can represent an adjusted/non-IFRS top-line measure rather than an IFRS/GAAP statement revenue concept.

## Decision

1. **EPS candidate survives semantic validation.** Keep `earnings_per_share_diluted_fq` as the live statement-oriented EPS candidate.
2. **Revenue candidate does not pass a universal SEC/IFRS-equivalence contract.** Do not promote `revenue_fq` as a generic replacement for SEC/IFRS statement revenue.
3. The 88-symbol set is a **coverage candidate set only**, not a production-approved fallback set.
4. Production integration remains blocked until revenue semantics are either:
   - sourced from a statement-oriented TradingView financial field with validated semantics, or
   - governed by an explicit issuer/industry-aware policy that does not silently mix adjusted and statement revenue.
5. SEC remains primary. Historical TradingView arrays remain prohibited for PIT backtests.

## Next research gate

Probe TradingView's statement-oriented revenue fields (especially `total_revenue_fq` / financial-data equivalents) against ARM, ALC, AS, KYIV and the BLSH counterexample. BLSH is the required negative control: a valid statement-revenue candidate must not silently reproduce adjusted revenue 92.6m while being labeled SEC/IFRS-equivalent.
