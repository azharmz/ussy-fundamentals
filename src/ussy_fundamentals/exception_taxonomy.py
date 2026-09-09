from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


DEFAULT_READINESS = Path("data/processed/fundamentals_readiness_report.csv")
DEFAULT_DETAIL = Path("data/processed/fundamentals_exception_taxonomy.csv")
DEFAULT_SUMMARY = Path("data/processed/fundamentals_exception_taxonomy_summary.json")

READY = {"PASS_FULL", "PASS_3Y_FALLBACK"}


def _split_failures(value: object) -> set[str]:
    if pd.isna(value):
        return set()
    return {part for part in str(value).split(";") if part}


def _fpi_subtype(forms: object) -> str:
    form_set = {part.strip().upper() for part in str(forms or "").split(",") if part.strip()}
    if "40-F" in form_set or "40-F/A" in form_set:
        return "FPI_CANADIAN_40F"
    if "20-F" in form_set or "20-F/A" in form_set:
        if "6-K" in form_set or "6-K/A" in form_set:
            return "FPI_20F_6K"
        return "FPI_20F_ONLY"
    if "6-K" in form_set or "6-K/A" in form_set:
        return "FPI_6K_ONLY"
    return "FPI_OTHER"


def classify_exception(row: pd.Series) -> tuple[str, str, bool]:
    status = str(row.get("production_status", ""))
    failures = _split_failures(row.get("failure_class"))

    if status in READY:
        return "READY", "NONE", False
    if status == "UNSUPPORTED_FPI":
        return "ISSUER_REGIME", _fpi_subtype(row.get("forms_detected")), False
    if status == "CIK_NOT_FOUND":
        return "IDENTITY_RESOLUTION", "CIK_NOT_FOUND", True
    if status == "NO_SEC_FACTS":
        return "SEC_ACQUISITION", "NO_SEC_FACTS", True
    if status == "NORMALIZATION_EMPTY":
        return "NORMALIZATION", "NORMALIZATION_EMPTY", True
    if status == "NO_SUPPORTED_FILINGS":
        return "ISSUER_REGIME", "NO_SUPPORTED_FILINGS", True

    if status == "FAIL_PRODUCTION_COVERAGE":
        annual_short = "ANNUAL_LT_3Y" in failures
        eps_insufficient = "QUARTERLY_EPS_YOY_INSUFFICIENT" in failures
        revenue_insufficient = "QUARTERLY_REVENUE_YOY_INSUFFICIENT" in failures
        revenue_stale = "QUARTERLY_REVENUE_YOY_STALE" in failures

        if revenue_stale and len(failures) == 1:
            return "PRODUCTION_COVERAGE", "REVENUE_YOY_STALE", True
        if annual_short and (eps_insufficient or revenue_insufficient):
            return "PRODUCTION_COVERAGE", "SHORT_HISTORY_PLUS_QUARTERLY_GAPS", True
        if annual_short:
            # This is a candidate short-history issuer class. The readiness report alone
            # cannot prove that the issuer is young versus a legacy extraction gap.
            return "PRODUCTION_COVERAGE", "ANNUAL_LT_3Y_NEEDS_AGE_VERIFICATION", True
        if eps_insufficient and revenue_insufficient:
            return "PRODUCTION_COVERAGE", "QUARTERLY_DUAL_YOY_GAP", True
        if revenue_insufficient:
            return "PRODUCTION_COVERAGE", "REVENUE_YOY_COVERAGE_GAP", True
        if eps_insufficient:
            return "PRODUCTION_COVERAGE", "EPS_YOY_COVERAGE_GAP", True
        return "PRODUCTION_COVERAGE", "OTHER_PRODUCTION_COVERAGE", True

    return "UNCLASSIFIED", status or "UNCLASSIFIED", True


def build_taxonomy(readiness: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    detail = readiness.copy()
    classified = detail.apply(classify_exception, axis=1, result_type="expand")
    classified.columns = ["exception_family", "exception_class", "needs_diagnosis"]
    detail = pd.concat([detail, classified], axis=1)

    non_ready = detail[~detail["production_status"].isin(READY)]
    family_counts = non_ready["exception_family"].value_counts().to_dict()
    class_counts = non_ready["exception_class"].value_counts().to_dict()

    fpi = detail[detail["production_status"].eq("UNSUPPORTED_FPI")]
    fpi_counts = fpi["exception_class"].value_counts().to_dict()

    summary = {
        "requested_symbols": int(len(detail)),
        "ready_symbols": int(detail["production_status"].isin(READY).sum()),
        "non_ready_symbols": int((~detail["production_status"].isin(READY)).sum()),
        "exception_family_counts": {str(k): int(v) for k, v in family_counts.items()},
        "exception_class_counts": {str(k): int(v) for k, v in class_counts.items()},
        "fpi_subtype_counts": {str(k): int(v) for k, v in fpi_counts.items()},
        "diagnosis_queue_symbols": int(non_ready["needs_diagnosis"].sum()),
        "notes": {
            "ANNUAL_LT_3Y_NEEDS_AGE_VERIFICATION": (
                "The readiness report proves fewer than 3 usable annual years, but not whether "
                "that is true issuer age/spin-off history or an extraction/history gap. Diagnose "
                "this class by representative samples, not ticker-by-ticker."
            ),
            "UNSUPPORTED_FPI": (
                "FPI is an explicit issuer-regime class. Do not force 20-F/6-K/40-F issuers through "
                "the domestic 10-Q/10-K normalizer without a separate supported design."
            ),
        },
    }
    return detail.sort_values(["exception_family", "exception_class", "symbol"]), summary


def write_taxonomy(
    readiness_path: Path = DEFAULT_READINESS,
    detail_path: Path = DEFAULT_DETAIL,
    summary_path: Path = DEFAULT_SUMMARY,
) -> tuple[Path, Path, pd.DataFrame, dict]:
    readiness = pd.read_csv(readiness_path)
    detail, summary = build_taxonomy(readiness)
    detail_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    detail.to_csv(detail_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return detail_path, summary_path, detail, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Build batch exception taxonomy from consolidated readiness report")
    parser.add_argument("--readiness", type=Path, default=DEFAULT_READINESS)
    parser.add_argument("--detail", type=Path, default=DEFAULT_DETAIL)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()

    detail_path, summary_path, _, summary = write_taxonomy(args.readiness, args.detail, args.summary)
    print("=== BATCH EXCEPTION TAXONOMY ===")
    print(f"Requested symbols: {summary['requested_symbols']:,}")
    print(f"Ready symbols: {summary['ready_symbols']:,}")
    print(f"Non-ready symbols: {summary['non_ready_symbols']:,}")
    print("Exception classes:")
    for key, value in summary["exception_class_counts"].items():
        print(f"  {key}: {value:,}")
    print(f"Wrote {detail_path}")
    print(f"Wrote {summary_path}")


if __name__ == "__main__":
    main()
