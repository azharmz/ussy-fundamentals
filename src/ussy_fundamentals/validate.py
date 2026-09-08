from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


DEFAULT_PATH = Path("data/processed/fundamentals_point_in_time.parquet")


def _pct(x):
    if pd.isna(x):
        return "NA"
    return f"{x:.1%}"


def _utc_naive(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce", utc=True)
    return parsed.dt.tz_convert(None)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate USSY SEC PIT fundamentals output")
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--recent", type=int, default=6, help="Rows per symbol to print")
    args = parser.parse_args()

    if not args.path.exists():
        raise SystemExit(f"Missing parquet: {args.path}")

    df = pd.read_parquet(args.path)

    print(f"Rows: {len(df):,}")
    print(f"Symbols: {df['symbol'].nunique() if 'symbol' in df.columns else 0}")
    print(f"Columns: {', '.join(df.columns)}")
    print()

    required = [
        "symbol",
        "accepted_at",
        "filed_at",
        "fiscal_period_end",
        "quarterly_eps",
        "quarterly_revenue",
        "quarterly_eps_yoy",
        "quarterly_revenue_yoy",
        "annual_eps",
        "annual_eps_growth",
        "annual_eps_accepted_at",
        "A_eps_20",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        print("MISSING REQUIRED COLUMNS:", ", ".join(missing))
        raise SystemExit(2)

    print("Null-rate summary:")
    for c in required:
        print(f"  {c:24s} {df[c].isna().mean():6.1%}")

    print()
    print("Basic PIT checks:")
    accepted = _utc_naive(df["accepted_at"])
    filed = _utc_naive(df["filed_at"])
    period_end = _utc_naive(df["fiscal_period_end"])
    annual_accepted = _utc_naive(df["annual_eps_accepted_at"])

    print(f"  accepted_at < fiscal_period_end : {(accepted < period_end).sum():,}")
    print(f"  filed_at < fiscal_period_end    : {(filed < period_end).sum():,}")
    print(f"  annual accepted > row accepted : {(annual_accepted > accepted).sum():,}")
    print(f"  duplicate full rows             : {df.duplicated().sum():,}")

    print()
    symbols = sorted(df["symbol"].dropna().unique())
    for symbol in symbols:
        s = df[df["symbol"] == symbol].copy()
        s = s.sort_values(["accepted_at", "fiscal_period_end"]).tail(args.recent)

        print(f"=== {symbol} latest {len(s)} rows ===")
        cols = [
            "fiscal_period_end",
            "accepted_at",
            "form",
            "quarterly_eps",
            "quarterly_eps_yoy",
            "quarterly_revenue",
            "quarterly_revenue_yoy",
            "annual_eps",
            "annual_eps_growth",
            "A_eps_20",
        ]
        existing = [c for c in cols if c in s.columns]
        view = s[existing].copy()

        for c in ["quarterly_eps_yoy", "quarterly_revenue_yoy", "annual_eps_growth"]:
            if c in view:
                view[c] = view[c].map(_pct)

        print(view.to_string(index=False))
        print()


if __name__ == "__main__":
    main()
