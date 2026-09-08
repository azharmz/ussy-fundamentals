from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd

from .annual_fallback import fill_missing_annual_eps
from .normalize import normalize_company, wide_table
from .sec_client import SecClient, companyfacts, submissions, ticker_mapping


def run(universe_path: Path, data_dir: Path) -> tuple[Path, Path]:
    raw = data_dir / "raw"
    processed = data_dir / "processed"
    mapping_dir = raw / "mapping"
    submissions_dir = raw / "submissions"
    companyfacts_dir = raw / "companyfacts"
    for p in [mapping_dir, submissions_dir, companyfacts_dir, processed]:
        p.mkdir(parents=True, exist_ok=True)

    client = SecClient()
    universe = pd.read_csv(universe_path)
    universe["symbol"] = universe["symbol"].astype(str).str.upper().str.strip()
    universe = universe.merge(ticker_mapping(client, mapping_dir), on="symbol", how="left")

    outputs = []
    for n, row in enumerate(universe.dropna(subset=["cik"]).itertuples(), start=1):
        print(f"[{n}] {row.symbol} CIK={row.cik}")
        filings = submissions(client, row.cik, submissions_dir)
        facts = companyfacts(client, row.cik, companyfacts_dir)
        normalized = normalize_company(row.symbol, row.cik, facts, filings)
        normalized = fill_missing_annual_eps(normalized, facts, filings)
        if not normalized.empty:
            outputs.append(normalized)

    if not outputs:
        raise RuntimeError("No fundamentals were normalized")

    long_df = pd.concat(outputs, ignore_index=True)
    wide_df = wide_table(long_df)

    # Preserve extreme growth values but flag them for audit/research slicing.
    # Numeric coercion is required because PIT growth columns can contain None/pd.NA.
    for col, flag_col in [
        ("quarterly_eps_yoy", "quarterly_eps_yoy_extreme"),
        ("quarterly_revenue_yoy", "quarterly_revenue_yoy_extreme"),
        ("annual_eps_growth", "annual_eps_growth_extreme"),
    ]:
        values = pd.to_numeric(wide_df[col], errors="coerce")
        wide_df[col] = values
        wide_df[flag_col] = values.abs().ge(3.0).fillna(False)

    long_path = processed / "fundamentals_point_in_time_long.parquet"
    wide_path = processed / "fundamentals_point_in_time.parquet"
    long_df.to_parquet(long_path, index=False)
    wide_df.to_parquet(wide_path, index=False)
    return long_path, wide_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", default="data/universe.sample.csv")
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()
    long_path, wide_path = run(Path(args.universe), Path(args.data_dir))
    print(f"Wrote {long_path}")
    print(f"Wrote {wide_path}")


if __name__ == "__main__":
    main()
