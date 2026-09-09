from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .normalize import REVENUE_TAGS, _same_filing_prior, accession_index, classify_period, fact_rows, normalize_company
from .quarterly_revenue_gap_diagnose import _recent_window
from .sec_client import SecClient, companyfacts, submissions

TARGET_CLASS = "COMPARATOR_PRESENT_NOT_SELECTED"


def _facts(payload: dict, filing_rows: list[dict]) -> pd.DataFrame:
    idx = accession_index(filing_rows)
    out = fact_rows(payload, REVENUE_TAGS, "revenue", idx)
    if not out.empty:
        out["period_type"] = out["duration_days"].map(classify_period)
    return out


def _pit_prior(row: pd.Series, rev: pd.DataFrame):
    lo = row["fiscal_period_end"] - pd.Timedelta(days=400)
    hi = row["fiscal_period_end"] - pd.Timedelta(days=330)
    prior = rev[
        rev["fiscal_period_end"].between(lo, hi, inclusive="both")
        & (rev["accepted_at"] <= row["accepted_at"])
        & rev["unit"].eq(row["unit"])
    ].copy()
    if prior.empty:
        return None
    same = prior[prior["tag"].eq(row["tag"])]
    if not same.empty:
        prior = same
    return prior.sort_values("accepted_at").iloc[-1]["value"]


def probe(input_path: Path, output: Path, summary_output: Path) -> tuple[pd.DataFrame, dict]:
    src = pd.read_csv(input_path, dtype={"cik": str})
    targets = src[src["comparator_probe_class"].eq(TARGET_CLASS)].copy()
    client = SecClient()
    rows: list[dict] = []

    for _, t in targets.sort_values("symbol").iterrows():
        symbol = str(t["symbol"]).upper()
        cik = str(t["cik"]).zfill(10)
        print(f"Probe comparator values: {symbol} CIK={cik}")
        filing_rows = submissions(client, cik, Path("data/revenue_comparator_value_probe/submissions"))
        payload = companyfacts(client, cik, Path("data/revenue_comparator_value_probe/companyfacts"))
        facts = _facts(payload, filing_rows)
        normalized = normalize_company(symbol, cik, payload, filing_rows)
        rev = normalized[normalized["metric"].eq("revenue")].copy() if not normalized.empty else pd.DataFrame()
        recent = _recent_window(rev, 8).sort_values(["fiscal_period_end", "accepted_at"])
        missing = recent[recent["yoy"].isna()].copy()

        for _, row in missing.iterrows():
            same = _same_filing_prior(row, facts, 60, 120) if not facts.empty else None
            pit = _pit_prior(row, rev) if not rev.empty else None
            chosen = same if same is not None else pit
            if chosen is None:
                reason = "NO_COMPARATOR"
            elif float(chosen) == 0:
                reason = "ZERO_BASE_INTENTIONALLY_UNDEFINED"
            else:
                reason = "NONZERO_COMPARATOR_SHOULD_YIELD_YOY"
            rows.append({
                "symbol": symbol,
                "cik": cik,
                "fiscal_period_end": row["fiscal_period_end"],
                "accepted_at": row["accepted_at"],
                "tag": row["tag"],
                "unit": row["unit"],
                "current_revenue": row["value"],
                "same_filing_prior": same,
                "pit_prior": pit,
                "chosen_prior": chosen,
                "reason": reason,
            })

    out = pd.DataFrame(rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output, index=False)
    counts = out["reason"].value_counts().to_dict() if not out.empty else {}
    summary = {
        "target_symbols": int(len(targets)),
        "missing_yoy_rows": int(len(out)),
        "reason_counts": {str(k): int(v) for k, v in counts.items()},
        "symbols_with_nonzero_comparator_bug": int(out.loc[out["reason"].eq("NONZERO_COMPARATOR_SHOULD_YIELD_YOY"), "symbol"].nunique()) if not out.empty else 0,
        "policy": "A zero prior-year revenue base makes percentage YoY undefined; only nonzero comparator rows indicate a normalizer bug.",
    }
    summary_output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("=== REVENUE COMPARATOR VALUE PROBE ===")
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not out.empty:
        print(out.to_csv(index=False))
    return out, summary


def main() -> None:
    p = argparse.ArgumentParser(description="Inspect comparator values for residual revenue YoY gaps")
    p.add_argument("--input", type=Path, default=Path("data/processed/residual_revenue_fixable_probe.csv"))
    p.add_argument("--output", type=Path, default=Path("data/processed/revenue_comparator_value_probe.csv"))
    p.add_argument("--summary", type=Path, default=Path("data/processed/revenue_comparator_value_probe_summary.json"))
    args = p.parse_args()
    probe(args.input, args.output, args.summary)


if __name__ == "__main__":
    main()
