from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .normalize import (
    REVENUE_TAGS,
    _current_period_only,
    accession_index,
    classify_period,
    fact_rows,
    normalize_company,
)
from .quarterly_revenue_gap_diagnose import _recent_window, _revenue_like_tags
from .sec_client import SecClient, companyfacts, submissions

FIXABLE_CLASSES = {
    "REVENUE_PRESENT_YOY_COMPARATOR_GAP",
    "STANDARD_REVENUE_EVIDENCE_STALE_TAG_TRANSITION_CANDIDATE",
    "NONSTANDARD_REVENUE_TAG_CANDIDATE",
}


def classify_comparator_evidence(
    *,
    recent_periods: int,
    missing_yoy_periods: int,
    raw_same_tag_comparators: int,
    raw_same_unit_comparators: int,
    pit_same_tag_comparators: int,
    pit_same_unit_comparators: int,
) -> str:
    if recent_periods < 4 and raw_same_unit_comparators == 0 and pit_same_unit_comparators == 0:
        return "SHORT_PRIOR_YEAR_HISTORY"
    if raw_same_tag_comparators > 0 or pit_same_tag_comparators > 0:
        return "COMPARATOR_PRESENT_NOT_SELECTED"
    if raw_same_unit_comparators > 0 or pit_same_unit_comparators > 0:
        return "CROSS_TAG_COMPARATOR_PRESENT"
    if missing_yoy_periods > 0:
        return "COMPARATOR_EVIDENCE_ABSENT"
    return "NO_COMPARATOR_GAP"


def _configured_revenue_facts(payload: dict, filing_rows: list[dict]) -> pd.DataFrame:
    idx = accession_index(filing_rows)
    facts = fact_rows(payload, REVENUE_TAGS, "revenue", idx)
    if not facts.empty:
        facts["period_type"] = facts["duration_days"].map(classify_period)
    return facts


def _comparator_probe(normalized: pd.DataFrame, facts: pd.DataFrame) -> dict:
    rev = normalized[normalized["metric"].eq("revenue")].copy() if not normalized.empty else pd.DataFrame()
    recent = _recent_window(rev, 8)
    if recent.empty:
        return {
            "recent_revenue_periods": 0,
            "recent_missing_yoy_periods": 0,
            "raw_same_tag_comparators": 0,
            "raw_same_unit_comparators": 0,
            "pit_same_tag_comparators": 0,
            "pit_same_unit_comparators": 0,
            "comparator_probe_class": "NO_COMPARATOR_GAP",
        }

    recent = recent.sort_values(["fiscal_period_end", "accepted_at"])
    missing = recent[recent["yoy"].isna()].copy()
    raw_same_tag = raw_same_unit = pit_same_tag = pit_same_unit = 0

    for _, row in missing.iterrows():
        lo = row["fiscal_period_end"] - pd.Timedelta(days=400)
        hi = row["fiscal_period_end"] - pd.Timedelta(days=330)
        raw = facts[
            facts["accession"].eq(row["accession"])
            & facts["end"].between(lo, hi, inclusive="both")
            & facts["duration_days"].between(60, 120, inclusive="both")
            & facts["unit"].eq(row["unit"])
        ].copy() if not facts.empty else pd.DataFrame()
        if not raw.empty:
            raw_same_unit += 1
            if raw["tag"].eq(row["tag"]).any():
                raw_same_tag += 1

        pit = rev[
            rev["fiscal_period_end"].between(lo, hi, inclusive="both")
            & (rev["accepted_at"] <= row["accepted_at"])
            & rev["unit"].eq(row["unit"])
        ].copy()
        if not pit.empty:
            pit_same_unit += 1
            if pit["tag"].eq(row["tag"]).any():
                pit_same_tag += 1

    cls = classify_comparator_evidence(
        recent_periods=int(recent["fiscal_period_end"].nunique()),
        missing_yoy_periods=int(missing["fiscal_period_end"].nunique()),
        raw_same_tag_comparators=raw_same_tag,
        raw_same_unit_comparators=raw_same_unit,
        pit_same_tag_comparators=pit_same_tag,
        pit_same_unit_comparators=pit_same_unit,
    )
    return {
        "recent_revenue_periods": int(recent["fiscal_period_end"].nunique()),
        "recent_missing_yoy_periods": int(missing["fiscal_period_end"].nunique()),
        "raw_same_tag_comparators": raw_same_tag,
        "raw_same_unit_comparators": raw_same_unit,
        "pit_same_tag_comparators": pit_same_tag,
        "pit_same_unit_comparators": pit_same_unit,
        "comparator_probe_class": cls,
    }


