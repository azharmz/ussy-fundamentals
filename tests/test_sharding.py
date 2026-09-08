from pathlib import Path

import pandas as pd

from ussy_fundamentals.merge_shards import merge_shard_outputs
from ussy_fundamentals.shard_universe import shard_universe


def test_shard_universe_partitions_every_row_once():
    universe = pd.DataFrame({"symbol": [f"S{i}" for i in range(11)]})
    shards = [shard_universe(universe, i, 4) for i in range(4)]
    combined = pd.concat(shards, ignore_index=True)

    assert sorted(combined["symbol"].tolist()) == sorted(universe["symbol"].tolist())
    assert combined["symbol"].is_unique
    assert [len(s) for s in shards] == [3, 3, 3, 2]


def test_merge_shards_reconciles_manifest_to_expected_universe(tmp_path: Path):
    root = tmp_path / "shards"
    expected = tmp_path / "current_universe.csv"
    pd.DataFrame({"symbol": ["AAA", "BBB"]}).to_csv(expected, index=False)

    for i, symbol in enumerate(["AAA", "BBB"]):
        out = root / f"fundamentals-shard-{i}" / "data" / "processed"
        out.mkdir(parents=True)
        pd.DataFrame(
            {
                "symbol": [symbol],
                "metric": ["eps"],
                "accepted_at": [pd.Timestamp("2026-01-01", tz="UTC")],
                "fiscal_period_end": [pd.Timestamp("2025-12-31")],
                "value": [1.0 + i],
                "accession": [f"acc-{i}"],
            }
        ).to_parquet(out / "fundamentals_point_in_time_long.parquet", index=False)
        pd.DataFrame(
            {
                "symbol": [symbol],
                "fiscal_period_end": [pd.Timestamp("2025-12-31")],
                "accepted_at": [pd.Timestamp("2026-01-01", tz="UTC")],
                "accession": [f"acc-{i}"],
                "quarterly_eps": [1.0 + i],
            }
        ).to_parquet(out / "fundamentals_point_in_time.parquet", index=False)
        pd.DataFrame(
            {
                "symbol": [symbol],
                "cik": [f"{i+1:010d}"],
                "status": ["NORMALIZED"],
                "normalized_rows": [1],
                "forms_detected": [""],
            }
        ).to_parquet(out / "fundamentals_run_manifest.parquet", index=False)

    long_df, wide_df, manifest = merge_shard_outputs(root, expected)

    assert set(manifest["symbol"]) == {"AAA", "BBB"}
    assert set(wide_df["symbol"]) == {"AAA", "BBB"}
    assert set(long_df["symbol"]) == {"AAA", "BBB"}
