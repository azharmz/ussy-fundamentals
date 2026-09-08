from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


DEFAULT_WIDE = Path("data/processed/fundamentals_point_in_time.parquet")
DEFAULT_LONG = Path("data/processed/fundamentals_point_in_time_long.parquet")
POLICY_REASONS = {"Q4_EXCLUDED_POLICY"}


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


def _reason_column(metric: str) -> str:
    return f"{metric}_missing_reason"


def _print_reason_breakdown(wide: pd.DataFrame, metric: str) -> None:
    reason_col = _reason_column(metric)
    if metric not in wide.columns or reason_col not in wide.columns:
        return
    missing = wide[wide[metric].isna()]
    if missing.empty:
        print(f"{metric:26s} no missing values")
        return
    counts = missing[reason_col].fillna("UNCLASSIFIED").value_counts()
    parts = ", ".join(f"{k}={v:,}" for k, v in counts.items())
    print(f"{metric:26s} {parts}")


def _cross_cik_overlaps(long: pd.DataFrame) -> pd.DataFrame:
    required = {"symbol", "fiscal_period_end", "metric", "cik"}
    if not required.issubset(long.columns):
        return pd.DataFrame()
    x = long.copy()
    x["cik"] = x["cik"].astype(str).str.zfill(10)
    keys = ["symbol", "fiscal_period_end", "metric"]
    multi = x.groupby(keys, dropna=False)["cik"].nunique()
    multi = multi[multi > 1]
    if multi.empty:
        return pd.DataFrame()
    hit_keys = multi.reset_index()[keys]
    out = x.merge(hit_keys, on=keys, how="inner")
    show = [
        c for c in [
            "symbol", "fiscal_period_end", "metric", "cik", "accepted_at", "form",
            "fp", "value", "accession", "period_type",
        ] if c in out.columns
    ]
    return out[show].sort_values(["symbol", "fiscal_period_end", "metric", "accepted_at"])


def _amendment_preservation_issues(long: pd.DataFrame) -> pd.DataFrame:
    """Return amendment observations that have no preserved earlier original observation.

    This is intentionally conservative: an amendment may introduce a fact that was not
    present in the original filing. Such rows are surfaced for review rather than silently
    treated as proof of overwrite.
    """
    required = {
        "symbol", "fiscal_period_end", "metric", "accepted_at", "accession", "is_amendment"
    }
    if not required.issubset(long.columns):
        return pd.DataFrame()

    x = long.copy()
    x["accepted_at"] = pd.to_datetime(x["accepted_at"], errors="coerce", utc=True)
    x["is_amendment"] = x["is_amendment"].fillna(False).astype(bool)
    amendments = x[x["is_amendment"]].copy()
    if amendments.empty:
        return pd.DataFrame()

    issues = []
    for _, row in amendments.iterrows():
        prior = x[
            (x["symbol"] == row["symbol"])
            & (x["metric"] == row["metric"])
            & (x["fiscal_period_end"] == row["fiscal_period_end"])
            & (~x["is_amendment"])
            & (x["accession"] != row["accession"])
            & (x["accepted_at"] < row["accepted_at"])
        ]
        if prior.empty:
            issues.append(row)

    if not issues:
        return pd.DataFrame()
    out = pd.DataFrame(issues)
    show = [
        c for c in [
            "symbol", "fiscal_period_end", "metric", "accepted_at", "form", "accession",
            "tag", "unit", "value", "period_type",
        ] if c in out.columns
    ]
    return out[show].sort_values(["symbol", "fiscal_period_end", "metric", "accepted_at"])


