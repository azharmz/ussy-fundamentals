from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .normalize import REVENUE_TAGS, _current_period_only, accession_index, classify_period, fact_rows, normalize_company
from .pipeline import _drop_nonadditive_derived_eps
from .sec_client import SecClient, companyfacts, submissions

FAILURES = {"QUARTERLY_REVENUE_YOY_INSUFFICIENT", "QUARTERLY_REVENUE_YOY_STALE"}


def _split_failures(value) -> set[str]:
    if pd.isna(value):
        return set()
    return {x for x in str(value).split(";") if x}


def _recent_window(df: pd.DataFrame, periods: int = 8) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    col = "end" if "end" in out.columns else "fiscal_period_end"
    out[col] = pd.to_datetime(out[col], errors="coerce")
    ends = sorted(out[col].dropna().unique())
    if len(ends) <= periods:
        return out
    return out[out[col].isin(set(ends[-periods:]))].copy()


def _latest_supported_report(filing_rows: list[dict]) -> pd.Timestamp:
    values = []
    for row in filing_rows:
        if row.get("form") not in {"10-Q", "10-Q/A", "10-K", "10-K/A"}:
            continue
        dt = pd.to_datetime(row.get("reportDate"), errors="coerce")
        if pd.notna(dt):
            values.append(dt)
    return max(values) if values else pd.NaT


def _revenue_like_tags(payload: dict) -> list[str]:
    """Return plausible monetary revenue/sales concepts outside configured vocabulary."""
    out = []
    configured = set(REVENUE_TAGS)
    excluded = ("costof", "deferred", "contractliability", "remainingperformanceobligation")
    for namespace, concepts in payload.get("facts", {}).items():
        for tag, concept in concepts.items():
            if tag in configured:
                continue
            low = tag.lower()
            if any(x in low for x in excluded):
                continue
            if "revenue" not in low and "sales" not in low:
                continue
            units = concept.get("units", {}) if isinstance(concept, dict) else {}
            monetary = [u for u, entries in units.items() if entries and (u == "USD" or u.endswith("USD"))]
            if monetary:
                out.append(f"{namespace}:{tag}")
    return sorted(out)


def classify_revenue_gap(*, recent_revenue_periods: int, recent_yoy_usable: int,
                         stale_days: int | None, direct_periods: int, ytd_periods: int,
                         annual_periods: int, nonconfigured_tags: int, history_span_days: int | None) -> str:
    if recent_revenue_periods >= 2 and recent_yoy_usable < 2:
        return "REVENUE_PRESENT_YOY_COMPARATOR_GAP"
    if stale_days is not None and stale_days > 180 and recent_revenue_periods >= 1:
        if nonconfigured_tags:
            return "STANDARD_REVENUE_EVIDENCE_STALE_TAG_TRANSITION_CANDIDATE"
        return "STANDARD_REVENUE_EVIDENCE_STALE"
    if recent_revenue_periods <= 4 and history_span_days is not None and history_span_days < 330:
        return "SHORT_REVENUE_HISTORY_EXPECTED"
    if direct_periods < 2 and ytd_periods >= 2:
        return "YTD_RECONSTRUCTION_INPUTS_PRESENT"
    if direct_periods < 2 and annual_periods >= 1:
        return "ANNUAL_Q4_RECONSTRUCTION_CONTEXT_ONLY"
    if nonconfigured_tags:
        return "NONSTANDARD_REVENUE_TAG_CANDIDATE"
    if direct_periods < 1 and ytd_periods < 1 and annual_periods < 1:
        return "NO_USABLE_REVENUE_EVIDENCE"
    return "SPARSE_REVENUE_EVIDENCE"


