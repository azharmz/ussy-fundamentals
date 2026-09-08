from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .production_coverage import can_slim_production_coverage
from .reconcile import combine_production_readiness


DEFAULT_WIDE = Path("data/processed/fundamentals_point_in_time.parquet")
DEFAULT_MANIFEST = Path("data/processed/fundamentals_run_manifest.parquet")
DEFAULT_DETAIL_CSV = Path("data/processed/fundamentals_readiness_report.csv")
DEFAULT_SUMMARY_JSON = Path("data/processed/fundamentals_readiness_summary.json")


def build_reports(wide: pd.DataFrame, manifest: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    coverage = can_slim_production_coverage(wide)
    combined = combine_production_readiness(manifest, coverage)

    if combined.empty:
        summary = {
            "requested_symbols": 0,
            "ready_symbols": 0,
            "non_ready_symbols": 0,
            "production_status_counts": {},
            "failure_class_counts": {},
        }
        return combined, summary

    ready_mask = combined["production_status"].isin(["PASS_FULL", "PASS_3Y_FALLBACK"])
    status_counts = combined["production_status"].fillna("UNCLASSIFIED").value_counts().to_dict()

    failure_series = combined.loc[~ready_mask, "failure_class"].dropna().astype(str)
    failure_counts: dict[str, int] = {}
    for value in failure_series:
        for failure in [part for part in value.split(";") if part]:
            failure_counts[failure] = failure_counts.get(failure, 0) + 1

    summary = {
        "requested_symbols": int(len(combined)),
        "ready_symbols": int(ready_mask.sum()),
        "non_ready_symbols": int((~ready_mask).sum()),
        "production_status_counts": {str(k): int(v) for k, v in status_counts.items()},
        "failure_class_counts": dict(sorted(failure_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
    }

    preferred = [
        "symbol",
        "cik",
        "production_status",
        "failure_class",
        "normalized_rows",
        "forms_detected",
        "quarter_periods_present",
        "quarterly_eps_yoy_usable",
        "quarterly_eps_yoy_nonpositive_base",
        "quarterly_eps_yoy_evaluable",
        "quarterly_eps_yoy_stale_quarters",
        "quarterly_revenue_yoy_usable",
        "quarterly_revenue_yoy_stale_quarters",
        "annual_years",
    ]
    ordered = [c for c in preferred if c in combined.columns]
    ordered += [c for c in combined.columns if c not in ordered]
    return combined[ordered].sort_values(["production_status", "symbol"]), summary


def write_reports(
    wide_path: Path = DEFAULT_WIDE,
    manifest_path: Path = DEFAULT_MANIFEST,
    detail_csv: Path = DEFAULT_DETAIL_CSV,
    summary_json: Path = DEFAULT_SUMMARY_JSON,
) -> tuple[Path, Path, pd.DataFrame, dict]:
    wide = pd.read_parquet(wide_path)
    manifest = pd.read_parquet(manifest_path)
    detail, summary = build_reports(wide, manifest)

    detail_csv.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    detail.to_csv(detail_csv, index=False)
    summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return detail_csv, summary_json, detail, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Export consolidated CAN SLIM readiness reports")
    parser.add_argument("--wide", type=Path, default=DEFAULT_WIDE)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--detail-csv", type=Path, default=DEFAULT_DETAIL_CSV)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    args = parser.parse_args()

    detail_csv, summary_json, _, summary = write_reports(
        wide_path=args.wide,
        manifest_path=args.manifest,
        detail_csv=args.detail_csv,
        summary_json=args.summary_json,
    )

    print("=== CONSOLIDATED READINESS REPORT ===")
    print(f"Requested symbols: {summary['requested_symbols']:,}")
    print(f"Ready symbols: {summary['ready_symbols']:,}")
    print(f"Non-ready symbols: {summary['non_ready_symbols']:,}")
    print("Production status counts:")
    for key, value in summary["production_status_counts"].items():
        print(f"  {key}: {value:,}")
    if summary["failure_class_counts"]:
        print("Failure class counts:")
        for key, value in summary["failure_class_counts"].items():
            print(f"  {key}: {value:,}")
    print(f"Wrote {detail_csv}")
    print(f"Wrote {summary_json}")


if __name__ == "__main__":
    main()
