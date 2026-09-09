from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .normalize import EPS_TAGS, accession_index, classify_period, fact_rows, _current_period_only
from .quarterly_eps_gap_diagnose import _eps_like_tags
from .sec_client import SecClient, companyfacts, submissions

TARGET_CLASS = "NONSTANDARD_EPS_TAGS_ONLY"


def _candidate_tags(payload: dict) -> list[tuple[str, str]]:
    configured = set(EPS_TAGS)
    out: list[tuple[str, str]] = []
    for qualified in _eps_like_tags(payload):
        namespace, tag = qualified.split(":", 1)
        if tag not in configured:
            out.append((namespace, tag))
    return out


def _tag_stats(payload: dict, filing_rows: list[dict], namespace: str, tag: str) -> dict:
    idx = accession_index(filing_rows)
    facts = fact_rows(payload, [tag], "eps_probe", idx)
    if not facts.empty:
        facts = facts[facts["taxonomy"].eq(namespace)].copy()
        facts["period_type"] = facts["duration_days"].map(classify_period)
    q = facts[
        facts["form"].isin(["10-Q", "10-Q/A"]) & facts["period_type"].eq("quarterly")
    ].copy() if not facts.empty else pd.DataFrame()
    q = _current_period_only(q) if not q.empty else q
    annual = facts[
        facts["form"].isin(["10-K", "10-K/A"]) & facts["period_type"].eq("annual")
    ].copy() if not facts.empty else pd.DataFrame()
    annual = _current_period_only(annual) if not annual.empty else annual
    units = sorted(set(str(x) for x in facts.get("unit", pd.Series(dtype=str)).dropna())) if not facts.empty else []
    latest = pd.to_datetime(q.get("end"), errors="coerce").max() if not q.empty else pd.NaT
    return {
        "namespace": namespace,
        "tag": tag,
        "units": ";".join(units),
        "quarterly_current_periods": int(q["end"].nunique()) if not q.empty else 0,
        "annual_current_periods": int(annual["end"].nunique()) if not annual.empty else 0,
        "latest_quarterly_end": str(latest.date()) if pd.notna(latest) else None,
    }


def probe(diagnosis_path: Path, output: Path, summary_output: Path) -> tuple[pd.DataFrame, dict]:
    diagnosis = pd.read_csv(diagnosis_path, dtype={"cik": str})
    targets = diagnosis[diagnosis["diagnosis_class"].eq(TARGET_CLASS)].copy()
    client = SecClient()
    rows: list[dict] = []
    for _, row in targets.sort_values("symbol").iterrows():
        symbol = str(row["symbol"]).upper()
        cik = str(row["cik"]).zfill(10)
        print(f"Probe nonstandard EPS: {symbol} CIK={cik}")
        filing_rows = submissions(client, cik, Path("data/nonstandard_eps_probe/submissions"))
        payload = companyfacts(client, cik, Path("data/nonstandard_eps_probe/companyfacts"))
        candidates = _candidate_tags(payload)
        if not candidates:
            rows.append({"symbol": symbol, "cik": cik, "namespace": None, "tag": None, "units": None,
                         "quarterly_current_periods": 0, "annual_current_periods": 0,
                         "latest_quarterly_end": None, "review_class": "NO_CANDIDATE_TAG"})
            continue
        for namespace, tag in candidates:
            stats = _tag_stats(payload, filing_rows, namespace, tag)
            stats.update({"symbol": symbol, "cik": cik})
            qn = stats["quarterly_current_periods"]
            unit_text = stats["units"].lower()
            per_share_unit = "/share" in unit_text or "shares" in unit_text
            if qn >= 2 and per_share_unit:
                review = "QUARTERLY_PER_SHARE_CANDIDATE_REVIEW"
            elif qn >= 2:
                review = "QUARTERLY_SEMANTICS_OR_UNIT_REVIEW"
            else:
                review = "INSUFFICIENT_DIRECT_QUARTERLY_HISTORY"
            stats["review_class"] = review
            rows.append(stats)

    out = pd.DataFrame(rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output, index=False)
    counts = out["review_class"].value_counts().to_dict() if not out.empty else {}
    summary = {
        "target_symbols": int(len(targets)),
        "candidate_rows": int(len(out)),
        "symbols_with_quarterly_per_share_candidates": int(out.loc[out["review_class"].eq("QUARTERLY_PER_SHARE_CANDIDATE_REVIEW"), "symbol"].nunique()) if not out.empty else 0,
        "review_class_counts": {str(k): int(v) for k, v in counts.items()},
        "policy": "Probe only; no EPS vocabulary expansion without semantic review and reported-EPS validation.",
    }
    summary_output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("=== NONSTANDARD EPS TAG PROBE ===")
    print(f"Target symbols: {len(targets)}")
    for key, value in counts.items():
        print(f"  {key}: {value}")
    print(f"Wrote {output}")
    print(f"Wrote {summary_output}")
    return out, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe nonstandard EPS-like SEC tags without changing production vocabulary")
    parser.add_argument("--diagnosis", type=Path, default=Path("data/processed/quarterly_eps_gap_diagnosis.csv"))
    parser.add_argument("--output", type=Path, default=Path("data/processed/nonstandard_eps_probe.csv"))
    parser.add_argument("--summary", type=Path, default=Path("data/processed/nonstandard_eps_probe_summary.json"))
    args = parser.parse_args()
    probe(args.diagnosis, args.output, args.summary)


if __name__ == "__main__":
    main()
