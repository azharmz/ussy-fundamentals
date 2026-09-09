from pathlib import Path

import pandas as pd

from ussy_fundamentals.incremental_plan import build_incremental_plan
from ussy_fundamentals.merge_incremental import merge_incremental


class FakeSecClient:
    def get_json(self, url: str):
        if url.endswith("company_tickers.json"):
            return {
                "0": {"ticker": "AAA", "cik_str": 1, "title": "AAA Inc"},
                "1": {"ticker": "BBB", "cik_str": 2, "title": "BBB Inc"},
            }
        if url.endswith("CIK0000000001.json"):
            return {
                "filings": {"recent": {
                    "form": ["10-Q"],
                    "acceptanceDateTime": ["2026-09-01T20:00:00.000Z"],
                    "filingDate": ["2026-09-01"],
                }}
            }
        if url.endswith("CIK0000000002.json"):
            return {
                "filings": {"recent": {
                    "form": ["10-Q"],
                    "acceptanceDateTime": ["2026-09-02T20:00:00.000Z"],
                    "filingDate": ["2026-09-02"],
                }}
            }
        raise AssertionError(url)


def test_incremental_plan_detects_new_filing_and_added_symbol(tmp_path: Path):
    current = pd.DataFrame([
        {"symbol": "AAA", "ticker": "AAA", "security_id": "US1"},
        {"symbol": "BBB", "ticker": "BBB", "security_id": "US2"},
    ])
    baseline_universe = pd.DataFrame([
        {"symbol": "AAA", "ticker": "AAA", "security_id": "US1"},
    ])
    manifest = pd.DataFrame([
        {"symbol": "AAA", "cik": "0000000001", "status": "NORMALIZED"},
    ])
    long_df = pd.DataFrame([
        {"symbol": "AAA", "accepted_at": "2026-08-01T20:00:00Z"},
    ])
    plan, summary = build_incremental_plan(
        current_universe=current,
        baseline_universe=baseline_universe,
        baseline_manifest=manifest,
        baseline_long=long_df,
        client=FakeSecClient(),
        mapping_cache=tmp_path / "mapping",
    )
    assert set(plan["symbol"]) == {"AAA", "BBB"}
    assert summary["added_symbols"] == ["BBB"]
    assert set(summary["new_sec_filing_symbols"]) == {"AAA", "BBB"}
    assert summary["needs_update"] is True


def test_incremental_merge_replaces_impacted_and_removes_departed_symbol(tmp_path: Path):
    baseline = tmp_path / "baseline"
    delta = tmp_path / "delta"
    baseline.mkdir()
    delta.mkdir()

    pd.DataFrame([
        {"symbol": "AAA", "metric": "eps", "accepted_at": "2026-01-01", "fiscal_period_end": "2025-12-31", "value": 1.0, "accession": "old-a"},
        {"symbol": "OLD", "metric": "eps", "accepted_at": "2026-01-01", "fiscal_period_end": "2025-12-31", "value": 2.0, "accession": "old-x"},
    ]).to_parquet(baseline / "fundamentals_point_in_time_long.parquet", index=False)
    pd.DataFrame([
        {"symbol": "AAA", "accepted_at": "2026-01-01", "fiscal_period_end": "2025-12-31", "accession": "old-a"},
        {"symbol": "OLD", "accepted_at": "2026-01-01", "fiscal_period_end": "2025-12-31", "accession": "old-x"},
    ]).to_parquet(baseline / "fundamentals_point_in_time.parquet", index=False)
    pd.DataFrame([
        {"symbol": "AAA", "cik": "1", "status": "NORMALIZED"},
        {"symbol": "OLD", "cik": "9", "status": "NORMALIZED"},
    ]).to_parquet(baseline / "fundamentals_run_manifest.parquet", index=False)

    pd.DataFrame([
        {"symbol": "AAA", "metric": "eps", "accepted_at": "2026-09-01", "fiscal_period_end": "2026-06-30", "value": 3.0, "accession": "new-a"},
        {"symbol": "NEW", "metric": "eps", "accepted_at": "2026-09-02", "fiscal_period_end": "2026-06-30", "value": 4.0, "accession": "new-n"},
    ]).to_parquet(delta / "fundamentals_point_in_time_long.parquet", index=False)
    pd.DataFrame([
        {"symbol": "AAA", "accepted_at": "2026-09-01", "fiscal_period_end": "2026-06-30", "accession": "new-a"},
        {"symbol": "NEW", "accepted_at": "2026-09-02", "fiscal_period_end": "2026-06-30", "accession": "new-n"},
    ]).to_parquet(delta / "fundamentals_point_in_time.parquet", index=False)
    pd.DataFrame([
        {"symbol": "AAA", "cik": "1", "status": "NORMALIZED"},
        {"symbol": "NEW", "cik": "2", "status": "NORMALIZED"},
    ]).to_parquet(delta / "fundamentals_run_manifest.parquet", index=False)

    current = tmp_path / "current.csv"
    pd.DataFrame([{"symbol": "AAA"}, {"symbol": "NEW"}]).to_csv(current, index=False)
    long_df, _, manifest = merge_incremental(
        baseline_dir=baseline,
        delta_dir=delta,
        current_universe_path=current,
    )
    assert set(manifest["symbol"]) == {"AAA", "NEW"}
    assert set(long_df["accession"]) == {"new-a", "new-n"}
