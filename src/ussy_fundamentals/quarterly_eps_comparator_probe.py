from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .normalize import EPS_TAGS, _current_period_only, accession_index, classify_period, fact_rows, normalize_company
from .pipeline import _drop_nonadditive_derived_eps
from .sec_client import SecClient, companyfacts, submissions

TARGET_CLASS = "DIRECT_EPS_PRESENT_YOY_COMPARATOR_GAP"


def _direct_current(payload: dict, filings: list[dict]) -> pd.DataFrame:
    idx = accession_index(filings)
    facts = fact_rows(payload, EPS_TAGS, "eps", idx)
    if facts.empty:
        return facts
    facts["period_type"] = facts["duration_days"].map(classify_period)
    q = facts[facts["form"].isin(["10-Q", "10-Q/A"]) & facts["period_type"].eq("quarterly")].copy()
    return _current_period_only(q)


def probe(diagnosis_path: Path, output: Path) -> pd.DataFrame:
    dx = pd.read_csv(diagnosis_path, dtype={"cik": str})
    targets = dx[dx["diagnosis_class"].eq(TARGET_CLASS)].copy()
    client = SecClient()
    rows = []

    for _, t in targets.sort_values("symbol").iterrows():
        symbol = str(t["symbol"]).upper()
        cik = str(t["cik"]).zfill(10)
        print(f"=== {symbol} CIK={cik} ===")
        filings = submissions(client, cik, Path("data/qeps_cmp_probe/submissions"))
        payload = companyfacts(client, cik, Path("data/qeps_cmp_probe/companyfacts"))
        direct = _direct_current(payload, filings)
        norm = normalize_company(symbol, cik, payload, filings)
        norm = _drop_nonadditive_derived_eps(norm)
        norm = norm[norm["metric"].eq("eps")].copy() if not norm.empty else pd.DataFrame()

        if direct.empty or norm.empty:
            print("No direct or normalized EPS rows")
            continue

        direct["end"] = pd.to_datetime(direct["end"], errors="coerce")
        direct["accepted_at"] = pd.to_datetime(direct["accepted_at"], errors="coerce", utc=True)
        norm["fiscal_period_end"] = pd.to_datetime(norm["fiscal_period_end"], errors="coerce")
        norm["accepted_at"] = pd.to_datetime(norm["accepted_at"], errors="coerce", utc=True)

        for _, r in norm.sort_values("fiscal_period_end").iterrows():
            if pd.notna(r.get("yoy")):
                continue
            end = r["fiscal_period_end"]
            lo, hi = end - pd.Timedelta(days=400), end - pd.Timedelta(days=330)
            candidates = direct[
                direct["end"].between(lo, hi, inclusive="both")
                & direct["unit"].eq(r["unit"])
                & direct["accepted_at"].le(r["accepted_at"])
            ].copy()
            same_tag = candidates[candidates["tag"].eq(r["tag"])]
            picked = same_tag if not same_tag.empty else candidates
            prior_value = pd.NA
            prior_end = pd.NaT
            prior_tag = pd.NA
            reason = "NO_COMPARABLE_PRIOR"
            if not picked.empty:
                p = picked.sort_values("accepted_at").iloc[-1]
                prior_value, prior_end, prior_tag = p["value"], p["end"], p["tag"]
                if pd.isna(prior_value):
                    reason = "PRIOR_VALUE_MISSING"
                elif prior_value <= 0:
                    reason = "NONPOSITIVE_PRIOR_BASE"
                else:
                    reason = "COMPARATOR_AVAILABLE_BUT_NOT_USED"
            row = {
                "symbol": symbol,
                "cik": cik,
                "current_end": end,
                "current_value": r["value"],
                "current_tag": r["tag"],
                "current_unit": r["unit"],
                "current_accession": r["accession"],
                "current_accepted_at": r["accepted_at"],
                "prior_end": prior_end,
                "prior_value": prior_value,
                "prior_tag": prior_tag,
                "candidate_count": int(len(candidates)),
                "reason": reason,
            }
            rows.append(row)
            print(pd.Series(row).to_string())
            print()

    out = pd.DataFrame(rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output, index=False)
    print("=== SUMMARY ===")
    if out.empty:
        print("No rows")
    else:
        print(out.groupby(["symbol", "reason"]).size().to_string())
        print("\nReason totals:")
        print(out["reason"].value_counts().to_string())
    print(f"Wrote {output}")
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--diagnosis", type=Path, default=Path("data/processed/quarterly_eps_gap_diagnosis.csv"))
    p.add_argument("--output", type=Path, default=Path("data/processed/quarterly_eps_comparator_probe.csv"))
    a = p.parse_args()
    probe(a.diagnosis, a.output)


if __name__ == "__main__":
    main()