def _transition_candidates(payload: dict, filing_rows: list[dict], cutoff: pd.Timestamp) -> list[dict]:
    idx = accession_index(filing_rows)
    out: list[dict] = []
    for qualified in _revenue_like_tags(payload):
        namespace, tag = qualified.split(":", 1)
        if tag in REVENUE_TAGS:
            continue
        df = fact_rows(payload, [tag], "revenue_probe", idx)
        if df.empty:
            continue
        df = df[df["taxonomy"].eq(namespace)].copy()
        if df.empty:
            continue
        df["period_type"] = df["duration_days"].map(classify_period)
        df = _current_period_only(df[df["form"].isin(["10-Q", "10-Q/A", "10-K", "10-K/A"])])
        if df.empty:
            continue
        if pd.notna(cutoff):
            df = df[pd.to_datetime(df["end"], errors="coerce") > cutoff].copy()
        if df.empty:
            continue
        direct = int(df.loc[df["period_type"].eq("quarterly"), "end"].nunique())
        ytd = int(df.loc[df["period_type"].isin(["half_year", "nine_month"]), "end"].nunique())
        annual = int(df.loc[df["period_type"].eq("annual"), "end"].nunique())
        if direct == 0 and ytd == 0 and annual == 0:
            continue
        out.append({
            "tag": tag,
            "namespace": namespace,
            "direct": direct,
            "ytd": ytd,
            "annual": annual,
            "units": ";".join(sorted(set(str(x) for x in df["unit"].dropna()))),
        })
    return sorted(out, key=lambda x: (-(x["direct"] + x["ytd"]), x["tag"]))


def probe(diagnosis_path: Path, readiness_path: Path, output: Path, summary_output: Path) -> tuple[pd.DataFrame, dict]:
    diagnosis = pd.read_csv(diagnosis_path, dtype={"cik": str})
    readiness = pd.read_csv(readiness_path, dtype={"cik": str})
    targets = diagnosis[diagnosis["diagnosis_class"].isin(FIXABLE_CLASSES)].copy()
    readiness_cols = [c for c in [
        "symbol", "latest_fiscal_period", "quarterly_revenue_yoy_latest_period",
        "quarterly_revenue_yoy_stale_quarters", "quarterly_revenue_yoy_usable",
    ] if c in readiness.columns]
    if readiness_cols:
        targets = targets.merge(readiness[readiness_cols].drop_duplicates("symbol"), on="symbol", how="left")

    client = SecClient()
    rows: list[dict] = []
    for _, src in targets.sort_values("symbol").iterrows():
        symbol = str(src["symbol"]).upper()
        cik = str(src["cik"]).zfill(10)
        print(f"Probe fixable revenue residual: {symbol} CIK={cik} class={src['diagnosis_class']}")
        filing_rows = submissions(client, cik, Path("data/residual_revenue_fixable_probe/submissions"))
        payload = companyfacts(client, cik, Path("data/residual_revenue_fixable_probe/companyfacts"))
        facts = _configured_revenue_facts(payload, filing_rows)
        normalized = normalize_company(symbol, cik, payload, filing_rows)
        comp = _comparator_probe(normalized, facts)

        yoy_cutoff = pd.to_datetime(src.get("quarterly_revenue_yoy_latest_period"), errors="coerce")
        if pd.isna(yoy_cutoff):
            yoy_cutoff = pd.to_datetime(src.get("latest_revenue_end"), errors="coerce")
        transitions = _transition_candidates(payload, filing_rows, yoy_cutoff)
        useful = [x for x in transitions if x["direct"] >= 2 or x["ytd"] >= 2]

        row = {
            "symbol": symbol,
            "cik": cik,
            "original_diagnosis_class": src["diagnosis_class"],
            **comp,
            "latest_fiscal_period": src.get("latest_fiscal_period"),
            "quarterly_revenue_yoy_latest_period": src.get("quarterly_revenue_yoy_latest_period"),
            "quarterly_revenue_yoy_stale_quarters": src.get("quarterly_revenue_yoy_stale_quarters"),
            "quarterly_revenue_yoy_usable": src.get("quarterly_revenue_yoy_usable"),
            "useful_transition_candidate_count": len(useful),
            "useful_transition_tags": ";".join(x["tag"] for x in useful),
            "transition_candidates_json": json.dumps(transitions, sort_keys=True),
        }
        rows.append(row)

    out = pd.DataFrame(rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output, index=False)
    summary = {
        "target_symbols": int(len(out)),
        "original_class_counts": {str(k): int(v) for k, v in out["original_diagnosis_class"].value_counts().to_dict().items()} if not out.empty else {},
        "comparator_probe_class_counts": {str(k): int(v) for k, v in out["comparator_probe_class"].value_counts().to_dict().items()} if not out.empty else {},
        "symbols_with_useful_transition_candidates": int((out["useful_transition_candidate_count"] > 0).sum()) if not out.empty else 0,
        "policy": "No production tag/comparator change unless this probe shows reusable evidence across current-period SEC facts.",
    }
    summary_output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("=== FIXABLE RESIDUAL REVENUE PROBE ===")
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not out.empty:
        print(out.to_csv(index=False))
    return out, summary


def main() -> None:
    p = argparse.ArgumentParser(description="Probe the remaining plausibly-fixable quarterly revenue failures")
    p.add_argument("--diagnosis", type=Path, default=Path("data/processed/residual_quarterly_revenue_gap_diagnosis.csv"))
    p.add_argument("--readiness", type=Path, default=Path("data/processed/fundamentals_readiness_report.csv"))
    p.add_argument("--output", type=Path, default=Path("data/processed/residual_revenue_fixable_probe.csv"))
    p.add_argument("--summary", type=Path, default=Path("data/processed/residual_revenue_fixable_probe_summary.json"))
    args = p.parse_args()
    probe(args.diagnosis, args.readiness, args.output, args.summary)


if __name__ == "__main__":
    main()
