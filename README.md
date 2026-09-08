# USSY Fundamentals

Point-in-time fundamental research pipeline for USSY. Initial scope: SEC EDGAR data for CAN SLIM **C** and **A** research. This repository is intentionally separate from production Supabase/Core TrendFoll.

## Current milestone

Build and validate a correct point-in-time extractor before running strategy CAGR comparisons.

Pipeline:

```text
Canonical USSY universe (private R2 pointer)
  -> SEC ticker/CIK mapping
  -> submissions + historical submission files
  -> companyfacts
  -> immutable raw cache
  -> point-in-time normalizer
  -> fundamentals_point_in_time.parquet
  -> CAN SLIM feature research
```

Implemented now:

- canonical current universe loader from `ussy-data` private R2
- stable-pointer guard around `universe/current.json`
- Musaffa compliance contract: `security_id` + `ticker` + `sharia_compliance == COMPLIANT`
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
- Q4 revenue derivation when valid; non-additive EPS reconstruction excluded
- quarterly EPS YoY and revenue YoY
- annual EPS state/history
- CAN SLIM production-window audit
- requested-universe reconciliation and explicit unsupported/failure taxonomy
- long and wide Parquet outputs

## Important research rules

Never join fundamentals to a backtest only by fiscal period end. At decision time `T`, only information with:

```text
accepted_at <= T
```

may be visible to the strategy. For daily research, use a backward/as-of join and explicitly define whether a filing accepted after the decision cutoff becomes available on the next session.

Current Musaffa membership is for production-now selection only. Historical backtests must use the latest immutable membership snapshot that was actually available on or before each `as_of_date`; never backfill current membership into history.

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

For the canonical universe source, this repo uses the same private Cloudflare R2 contract as `azharmz/ussy-data`:

```text
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
R2_ENDPOINT
R2_BUCKET_NAME
```

The loader reads `universe/current.json`, follows `membership_key`, validates the COMPLIANT count against `confirmed_compliant`, and re-reads the pointer before accepting the snapshot so a run cannot mix two universe versions.

Fetch the current production universe:

```bash
python -m ussy_fundamentals.universe_source
```

Generated local files:

```text
data/processed/current_universe.csv
data/processed/current_universe_source.json
```

The metadata file preserves the canonical `snapshot_date`, `membership_key`, `security_master_key`, and `change_key` used by the run.

## Run a local universe

```bash
python -m ussy_fundamentals.pipeline --universe data/universe.sample.csv
```

For a current R2 universe run after fetching the canonical snapshot:

```bash
python -m ussy_fundamentals.pipeline --universe data/processed/current_universe.csv
```

Outputs:

```text
data/processed/fundamentals_point_in_time_long.parquet
data/processed/fundamentals_point_in_time.parquet
data/processed/fundamentals_run_manifest.parquet
```

Raw SEC responses are cached under `data/raw/` and intentionally ignored by Git.

## Tests

```bash
pytest -q
```

## Production coverage policy

Strategy-aware readiness is intentionally narrower than full historical completeness:

- quarterly: recent 8 fiscal quarters, with at least 2 evaluable EPS YoY periods and 2 usable revenue YoY periods;
- annual: 5 FY target, 3 FY fallback;
- legacy gaps outside this window do not automatically fail production coverage;
- current/prior negative EPS bases are evaluable data states even when percentage YoY is intentionally undefined;
- unsupported FPI/ADR filing families such as 20-F/6-K remain explicit `UNSUPPORTED_FPI` until separately implemented.

## Scope boundary

This repository produces research-grade point-in-time fundamental data. It does **not** modify Supabase, the production screener, or Core TrendFoll at this stage.