def diagnose_symbol(symbol: str, cik: str, payload: dict, filing_rows: list[dict]) -> dict:
    idx = accession_index(filing_rows)
    facts = fact_rows(payload, REVENUE_TAGS, "revenue", idx)
    if not facts.empty:
        facts["period_type"] = facts["duration_days"].map(classify_period)

    direct = facts[
        facts["form"].isin(["10-Q", "10-Q/A", "10-K", "10-K/A"])
        & facts["period_type"].eq("quarterly")
    ].copy() if not facts.empty else pd.DataFrame()
    direct = _current_period_only(direct) if not direct.empty else direct

    ytd = facts[
        facts["form"].isin(["10-Q", "10-Q/A"])
        & facts["period_type"].isin(["half_year", "nine_month"])
    ].copy() if not facts.empty else pd.DataFrame()
    ytd = _current_period_only(ytd) if not ytd.empty else ytd

    annual = facts[
        facts["form"].isin(["10-K", "10-K/A"])
        & facts["period_type"].eq("annual")
    ].copy() if not facts.empty else pd.DataFrame()
    annual = _current_period_only(annual) if not annual.empty else annual

    normalized = normalize_company(symbol, cik, payload, filing_rows)
    normalized = _drop_nonadditive_derived_eps(normalized)
    rev = normalized[normalized["metric"].eq("revenue")].copy() if not normalized.empty else pd.DataFrame()
    recent = _recent_window(rev, 8)

    recent_periods = int(recent["fiscal_period_end"].nunique()) if not recent.empty else 0
    yoy_usable = int(pd.to_numeric(recent.get("yoy"), errors="coerce").notna().sum()) if not recent.empty else 0
    direct_recent = _recent_window(direct, 8)
    ytd_recent = _recent_window(ytd, 8)
    annual_recent = _recent_window(annual, 5)
    direct_periods = int(direct_recent["end"].nunique()) if not direct_recent.empty else 0
    ytd_periods = int(ytd_recent["end"].nunique()) if not ytd_recent.empty else 0
    annual_periods = int(annual_recent["end"].nunique()) if not annual_recent.empty else 0

    latest_report = _latest_supported_report(filing_rows)
    latest_rev = pd.to_datetime(rev["fiscal_period_end"], errors="coerce").max() if not rev.empty else pd.NaT
    earliest_rev = pd.to_datetime(rev["fiscal_period_end"], errors="coerce").min() if not rev.empty else pd.NaT
    stale_days = int((latest_report - latest_rev).days) if pd.notna(latest_report) and pd.notna(latest_rev) else None
    span_days = int((latest_rev - earliest_rev).days) if pd.notna(latest_rev) and pd.notna(earliest_rev) else None

    nonconfigured = _revenue_like_tags(payload)
    cls = classify_revenue_gap(
        recent_revenue_periods=recent_periods,
        recent_yoy_usable=yoy_usable,
        stale_days=stale_days,
        direct_periods=direct_periods,
        ytd_periods=ytd_periods,
        annual_periods=annual_periods,
        nonconfigured_tags=len(nonconfigured),
        history_span_days=span_days,
    )

    derived_ytd = int(rev["period_type"].eq("quarterly_derived_ytd").sum()) if not rev.empty else 0
    derived_q4 = int(rev["period_type"].eq("quarterly_derived_q4").sum()) if not rev.empty else 0
    return {
        "symbol": symbol,
        "cik": cik,
        "diagnosis_class": cls,
        "configured_tags_present": ";".join(sorted(set(facts["tag"].dropna()))) if not facts.empty else "",
        "nonconfigured_revenue_like_tags": ";".join(nonconfigured),
        "recent_revenue_periods": recent_periods,
        "recent_revenue_yoy_usable": yoy_usable,
        "direct_current_quarter_periods": direct_periods,
        "ytd_current_periods": ytd_periods,
        "annual_current_periods": annual_periods,
        "derived_ytd_periods": derived_ytd,
        "derived_q4_periods": derived_q4,
        "revenue_history_span_days": span_days,
        "revenue_evidence_stale_days": stale_days,
        "latest_supported_report_date": str(latest_report.date()) if pd.notna(latest_report) else None,
        "latest_revenue_end": str(latest_rev.date()) if pd.notna(latest_rev) else None,
    }


def diagnose(readiness_path: Path, output: Path, summary_output: Path) -> tuple[pd.DataFrame, dict]:
    readiness = pd.read_csv(readiness_path, dtype={"cik": str})
    targets = readiness[readiness["failure_class"].apply(lambda x: bool(_split_failures(x) & FAILURES))].copy()
    client = SecClient()
    rows = []
    for _, row in targets.sort_values("symbol").iterrows():
        symbol = str(row["symbol"]).upper()
        cik = str(row["cik"]).zfill(10)
        print(f"Diagnose quarterly revenue gap: {symbol} CIK={cik}")
        filing_rows = submissions(client, cik, Path("data/quarterly_revenue_probe/submissions"))
        payload = companyfacts(client, cik, Path("data/quarterly_revenue_probe/companyfacts"))
        stats = diagnose_symbol(symbol, cik, payload, filing_rows)
        stats["original_failure_class"] = row.get("failure_class")
        rows.append(stats)

    out = pd.DataFrame(rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output, index=False)
    counts = out["diagnosis_class"].value_counts().to_dict() if not out.empty else {}
    summary = {
        "target_symbols": int(len(out)),
        "diagnosis_class_counts": {str(k): int(v) for k, v in counts.items()},
        "policy": "Revenue is additive: direct discrete quarters, YTD reconstruction, and FY-minus-prior-quarter Q4 are allowed only with existing provenance/PIT rules.",
    }
    summary_output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("=== QUARTERLY REVENUE GAP DIAGNOSIS ===")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(out.to_csv(index=False))
    return out, summary


def main() -> None:
    p = argparse.ArgumentParser(description="Batch diagnose quarterly revenue YoY production failures")
    p.add_argument("--readiness", type=Path, default=Path("data/processed/fundamentals_readiness_report.csv"))
    p.add_argument("--output", type=Path, default=Path("data/processed/quarterly_revenue_gap_diagnosis.csv"))
    p.add_argument("--summary", type=Path, default=Path("data/processed/quarterly_revenue_gap_diagnosis_summary.json"))
    args = p.parse_args()
    diagnose(args.readiness, args.output, args.summary)


if __name__ == "__main__":
    main()
