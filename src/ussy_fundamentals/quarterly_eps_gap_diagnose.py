from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .annual_fallback import DILUTED_SHARES_TAGS, NET_INCOME_TAGS
from .normalize import EPS_TAGS, _current_period_only, accession_index, classify_period, fact_rows, normalize_company
from .pipeline import _drop_nonadditive_derived_eps
from .sec_client import SecClient, companyfacts, submissions

FAILURE = "QUARTERLY_EPS_YOY_INSUFFICIENT"


def _split_failures(value) -> set[str]:
    if pd.isna(value):
        return set()
    return {x for x in str(value).split(";") if x}


def _eps_like_tags(payload: dict) -> list[str]:
    out = []
    for namespace, concepts in payload.get("facts", {}).items():
        for tag in concepts:
            low = tag.lower()
            if "earningspershare" in low or "perdilutedshare" in low or "perbasicshare" in low:
                out.append(f"{namespace}:{tag}")
    return sorted(out)


def _recent_window(df: pd.DataFrame, periods: int = 8) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["end"] = pd.to_datetime(out["end"], errors="coerce")
    ends = sorted(out["end"].dropna().unique())
    if len(ends) <= periods:
        return out
    keep = set(ends[-periods:])
    return out[out["end"].isin(keep)].copy()


def _direct_quarter_candidates(payload: dict, idx: dict[str, dict], tags: list[str], metric: str) -> pd.DataFrame:
    df = fact_rows(payload, tags, metric, idx)
    if df.empty:
        return df
    df["period_type"] = df["duration_days"].map(classify_period)
    df = df[
        df["form"].isin(["10-Q", "10-Q/A"])
        & df["period_type"].eq("quarterly")
    ].copy()
    return _current_period_only(df)


def _fallback_pair_periods(payload: dict, idx: dict[str, dict]) -> tuple[int, str | None]:
    ni = _direct_quarter_candidates(payload, idx, NET_INCOME_TAGS, "net_income")
    sh = _direct_quarter_candidates(payload, idx, DILUTED_SHARES_TAGS, "diluted_shares")
    if ni.empty or sh.empty:
        return 0, None
    pairs = ni[["accession", "end"]].drop_duplicates().merge(
        sh[["accession", "end"]].drop_duplicates(), on=["accession", "end"], how="inner"
    )
    if pairs.empty:
        return 0, None
    recent = _recent_window(pairs.sort_values("end"), 8)
    latest = pd.to_datetime(recent["end"], errors="coerce").max()
    return int(recent["end"].nunique()), str(latest.date()) if pd.notna(latest) else None


def _latest_supported_report(filing_rows: list[dict]) -> pd.Timestamp:
    values = []
    for row in filing_rows:
        if row.get("form") not in {"10-Q", "10-Q/A", "10-K", "10-K/A"}:
            continue
        dt = pd.to_datetime(row.get("reportDate"), errors="coerce")
        if pd.notna(dt):
            values.append(dt)
    return max(values) if values else pd.NaT


