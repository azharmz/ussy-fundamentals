# CAN SLIM sales semantics review — 2026-09-19

Status: **RESEARCH DECISION / PRODUCTION GUARDRAIL**

## Question

Does original CAN SLIM require a strict SEC/IFRS statement-revenue concept for the C component, or can a provider-adjusted/non-GAAP revenue measure be silently substituted?

## Evidence reviewed

Official IBD material consistently describes CAN SLIM/current-quarter analysis using **sales growth** alongside EPS growth. IBD examples report company revenue/sales figures and sales-growth percentages. IBD material also explicitly states that **nonrecurring items may be excluded from EPS**, demonstrating an earnings adjustment policy.

The reviewed IBD material did **not** establish a parallel rule authorizing adjusted/non-GAAP revenue to be substituted for reported sales. Therefore absence of an explicit revenue-adjustment rule must not be converted into permission to mix provider-adjusted revenue with statement revenue.

## Contract decision

For USSY CAN SLIM research/production:

1. Keep the metric name **sales/revenue growth** faithful to CAN SLIM.
2. Do not require the source to be SEC merely because CAN SLIM says “sales”; foreign issuers may use IFRS or another authoritative issuer statement.
3. Require **like-for-like sales semantics across compared quarters** and preserve the source/metric definition.
4. Prefer authoritative reported/statement revenue or sales.
5. Provider-standardized revenue may be admitted only if its semantic definition is documented and stable enough for YoY comparison.
6. Adjusted/non-GAAP revenue must **not silently substitute** for statement revenue. If intentionally used, it is a separate metric variant and requires an explicit research decision.
7. TradingView `revenue_fq` / `total_revenue_fq` therefore remain blocked as a generic statement-revenue fallback because the BLSH negative control maps to adjusted revenue.
8. TradingView `earnings_per_share_diluted_fq` remains a separate EPS candidate; its acceptance does not imply revenue acceptance.
9. Historical TradingView arrays remain non-PIT-safe unless independent availability timing is established.

## Practical implication

The production problem is now narrower than “TradingView is unusable.” EPS has a viable live candidate. Revenue needs an authoritative issuer/statement source or a separately validated provider-standardized sales contract. Do not weaken the semantic contract merely to increase FPI coverage.
