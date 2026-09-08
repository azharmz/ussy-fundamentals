from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


DEFAULT_WIDE = Path("data/processed/fundamentals_point_in_time.parquet")
DEFAULT_LONG = Path("data/processed/fundamentals_point_in_time_long.parquet")


def _pct(x: float) -> str:
    return f"{x:.1%}"


def _print_missing_by_symbol(df: pd.DataFrame, column: str, topn: int = 15) -> None:
    if column not in df.columns:
        return
    missing = (
        df.groupby("symbol")[column]
        .apply(lambda s: s.isna().mean())
        .sort_values(ascending=False)
    )
    missing = missing[missing > 0].head(topn)
    if missing.empty:
        print(f"  {column}: none")
        return
    print(f"  {column}:")
    for symbol, rate in missing.items():
        print(f"    {symbol:8s} {_pct(rate)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit SEC PIT fundamentals coverage and anomalies")
    parser.add_argument("--wide", type=Path, default=DEFAULT_WIDE)
    parser.add_argument("--long", type=Path, default=DEFAULT_LONG)
    parser.add_argument("--outlier", type=float, default=3.0, help="Absolute growth threshold, 3.0 = 300%%")
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()

    if not args.wide.exists():
        raise SystemExit(f"Missing parquet: {args.wide}")
    if not args.long.exists():
        raise SystemExit(f"Missing parquet: {args.long}")

    wide = pd.read_parquet(args.wide)
    long = pd.read_parquet(args.long)

    print("=== DATASET ===")
    print(f"Rows: {len(wide):,}")
    print(f"Symbols: {wide['symbol'].nunique():,}")
    if "fiscal_period_end" in wide:
        print(f"Fiscal period range: {wide['fiscal_period_end'].min()} -> {wide['fiscal_period_end'].max()}")
    print()

    metrics = [
        "quarterly_eps",
        "quarterly_revenue",
        "quarterly_eps_yoy",
        "quarterly_revenue_yoy",
        "annual_eps",
        "annual_eps_growth",
    ]

    print("=== COVERAGE ===")
    for c in metrics:
        if c in wide.columns:
            nonnull = wide[c].notna().mean()
            print(f"{c:26s} {_pct(nonnull)} available | {_pct(1 - nonnull)} missing")
    print()

    print("=== TOP SYMBOLS WITH MISSING DATA ===")
    for c in ["quarterly_eps", "quarterly_revenue", "quarterly_eps_yoy", "quarterly_revenue_yoy", "annual_eps", "annual_eps_growth"]:
        _print_missing_by_symbol(wide, c, args.top)
    print()

    print("=== DERIVED Q4 ===")
    if "period_type" in long.columns:
        q4 = long[long["period_type"] == "quarterly_derived_q4"]
        print(f"Derived Q4 fact rows: {len(q4):,}")
        print(f"Symbols with derived Q4: {q4['symbol'].nunique() if not q4.empty else 0:,}")
        if not q4.empty:
            print("By metric:")
            print(q4["metric"].value_counts().to_string())
    else:
        print("period_type not present in long parquet")
    print()

    print("=== YOY SOURCE ===")
    if "yoy_source" in long.columns:
        yoy_src = long[long["yoy"].notna()]["yoy_source"].value_counts(dropna=False)
        print(yoy_src.to_string())
    else:
        print("yoy_source not present")
    print()

    print("=== GROWTH OUTLIERS ===")
    outlier_frames = []
    for c in ["quarterly_eps_yoy", "quarterly_revenue_yoy", "annual_eps_growth"]:
        if c not in wide.columns:
            continue
        o = wide[wide[c].abs() >= args.outlier].copy()
        if o.empty:
            print(f"{c}: 0")
            continue
        print(f"{c}: {len(o):,}")
        o = o[["symbol", "fiscal_period_end", "accepted_at", c]].copy()
        o["metric"] = c
        o["growth"] = o[c]
        outlier_frames.append(o[["symbol", "fiscal_period_end", "accepted_at", "metric", "growth"]])

    if outlier_frames:
        outliers = pd.concat(outlier_frames, ignore_index=True)
        outliers = outliers.sort_values("growth", key=lambda s: s.abs(), ascending=False)
        print()
        print(f"Top {min(args.top, len(outliers))} absolute outliers:")
        print(outliers.head(args.top).to_string(index=False))
    print()

    print("=== QUALITY FLAGS ===")
    flags = []
    if "accepted_at" in wide and "fiscal_period_end" in wide:
        accepted = pd.to_datetime(wide["accepted_at"], errors="coerce", utc=True).dt.tz_convert(None)
        period_end = pd.to_datetime(wide["fiscal_period_end"], errors="coerce", utc=True).dt.tz_convert(None)
        flags.append(("accepted_before_period_end", int((accepted < period_end).sum())))
    if "annual_eps_accepted_at" in wide:
        annual_accepted = pd.to_datetime(wide["annual_eps_accepted_at"], errors="coerce", utc=True).dt.tz_convert(None)
        row_accepted = pd.to_datetime(wide["accepted_at"], errors="coerce", utc=True).dt.tz_convert(None)
        flags.append(("annual_state_from_future", int((annual_accepted > row_accepted).sum())))
    flags.append(("duplicate_full_rows", int(wide.duplicated().sum())))

    for name, count in flags:
        status = "PASS" if count == 0 else "FAIL"
        print(f"{status:4s} {name:30s} {count:,}")


if __name__ == "__main__":
    main()
