# CAN SLIM EPS semantics review — 2026-09-19

Status: **RESEARCH DECISION / DO NOT CHANGE PRODUCTION YET**

## Question

For original CAN SLIM-style current quarterly earnings, should USSY automatically prefer statement/GAAP diluted EPS, or should it permit an adjusted EPS concept?

## Evidence and observed controls

IBD educational/public examples commonly discuss adjusted EPS in growth-stock analysis. The existing TradingView semantic controls show:

- ARM: `earnings_per_share_fq` ≈ 0.45 and financial diluted EPS ≈ 0.25; issuer reports non-GAAP diluted EPS 0.45 and GAAP diluted EPS 0.25.
- ALC: `earnings_per_share_fq` ≈ 0.84 and financial diluted EPS 0.00; issuer reports core diluted EPS 0.84 and diluted EPS 0.00.
- AS: `earnings_per_share_fq` ≈ 0.22 and financial diluted EPS ≈ 0.18; issuer reports adjusted diluted EPS 0.22 and diluted EPS 0.18.

Therefore the TradingView earnings field is not merely an erroneous version of statement EPS; in these controls it tracks the adjusted/core earnings concept used in growth-stock analysis.

## Contract decision

1. Do **not** redefine original CAN SLIM C as “GAAP/IFRS diluted EPS only.”
2. Preserve two distinct concepts:
   - `statement_diluted_eps`: authoritative GAAP/IFRS diluted EPS.
   - `canslim_adjusted_eps_candidate`: provider/issuer adjusted or core EPS when the adjustment semantics are attributable and comparable.
3. Never silently mix the two concepts within a YoY growth series.
4. For CAN SLIM-style screening, an adjusted EPS series may be closer to IBD practice, but it requires a stable like-for-like series and provenance.
5. TradingView `earnings_per_share_fq` is therefore promoted only from “suspect” to **RESEARCH CANDIDATE**, not production-approved.
6. TradingView `earnings_per_share_diluted_fq` remains useful as a statement-oriented control/fallback candidate.
7. Revenue remains governed separately; acceptance of adjusted EPS does not authorize adjusted revenue.
8. Historical TradingView arrays remain non-PIT-safe without independent availability timestamps.

## Next implementation gate

Extend the audit output so EPS semantics are explicit rather than overloaded:

- reported/adjusted EPS candidate
- financial statement diluted EPS
- semantic classification
- provenance
- PIT/live-only status

Then validate YoY comparability across several consecutive quarters for a deterministic issuer sample before selecting the CAN SLIM production EPS series.
