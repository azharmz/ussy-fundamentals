from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pandas as pd

from .normalize import REVENUE_TAGS, _current_period_only, accession_index, classify_period, fact_rows
from .quarterly_revenue_gap_diagnose import _revenue_like_tags
from .sec_client import SecClient, companyfacts, submissions

TARGET_CLASS = "STANDARD_REVENUE_EVIDENCE_STALE_TAG_TRANSITION_CANDIDATE"


def _current_periods(payload: dict, idx: dict[str, dict], namespace: str, tag: str) -> pd.DataFrame:
    df = fact_rows(payload, [tag], "revenue_probe", idx)
    if df.empty:
        return df
    df = df[df["taxonomy"].eq(namespace)].copy()
    if df.empty:
        return df
    df["period_type"] = df["duration_days"].map(classify_period)
    df = df[df["form"].isin(["10-Q", "10-Q/A", "10-K", "10-K/A"])].copy()
    return _current_period_only(df)


def probe_symbol(symbol: str, cik: str, payload: dict, filing_rows: list[dict], latest_configured_end: pd.Timestamp) -> list[dict]:
    idx = accession_index(filing_rows)
    rows: list[dict] = []
    for qualified in _revenue_like_tags(payload):
        namespace, tag = qualified.split(":", 1)
        if tag in REVENUE_TAGS:
            continue
        df = _current_periods(payload, idx, namespace, tag)
        if df.empty:
            continue
        end = pd.to_datetime(df["end"], errors="coerce")
        post = df[end.gt(latest_configured_end)].copy() if pd.notna(latest_configured_end) else df.copy()
        if post.empty:
            continue
        direct = post[post["period_type"].eq("quarterly")]
        ytd = post[post["period_type"].isin(["half_year", "nine_month"])]
        annual = post[post["period_type"].eq("annual")]
        units = sorted(set(str(x) for x in post["unit"].dropna()))
        rows.append({
            "symbol": symbol,
            "cik": cik,
            "namespace": namespace,
            "tag": tag,
            "units": ";".join(units),
            "post_cutoff_direct_quarters": int(direct["end"].nunique()),
            "post_cutoff_ytd_periods": int(ytd["end"].nunique()),
            "post_cutoff_annual_periods": int(annual["end"].nunique()),
            "post_cutoff_total_current_periods": int(post["end"].nunique()),
            "latest_candidate_end": str(pd.to_datetime(post["end"], errors="coerce").max().date()),
        })
    return rows


def probe(input_path: Path, output: Path, summary_output: Path) -> tuple[pd.DataFrame, dict]:
    src = pd.read_csv(input_path, dtype={"cik": str})
    targets = src[src["diagnosis_class"].eq(TARGET_CLASS)].copy()
    client = SecClient()
    rows: list[dict] = []
    for _, row in targets.sort_values("symbol").iterrows():
        symbol = str(row["symbol"]).upper()
        cik = str(row["cik"]).zfill(10)
        cutoff = pd.to_datetime(row.get("latest_revenue_end"), errors="coerce")
        print(f"Probe revenue transition: {symbol} CIK={cik} cutoff={cutoff}")
        filings = submissions(client, cik, Path("data/revenue_transition_probe/submissions"))
        payload = companyfacts(client, cik, Path("data/revenue_transition_probe/companyfacts"))
        rows.extend(probe_symbol(symbol, cik, payload, filings, cutoff))

    out = pd.DataFrame(rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output, index=False)
    if out.empty:
        tag_counts = {}
        symbols_with_candidates = 0
    else:
        useful = out[(out["post_cutoff_direct_quarters"] >= 2) | (out["post_cutoff_ytd_periods"] >= 2)]
        tag_counts = {str(k): int(v) for k, v in useful["tag"].value_counts().to_dict().items()}
        symbols_with_candidates = int(useful["symbol"].nunique())
    summary = {
        "target_symbols": int(len(targets)),
        "symbols_with_post_cutoff_quarterly_candidates": symbols_with_candidates,
        "candidate_tag_counts": tag_counts,
        "policy": "Probe only. A tag is not added to production vocabulary until current-period and semantic continuity are validated.",
    }
    summary_output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("=== REVENUE TAG TRANSITION PROBE ===")
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not out.empty:
        print(out.to_csv(index=False))
    return out, summary


def main() -> None:
    p = argparse.ArgumentParser(description="Probe actual post-cutoff revenue tag transitions")
    p.add_argument("--input", type=Path, default=Path("data/processed/quarterly_revenue_gap_diagnosis.csv"))
    p.add_argument("--output", type=Path, default=Path("data/processed/revenue_tag_transition_probe.csv"))
    p.add_argument("--summary", type=Path, default=Path("data/processed/revenue_tag_transition_probe_summary.json"))
    args = p.parse_args()
    probe(args.input, args.output, args.summary)


if __name__ == "__main__":
    main()
