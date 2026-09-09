from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .sec_client import SecClient, submissions


DEFAULT_READINESS = Path("data/processed/fundamentals_readiness_report.csv")
DEFAULT_OUTPUT = Path("data/processed/annual_lt3_verification.csv")
DEFAULT_SUMMARY = Path("data/processed/annual_lt3_verification_summary.json")
DEFAULT_CACHE = Path("data/raw/annual_history_verification")

DOMESTIC_ANNUAL_FORMS = {"10-K", "10-K/A"}
FPI_ANNUAL_FORMS = {"20-F", "20-F/A", "40-F", "40-F/A"}


def _normalize_cik(value: object) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(10) if digits else None


def _annual_periods(rows: list[dict], forms: set[str]) -> list[pd.Timestamp]:
    periods: set[pd.Timestamp] = set()
    for row in rows:
        form = str(row.get("form", "")).upper()
        if form not in forms:
            continue
        value = row.get("reportDate") or row.get("filingDate")
        period = pd.to_datetime(value, errors="coerce")
        if pd.notna(period):
            periods.add(pd.Timestamp(period).normalize())
    return sorted(periods)


def classify_annual_history(
    usable_annual_years: int,
    domestic_periods: list[pd.Timestamp],
    fpi_periods: list[pd.Timestamp],
) -> str:
    """Explain why production coverage has fewer than three usable annual EPS years.

    This classifier deliberately separates true short current-CIK history from cases
    where the SEC already has enough annual filings but the domestic EPS normalizer did
    not produce three usable annual states. Older 20-F/40-F history is classified as an
    issuer-regime transition rather than silently treated as a tag failure.
    """
    domestic_count = len(domestic_periods)
    total_count = len(set(domestic_periods) | set(fpi_periods))

    if usable_annual_years >= 3:
        return "NOT_ANNUAL_LT3"
    if domestic_count >= 3:
        return "DOMESTIC_HISTORY_EXTRACTION_GAP"
    if total_count >= 3 and fpi_periods:
        return "ISSUER_REGIME_TRANSITION"
    if total_count < 3:
        return "SHORT_CURRENT_CIK_HISTORY"
    return "OTHER_ANNUAL_HISTORY_GAP"


def _contains_annual_lt3(value: object) -> bool:
    if pd.isna(value):
        return False
    return "ANNUAL_LT_3Y" in {part for part in str(value).split(";") if part}


def verify_candidates(
    readiness: pd.DataFrame,
    client: SecClient,
    cache_dir: Path = DEFAULT_CACHE,
) -> tuple[pd.DataFrame, dict]:
    candidates = readiness[
        readiness["failure_class"].apply(_contains_annual_lt3)
        & readiness["production_status"].eq("FAIL_PRODUCTION_COVERAGE")
    ].copy()

    rows: list[dict] = []
    for row in candidates.itertuples(index=False):
        symbol = str(getattr(row, "symbol")).upper()
        cik = _normalize_cik(getattr(row, "cik", None))
        usable = int(getattr(row, "annual_years", 0) or 0)

        if not cik:
            rows.append(
                {
                    "symbol": symbol,
                    "cik": pd.NA,
                    "usable_annual_years": usable,
                    "verification_class": "CIK_MISSING_FOR_VERIFICATION",
                    "domestic_annual_filings": 0,
                    "fpi_annual_filings": 0,
                    "all_annual_filings": 0,
                    "first_domestic_annual_period": pd.NaT,
                    "latest_domestic_annual_period": pd.NaT,
                    "first_fpi_annual_period": pd.NaT,
                    "latest_fpi_annual_period": pd.NaT,
                }
            )
            continue

        filing_rows = submissions(client, cik, cache_dir)
        domestic = _annual_periods(filing_rows, DOMESTIC_ANNUAL_FORMS)
        fpi = _annual_periods(filing_rows, FPI_ANNUAL_FORMS)
        all_periods = sorted(set(domestic) | set(fpi))
        verification_class = classify_annual_history(usable, domestic, fpi)

        rows.append(
            {
                "symbol": symbol,
                "cik": cik,
                "usable_annual_years": usable,
                "verification_class": verification_class,
                "domestic_annual_filings": len(domestic),
                "fpi_annual_filings": len(fpi),
                "all_annual_filings": len(all_periods),
                "first_domestic_annual_period": domestic[0] if domestic else pd.NaT,
                "latest_domestic_annual_period": domestic[-1] if domestic else pd.NaT,
                "first_fpi_annual_period": fpi[0] if fpi else pd.NaT,
                "latest_fpi_annual_period": fpi[-1] if fpi else pd.NaT,
            }
        )

    detail = pd.DataFrame(rows)
    counts = detail["verification_class"].value_counts().to_dict() if not detail.empty else {}
    summary = {
        "candidate_symbols": int(len(detail)),
        "verification_class_counts": {str(k): int(v) for k, v in counts.items()},
        "interpretation": {
            "DOMESTIC_HISTORY_EXTRACTION_GAP": (
                "At least three 10-K annual periods exist under the current CIK, but fewer than three "
                "usable annual EPS states reached production coverage. Diagnose EPS tag/normalization, not issuer age."
            ),
            "ISSUER_REGIME_TRANSITION": (
                "The current CIK has enough annual history only when older 20-F/40-F filings are included. "
                "Treat as an issuer-regime support decision, not a generic domestic tag failure."
            ),
            "SHORT_CURRENT_CIK_HISTORY": (
                "Fewer than three annual reports exist under the current CIK. This is consistent with a recent IPO, "
                "spin-off, reorganization, or predecessor-CIK transition and should not be patched as an EPS tag bug "
                "without separate predecessor evidence."
            ),
        },
    }
    return detail.sort_values(["verification_class", "symbol"]) if not detail.empty else detail, summary


def write_verification(
    readiness_path: Path = DEFAULT_READINESS,
    output_path: Path = DEFAULT_OUTPUT,
    summary_path: Path = DEFAULT_SUMMARY,
    cache_dir: Path = DEFAULT_CACHE,
) -> tuple[Path, Path, pd.DataFrame, dict]:
    readiness = pd.read_csv(readiness_path)
    client = SecClient()
    detail, summary = verify_candidates(readiness, client, cache_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    detail.to_csv(output_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return output_path, summary_path, detail, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify ANNUAL_LT_3Y failures against SEC annual filing history")
    parser.add_argument("--readiness", type=Path, default=DEFAULT_READINESS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args()

    output, summary_path, _, summary = write_verification(
        readiness_path=args.readiness,
        output_path=args.output,
        summary_path=args.summary,
        cache_dir=args.cache_dir,
    )
    print("=== ANNUAL <3Y HISTORY VERIFICATION ===")
    print(f"Candidates: {summary['candidate_symbols']:,}")
    for key, value in summary["verification_class_counts"].items():
        print(f"  {key}: {value:,}")
    print(f"Wrote {output}")
    print(f"Wrote {summary_path}")


if __name__ == "__main__":
    main()
