from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd

from . import normalize as normalize_mod
from .normalize import accession_index, classify_period, fact_rows, normalize_company
from .pipeline import _drop_nonadditive_derived_eps, _load_cik_history, _symbol_ciks
from .sec_client import SecClient, companyfacts, submissions, ticker_mapping


def _candidate_reason(row: pd.Series, metric: str) -> str:
    if row.get("form") not in {"10-Q", "10-Q/A", "10-K", "10-K/A"}:
        return "REJECT_FORM"

    period_type = row.get("period_type")
    if metric in {"eps", "revenue"}:
        if row.get("form") in {"10-Q", "10-Q/A"}:
            if period_type not in {"quarterly", "half_year", "nine_month"}:
                return "REJECT_DURATION"
        elif row.get("form") in {"10-K", "10-K/A"}:
            if period_type != "annual":
                return "REJECT_DURATION"

    report_date = pd.to_datetime(row.get("report_date"), errors="coerce")
    end = pd.to_datetime(row.get("end"), errors="coerce")
    if pd.isna(report_date):
        return "REJECT_NO_REPORT_DATE"
    if pd.isna(end):
        return "REJECT_NO_END"
    if end != report_date:
        return "COMPARATIVE_NOT_OBSERVATION"

    if pd.isna(pd.to_datetime(row.get("accepted_at"), errors="coerce")):
        return "REJECT_NO_ACCEPTED_AT"

    if row.get("form") in {"10-Q", "10-Q/A"} and period_type == "quarterly":
        return "DIRECT_QUARTER_CANDIDATE"
    if row.get("form") in {"10-Q", "10-Q/A"} and period_type in {"half_year", "nine_month"}:
        return "YTD_CANDIDATE"
    if row.get("form") in {"10-K", "10-K/A"} and period_type == "annual":
        return "ANNUAL_CANDIDATE"
    return "OTHER"


def _prepare_tags(metric: str) -> list[str]:
    if metric == "eps":
        tags = list(normalize_mod.EPS_TAGS)
        extra = ["IncomeLossFromContinuingOperationsPerDilutedShare"]
    else:
        tags = list(normalize_mod.REVENUE_TAGS)
        extra = ["OperatingRevenues", "RegulatedAndUnregulatedOperatingRevenue"]
    for tag in extra:
        if tag not in tags:
            tags.append(tag)
    return tags


def _print_normalized_observations(normalized: pd.DataFrame, metric: str) -> None:
    if normalized.empty:
        print("\nNo final pipeline observations.")
        return

    q = normalized[normalized["metric"] == metric].copy()
    if q.empty:
        print(f"\nNo final pipeline {metric} observations.")
        return

    q["fiscal_period_end"] = pd.to_datetime(q["fiscal_period_end"], errors="coerce")
    q = q.sort_values(["fiscal_period_end", "accepted_at"])
    print("\n=== FINAL PIPELINE OBSERVATIONS ===")
    cols = [
        "fiscal_period_end", "accepted_at", "form", "fp", "tag", "unit",
        "period_type", "value", "yoy", "yoy_source", "accession",
    ]
    cols = [c for c in cols if c in q.columns]
    print(q[cols].tail(30).to_string(index=False))


def _print_duration_reject_sample(candidates: pd.DataFrame, limit: int = 15) -> None:
    rejected = candidates[candidates["decision"] == "REJECT_DURATION"].copy()
    if rejected.empty:
        return

    # Prioritize rows whose fact end equals the filing report date. These are the rejects
    # most likely to represent a legitimate current-period stub rather than comparative noise.
    rejected["is_current_period"] = rejected["end"].eq(rejected["report_date"])
    rejected = rejected.sort_values(
        ["is_current_period", "accepted_at", "end"], ascending=[False, False, False]
    )

    print("\n=== REJECT_DURATION SAMPLE ===")
    print("Current-period rejects are shown first; inspect for legitimate stub/corporate-action periods.")
    cols = [
        "cik", "end", "report_date", "accepted_at", "form", "fy", "fp",
        "duration_days", "period_type", "tag", "unit", "value", "accession",
        "is_current_period",
    ]
    cols = [col for col in cols if col in rejected.columns]
    print(rejected[cols].head(limit).to_string(index=False))


