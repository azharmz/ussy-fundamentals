from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

READY = {"PASS_FULL", "PASS_3Y_FALLBACK"}

DEFAULT_READINESS = Path("data/processed/fundamentals_readiness_report.csv")
DEFAULT_TAXONOMY = Path("data/processed/fundamentals_exception_taxonomy.csv")
DEFAULT_DETAIL = Path("data/processed/fundamentals_final_production_report.csv")
DEFAULT_SUMMARY = Path("data/processed/fundamentals_final_production_summary.json")


def _bucket(row: pd.Series) -> str:
    status = str(row.get("production_status", ""))
    exception_class = str(row.get("exception_class", ""))
    failure = str(row.get("failure_class", ""))

    if status in READY:
        return "READY"
    if status == "UNSUPPORTED_FPI":
        return "UNSUPPORTED_FPI"
    if status == "CIK_NOT_FOUND":
        return "UNRESOLVED_CIK"
    if status in {"NO_SEC_FACTS", "NO_SUPPORTED_FILINGS", "NORMALIZATION_EMPTY"}:
        return "STRUCTURALLY_UNAVAILABLE_OR_UNSUPPORTED"
    if "ANNUAL_LT_3Y" in failure or exception_class in {
        "ANNUAL_LT_3Y_NEEDS_AGE_VERIFICATION",
        "SHORT_HISTORY_PLUS_QUARTERLY_GAPS",
    }:
        return "INSUFFICIENT_HISTORY"
    if status == "FAIL_PRODUCTION_COVERAGE":
        return "RESIDUAL_PRODUCTION_COVERAGE"
    return "OTHER_NON_READY"


def build_final_report(readiness: pd.DataFrame, taxonomy: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    if readiness.empty:
        return readiness.copy(), {
            "requested_symbols": 0,
            "ready_symbols": 0,
            "ready_pct": 0.0,
            "final_bucket_counts": {},
            "production_status_counts": {},
            "funnel": {},
        }

    extra_cols = [c for c in ["symbol", "exception_family", "exception_class", "needs_diagnosis"] if c in taxonomy.columns]
    detail = readiness.copy()
    if extra_cols and "symbol" in extra_cols:
        extra = taxonomy[extra_cols].drop_duplicates("symbol")
        for c in ["exception_family", "exception_class", "needs_diagnosis"]:
            if c in detail.columns:
                detail = detail.drop(columns=[c])
        detail = detail.merge(extra, on="symbol", how="left")

    detail["final_bucket"] = detail.apply(_bucket, axis=1)

    requested = int(len(detail))
    ready = int(detail["production_status"].isin(READY).sum())
    status_counts = detail["production_status"].fillna("UNCLASSIFIED").value_counts().to_dict()
    bucket_counts = detail["final_bucket"].value_counts().to_dict()

    cik_resolved_mask = ~detail["production_status"].eq("CIK_NOT_FOUND")
    sec_supported_mask = cik_resolved_mask & ~detail["production_status"].isin(["UNSUPPORTED_FPI", "NO_SUPPORTED_FILINGS"])
    normalized_mask = sec_supported_mask & ~detail["production_status"].isin(["NO_SEC_FACTS", "NORMALIZATION_EMPTY"])

    summary = {
        "requested_symbols": requested,
        "ready_symbols": ready,
        "non_ready_symbols": requested - ready,
        "ready_pct": round((100.0 * ready / requested) if requested else 0.0, 2),
        "production_status_counts": {str(k): int(v) for k, v in status_counts.items()},
        "final_bucket_counts": {str(k): int(v) for k, v in bucket_counts.items()},
        "funnel": {
            "requested_universe": requested,
            "cik_resolved_or_not_required": int(cik_resolved_mask.sum()),
            "domestic_sec_supported": int(sec_supported_mask.sum()),
            "normalized_with_sec_facts": int(normalized_mask.sum()),
            "production_ready": ready,
        },
        "policy": {
            "ready_statuses": sorted(READY),
            "consumer_contract": "Select only observations with accepted_at <= as_of_date.",
            "scope": "CAN SLIM strategy-aware production readiness; legacy gaps outside the production window are not blockers.",
            "residual_policy": "Residual zero-base/no-comparator revenue YoY and unsupported issuer regimes are not force-filled.",
        },
    }

    preferred = [
        "symbol", "cik", "production_status", "final_bucket", "failure_class",
        "exception_family", "exception_class", "annual_years",
        "quarterly_eps_yoy_usable", "quarterly_revenue_yoy_usable",
    ]
    ordered = [c for c in preferred if c in detail.columns]
    ordered += [c for c in detail.columns if c not in ordered]
    return detail[ordered].sort_values(["final_bucket", "production_status", "symbol"]), summary


def write_final_report(
    readiness_path: Path = DEFAULT_READINESS,
    taxonomy_path: Path = DEFAULT_TAXONOMY,
    detail_path: Path = DEFAULT_DETAIL,
    summary_path: Path = DEFAULT_SUMMARY,
) -> tuple[Path, Path, pd.DataFrame, dict]:
    readiness = pd.read_csv(readiness_path, dtype={"cik": str})
    taxonomy = pd.read_csv(taxonomy_path, dtype={"cik": str}) if taxonomy_path.exists() else pd.DataFrame()
    detail, summary = build_final_report(readiness, taxonomy)
    detail_path.parent.mkdir(parents=True, exist_ok=True)
    detail.to_csv(detail_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return detail_path, summary_path, detail, summary


def main() -> None:
    p = argparse.ArgumentParser(description="Build final full-universe CAN SLIM production-readiness summary")
    p.add_argument("--readiness", type=Path, default=DEFAULT_READINESS)
    p.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    p.add_argument("--detail", type=Path, default=DEFAULT_DETAIL)
    p.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = p.parse_args()
    detail_path, summary_path, _, summary = write_final_report(args.readiness, args.taxonomy, args.detail, args.summary)

    print("=== FINAL FULL-UNIVERSE PRODUCTION READINESS ===")
    print(f"Requested: {summary['requested_symbols']:,}")
    print(f"Ready: {summary['ready_symbols']:,} ({summary['ready_pct']:.2f}%)")
    print(f"Non-ready: {summary['non_ready_symbols']:,}")
    print("Final buckets:")
    for key, value in summary["final_bucket_counts"].items():
        print(f"  {key}: {value:,}")
    print("Funnel:")
    for key, value in summary["funnel"].items():
        print(f"  {key}: {value:,}")
    print(f"Wrote {detail_path}")
    print(f"Wrote {summary_path}")


if __name__ == "__main__":
    main()
