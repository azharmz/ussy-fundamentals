# USSY Fundamentals

Point-in-time fundamental research pipeline for USSY. Initial scope: SEC EDGAR data for CAN SLIM **C** and **A** research. This repository is intentionally separate from production Supabase/Core TrendFoll.

## Current milestone

Build and validate a correct point-in-time extractor before running strategy CAGR comparisons.

Pipeline:

```text
Universe
  -> SEC ticker/CIK mapping
  -> submissions + historical submission files
  -> companyfacts
  -> immutable raw cache
  -> point-in-time normalizer
  -> fundamentals_point_in_time.parquet
  -> CAN SLIM feature research
```

Implemented now:

- official SEC ticker -> CIK mapping
- SEC `submissions` acquisition, including historical files listed by SEC
- SEC `companyfacts` acquisition
- identified `User-Agent` requirement
- throttling below 10 requests/second
- immutable raw JSON cache
- `filed_at` and `accepted_at`
- 10-Q/10-K and amendment metadata
- preferred-tag extraction for diluted EPS and revenue
- direct quarterly observations
- Q4 derivation from `FY - Q1 - Q2 - Q3` when valid
- quarterly EPS YoY and revenue YoY
- research flags `C_eps_25` and `C_revenue_25`
- long and wide Parquet outputs

## Important research rule

Never join fundamentals to a backtest only by fiscal period end. At decision time `T`, only information with:

```text
accepted_at <= T
```

may be visible to the strategy. For daily research, use a backward/as-of join and explicitly define whether a filing accepted after the decision cutoff becomes available on the next session.

## Setup

Python 3.11+ recommended.

```bash
python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
pip install -e .
pip install pytest
```

Set an identifiable SEC user agent containing a project/contact identity:

```powershell
$env:SEC_USER_AGENT="USSY Research your-email@example.com"
```

On Linux/macOS:

```bash
export SEC_USER_AGENT="USSY Research your-email@example.com"
```

## Run the five-symbol smoke universe

```bash
python -m ussy_fundamentals.pipeline --universe data/universe.sample.csv
```

Outputs:

```text
data/processed/fundamentals_point_in_time_long.parquet
data/processed/fundamentals_point_in_time.parquet
```

Raw SEC responses are cached under `data/raw/` and intentionally ignored by Git.

## Tests

```bash
pytest -q
```

## Validation plan

Before expanding to ~1,300 symbols, manually validate AAPL, MSFT, NVDA, AMZN and META across several 10-Q/10-K periods. Check:

1. accession/form and acceptance timestamp
2. quarterly EPS against the filing
3. quarterly revenue against the filing
4. Q4 reconstruction against annual minus Q1/Q2/Q3
5. YoY pairing against the comparable prior-year quarter
6. amendments/restatements and unusual fiscal calendars

Do not promote the C filter to production until this validation passes.

## Next milestones

1. harden quarterly reconstruction, including cumulative/YTD contexts and edge cases
2. add explicit EPS transition states for negative/positive denominator cases
3. add annual EPS history and A features
4. build a manual-validation report
5. expand acquisition to the full universe; consider SEC nightly bulk archives for scale
6. compare `BASE`, `BASE+C`, `BASE+A`, and `BASE+C+A` in a separate backtest layer

## Scope boundary

This repository produces research-grade point-in-time fundamental data. It does **not** modify Supabase, the production screener, or Core TrendFoll at this stage.