def _symbol_quarter_detail(wide: pd.DataFrame, long: pd.DataFrame, symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    s = symbol.upper()
    w = wide[wide["symbol"].astype(str).str.upper() == s].copy()
    l = long[long["symbol"].astype(str).str.upper() == s].copy()

    detail_cols = [
        c for c in [
            "fiscal_period_end", "accepted_at", "form", "fp", "quarterly_eps",
            "quarterly_eps_missing_reason", "quarterly_eps_yoy",
            "quarterly_eps_yoy_missing_reason", "quarterly_revenue",
            "quarterly_revenue_missing_reason", "quarterly_revenue_yoy",
            "quarterly_revenue_yoy_missing_reason", "accession", "cik",
        ] if c in w.columns
    ]
    if not w.empty:
        w = w[detail_cols].sort_values(["fiscal_period_end", "accepted_at"])

    q4_cols = [
        c for c in [
            "fiscal_period_end", "accepted_at", "metric", "period_type", "tag", "unit",
            "value", "yoy", "yoy_source", "accession", "cik",
        ] if c in l.columns
    ]
    if not l.empty and "fp" in l.columns:
        l = l[l["fp"].astype(str).eq("Q4")]
    if not l.empty:
        l = l[q4_cols].sort_values(["fiscal_period_end", "metric", "accepted_at"])
    return w, l


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit SEC PIT fundamentals coverage and anomalies")
    parser.add_argument("--wide", type=Path, default=DEFAULT_WIDE)
    parser.add_argument("--long", type=Path, default=DEFAULT_LONG)
    parser.add_argument("--outlier", type=float, default=3.0, help="Absolute growth threshold, 3.0 = 300%%")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument(
        "--detail-symbol",
        action="append",
        default=[],
        help="Print quarter-level coverage and Q4 provenance for a symbol; may be repeated",
    )
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
        if c not in wide.columns:
            continue
        available = wide[c].notna().mean()
        reason_col = _reason_column(c)
        policy = 0.0
        if reason_col in wide.columns:
            policy = wide[reason_col].isin(POLICY_REASONS).mean()
        unexplained = max(0.0, 1.0 - available - policy)
        print(
            f"{c:26s} {_pct(available)} available | "
            f"{_pct(policy)} policy-excluded | {_pct(unexplained)} other-missing"
        )
    print()

    print("=== MISSING REASONS ===")
    for c in metrics:
        _print_reason_breakdown(wide, c)
    print()

    print("=== TOP SYMBOLS WITH MISSING DATA ===")
    for c in metrics:
        _print_missing_by_symbol(wide, c, args.top)
    print()

    print("=== Q4 PROVENANCE ===")
    if "period_type" in long.columns:
        q4 = long[long["period_type"].isin(["quarterly_direct_q4", "quarterly_derived_q4"])]
        if q4.empty:
            print("No Q4 observations")
        else:
            print(q4.groupby(["metric", "period_type"]).size().to_string())
    else:
        print("period_type not present in long parquet")
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

    print("=== CIK BOUNDARY AUDIT ===")
    overlaps = _cross_cik_overlaps(long)
    if overlaps.empty:
        print("Cross-CIK symbol/fiscal_period_end/metric overlaps: 0")
    else:
        keys = ["symbol", "fiscal_period_end", "metric"]
        overlap_groups = overlaps[keys].drop_duplicates()
        print(f"Cross-CIK symbol/fiscal_period_end/metric overlaps: {len(overlap_groups):,}")
        print(overlaps.head(max(args.top, 20)).to_string(index=False))
    print()

    print("=== AMENDMENT PRESERVATION ===")
    amendment_count = 0
    if "is_amendment" in long.columns:
        amendment_count = int(long["is_amendment"].fillna(False).astype(bool).sum())
    amendment_issues = _amendment_preservation_issues(long)
    print(f"Amendment observations: {amendment_count:,}")
    print(f"Amendments without preserved prior original: {len(amendment_issues):,}")
    if not amendment_issues.empty:
        print(amendment_issues.head(max(args.top, 20)).to_string(index=False))
    print()

    print("=== YOY SOURCE ===")
    if "yoy_source" in long.columns:
        yoy_rows = long[long["yoy"].notna()].copy()
        yoy_src = yoy_rows["yoy_source"].value_counts(dropna=False)
        print(yoy_src.to_string())
        if "metric" in yoy_rows.columns and not yoy_rows.empty:
            print("\nBy metric:")
            print(yoy_rows.groupby(["metric", "yoy_source"]).size().to_string())
    else:
        print("yoy_source not present")
    print()

    print("=== GROWTH OUTLIERS ===")
    outlier_frames = []
    for c in ["quarterly_eps_yoy", "quarterly_revenue_yoy", "annual_eps_growth"]:
        if c not in wide.columns:
            continue
        values = pd.to_numeric(wide[c], errors="coerce")
        o = wide[values.abs() >= args.outlier].copy()
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

        if "yoy_source" in long.columns:
            q_outliers = outliers[outliers["metric"].isin(["quarterly_eps_yoy", "quarterly_revenue_yoy"])].copy()
            if not q_outliers.empty:
                source_map = long[["symbol", "fiscal_period_end", "metric", "yoy", "yoy_source"]].copy()
                source_map["metric"] = source_map["metric"].map({"eps": "quarterly_eps_yoy", "revenue": "quarterly_revenue_yoy"})
                merged = q_outliers.merge(source_map, on=["symbol", "fiscal_period_end", "metric"], how="left")
                print("\nQuarterly outliers by comparator source:")
                print(merged["yoy_source"].fillna("UNKNOWN").value_counts().to_string())
    print()

    for symbol in args.detail_symbol:
        detail, q4_detail = _symbol_quarter_detail(wide, long, symbol)
        print(f"=== SYMBOL DETAIL {symbol.upper()} ===")
        if detail.empty:
            print("No wide observations")
        else:
            print(detail.to_string(index=False))
        print(f"\n--- {symbol.upper()} Q4 PROVENANCE ---")
        if q4_detail.empty:
            print("No Q4 observations")
        else:
            print(q4_detail.to_string(index=False))
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

    overlap_count = 0 if overlaps.empty else len(overlaps[["symbol", "fiscal_period_end", "metric"]].drop_duplicates())
    flags.append(("cross_cik_period_metric_overlap", overlap_count))
    flags.append(("amendment_without_prior_original", len(amendment_issues)))

    for name, count in flags:
        status = "PASS" if count == 0 else "FAIL"
        print(f"{status:4s} {name:30s} {count:,}")


if __name__ == "__main__":
    main()