def diagnose(symbol: str, metric: str, data_dir: Path) -> None:
    symbol = symbol.upper().strip()
    tags = _prepare_tags(metric)

    client = SecClient()
    mapping = ticker_mapping(client, data_dir / "raw" / "mapping")
    hit = mapping[mapping["symbol"] == symbol]
    if hit.empty:
        raise SystemExit(f"Symbol not found in SEC ticker mapping: {symbol}")

    current_cik = str(hit.iloc[0]["cik"]).zfill(10)
    history = _load_cik_history()
    ciks = _symbol_ciks(symbol, current_cik, history)

    print(f"=== DIAGNOSE {symbol} {metric.upper()} ===")
    print(f"CIKs: {', '.join(ciks)}")
    print(f"Tags: {', '.join(tags)}")

    all_candidates = []
    normalized_parts = []

    for cik in ciks:
        filing_rows = submissions(client, cik, data_dir / "raw" / "submissions")
        facts_json = companyfacts(client, cik, data_dir / "raw" / "companyfacts")
        idx = accession_index(filing_rows)
        candidates = fact_rows(facts_json, tags, metric, idx)
        if not candidates.empty:
            candidates["period_type"] = candidates["duration_days"].map(classify_period)
            candidates["decision"] = candidates.apply(lambda r: _candidate_reason(r, metric), axis=1)
            candidates["cik"] = cik
            all_candidates.append(candidates)

        normalized = normalize_company(symbol, cik, facts_json, filing_rows)
        normalized = _drop_nonadditive_derived_eps(normalized)
        if not normalized.empty:
            normalized_parts.append(normalized)

    if not all_candidates:
        print("No candidate SEC facts found for configured tags.")
        return

    c = pd.concat(all_candidates, ignore_index=True)
    c["end"] = pd.to_datetime(c["end"], errors="coerce")
    c["report_date"] = pd.to_datetime(c["report_date"], errors="coerce")
    c["accepted_at"] = pd.to_datetime(c["accepted_at"], errors="coerce")

    print("\n=== CANDIDATE SUMMARY ===")
    print(c["decision"].value_counts(dropna=False).to_string())

    print("\n=== TAG COVERAGE ===")
    tag_summary = (
        c.groupby(["tag", "unit"], dropna=False)
        .agg(rows=("value", "size"), first_end=("end", "min"), last_end=("end", "max"))
        .sort_values("rows", ascending=False)
    )
    print(tag_summary.to_string())

    print("\n=== RECENT CANDIDATES ===")
    cols = [
        "cik", "end", "report_date", "accepted_at", "form", "fy", "fp", "duration_days",
        "period_type", "tag", "unit", "value", "decision", "accession",
    ]
    print(c.sort_values(["end", "accepted_at"])[cols].tail(60).to_string(index=False))

    _print_duration_reject_sample(c)

    if normalized_parts:
        n = pd.concat(normalized_parts, ignore_index=True)
        _print_normalized_observations(n, metric)

    print("\n=== DIAGNOSTIC HINTS ===")
    direct = (c["decision"] == "DIRECT_QUARTER_CANDIDATE").sum()
    ytd = (c["decision"] == "YTD_CANDIDATE").sum()
    comparative = (c["decision"] == "COMPARATIVE_NOT_OBSERVATION").sum()
    duration_reject = (c["decision"] == "REJECT_DURATION").sum()
    print(f"direct quarter candidates : {direct}")
    print(f"YTD candidates            : {ytd}")
    print(f"comparative-only facts    : {comparative}")
    print(f"duration rejects          : {duration_reject}")
    if metric == "eps" and ytd:
        print("NOTE: YTD EPS candidates are intentionally not arithmetically subtracted into quarterly EPS.")
        print("NOTE: derived Q4 EPS is also excluded from final pipeline observations.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect why SEC facts do or do not enter the PIT normalizer")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--metric", required=True, choices=["eps", "revenue"])
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()
    diagnose(args.symbol, args.metric, Path(args.data_dir))


if __name__ == "__main__":
    main()