def classify_symbol(symbol: str, cik: str, payload: dict, filing_rows: list[dict]) -> tuple[str, dict]:
    idx = accession_index(filing_rows)
    facts = fact_rows(payload, EPS_TAGS, "eps", idx)
    if not facts.empty:
        facts["period_type"] = facts["duration_days"].map(classify_period)

    q10 = facts[
        facts["form"].isin(["10-Q", "10-Q/A"])
        & facts["period_type"].eq("quarterly")
    ].copy() if not facts.empty else pd.DataFrame()
    current_direct = _current_period_only(q10) if not q10.empty else q10

    ytd = facts[
        facts["form"].isin(["10-Q", "10-Q/A"])
        & facts["period_type"].isin(["half_year", "nine_month"])
    ].copy() if not facts.empty else pd.DataFrame()
    current_ytd = _current_period_only(ytd) if not ytd.empty else ytd

    normalized = normalize_company(symbol, cik, payload, filing_rows)
    normalized = _drop_nonadditive_derived_eps(normalized)
    eps_norm = normalized[normalized["metric"].eq("eps")].copy() if not normalized.empty else pd.DataFrame()
    recent_norm = _recent_window(eps_norm, 8)

    direct_recent = _recent_window(current_direct, 8)
    ytd_recent = _recent_window(current_ytd, 8)
    eps_like = _eps_like_tags(payload)
    fallback_periods, fallback_latest = _fallback_pair_periods(payload, idx)

    norm_periods = int(recent_norm["fiscal_period_end"].nunique()) if not recent_norm.empty else 0
    yoy_usable = int(pd.to_numeric(recent_norm.get("yoy"), errors="coerce").notna().sum()) if not recent_norm.empty else 0
    direct_periods = int(direct_recent["end"].nunique()) if not direct_recent.empty else 0
    ytd_periods = int(ytd_recent["end"].nunique()) if not ytd_recent.empty else 0

    latest_direct = pd.to_datetime(current_direct["end"], errors="coerce").max() if not current_direct.empty else pd.NaT
    earliest_direct = pd.to_datetime(current_direct["end"], errors="coerce").min() if not current_direct.empty else pd.NaT
    latest_report = _latest_supported_report(filing_rows)
    direct_span_days = int((latest_direct - earliest_direct).days) if pd.notna(latest_direct) and pd.notna(earliest_direct) else None
    direct_stale_days = int((latest_report - latest_direct).days) if pd.notna(latest_report) and pd.notna(latest_direct) else None

    configured_present = sorted(set(facts["tag"].dropna())) if not facts.empty else []
    nonconfigured_eps_like = sorted(tag for tag in eps_like if tag.split(":", 1)[-1] not in set(EPS_TAGS))

    if direct_periods >= 1 and direct_stale_days is not None and direct_stale_days > 180:
        cls = "STANDARD_EPS_EVIDENCE_STALE"
    elif direct_periods >= 1 and direct_periods <= 4 and direct_span_days is not None and direct_span_days < 330:
        cls = "SHORT_QUARTERLY_HISTORY_EXPECTED"
    elif direct_periods >= 2 and norm_periods >= 2 and yoy_usable < 2:
        cls = "DIRECT_EPS_PRESENT_YOY_COMPARATOR_GAP"
    elif direct_periods >= 2 and norm_periods < 2:
        cls = "DIRECT_EPS_PRESENT_NORMALIZATION_GAP"
    elif direct_periods < 2 and fallback_periods >= 2:
        cls = "DIRECT_NET_INCOME_SHARES_FALLBACK_AVAILABLE"
    elif direct_periods < 2 and ytd_periods >= 2:
        cls = "YTD_ONLY_OR_MOSTLY_YTD"
    elif not configured_present and nonconfigured_eps_like:
        cls = "NONSTANDARD_EPS_TAGS_ONLY"
    elif not configured_present:
        cls = "NO_USABLE_EPS_EVIDENCE"
    else:
        cls = "SPARSE_STANDARD_EPS_EVIDENCE"

    stats = {
        "symbol": symbol,
        "cik": cik,
        "diagnosis_class": cls,
        "configured_tags_present": ";".join(configured_present),
        "nonconfigured_eps_like_tags": ";".join(nonconfigured_eps_like),
        "direct_current_quarter_periods": direct_periods,
        "direct_history_span_days": direct_span_days,
        "direct_evidence_stale_days": direct_stale_days,
        "latest_supported_report_date": str(latest_report.date()) if pd.notna(latest_report) else None,
        "ytd_current_periods": ytd_periods,
        "normalized_recent_eps_periods": norm_periods,
        "normalized_recent_eps_yoy_usable": yoy_usable,
        "fallback_direct_net_income_shares_periods": fallback_periods,
        "fallback_latest_end": fallback_latest,
        "latest_direct_end": str(latest_direct.date()) if pd.notna(latest_direct) else None,
        "latest_normalized_end": str(pd.to_datetime(eps_norm["fiscal_period_end"], errors="coerce").max().date()) if not eps_norm.empty else None,
    }
    return cls, stats


def diagnose(readiness_path: Path, output: Path, summary_output: Path) -> tuple[pd.DataFrame, dict]:
    readiness = pd.read_csv(readiness_path, dtype={"cik": str})
    targets = readiness[readiness["failure_class"].apply(lambda x: FAILURE in _split_failures(x))].copy()
    client = SecClient()
    rows = []
    for _, row in targets.sort_values("symbol").iterrows():
        symbol = str(row["symbol"]).upper()
        cik = str(row["cik"]).zfill(10)
        print(f"Diagnose quarterly EPS gap: {symbol} CIK={cik}")
        filing_rows = submissions(client, cik, Path("data/quarterly_eps_probe/submissions"))
        payload = companyfacts(client, cik, Path("data/quarterly_eps_probe/companyfacts"))
        _, stats = classify_symbol(symbol, cik, payload, filing_rows)
        stats["original_failure_class"] = row.get("failure_class")
        rows.append(stats)

    out = pd.DataFrame(rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output, index=False)
    counts = out["diagnosis_class"].value_counts().to_dict() if not out.empty else {}
    summary = {
        "target_symbols": int(len(out)),
        "diagnosis_class_counts": {str(k): int(v) for k, v in counts.items()},
        "fallback_candidates": int((out.get("fallback_direct_net_income_shares_periods", pd.Series(dtype=int)) >= 2).sum()) if not out.empty else 0,
    }
    summary_output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("=== QUARTERLY EPS GAP DIAGNOSIS ===")
    print(f"Target symbols: {len(out)}")
    for key, value in counts.items():
        print(f"  {key}: {value}")
    print(f"Fallback input candidates: {summary['fallback_candidates']}")
    print(f"Wrote {output}")
    print(f"Wrote {summary_output}")
    return out, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch diagnose QUARTERLY_EPS_YOY_INSUFFICIENT symbols")
    parser.add_argument("--readiness", type=Path, default=Path("data/processed/fundamentals_readiness_report.csv"))
    parser.add_argument("--output", type=Path, default=Path("data/processed/quarterly_eps_gap_diagnosis.csv"))
    parser.add_argument("--summary", type=Path, default=Path("data/processed/quarterly_eps_gap_diagnosis_summary.json"))
    args = parser.parse_args()
    diagnose(args.readiness, args.output, args.summary)


if __name__ == "__main__":
    main()
