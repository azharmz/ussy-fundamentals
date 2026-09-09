from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .annual_fallback import _derived_state
from .normalize import EPS_TAGS, _build_annual_eps_state, accession_index, fact_rows
from .sec_client import SecClient, submissions

DEFAULT_SYMBOLS = {
    "ANNA": "0001845123",
    "BRLS": "0001852973",
    "NEGG": "0001474627",
    "OPTX": "0001866816",
    "XPON": "0001894954",
    "ARLP": "0001086600",
    "MGY": "0001698990",
    "V": "0001403161",
}


def _annual_eps_rows(payload: dict, filings: list[dict]) -> pd.DataFrame:
    df = fact_rows(payload, EPS_TAGS, "eps", accession_index(filings))
    if df.empty:
        return df
    return df[
        df["form"].isin(["10-K", "10-K/A"])
        & df["duration_days"].between(300, 430, inclusive="both")
    ].copy()


def probe_symbol(symbol: str, cik: str, client: SecClient) -> tuple[pd.DataFrame, dict]:
    filings = submissions(client, cik, Path("data/probe/submissions"))
    payload = client.get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
    annual = _annual_eps_rows(payload, filings)
    state = _build_annual_eps_state(annual)
    fallback = _derived_state(payload, filings)

    rows = []
    if not annual.empty:
        for _, r in annual.sort_values(["end", "accepted_at", "tag_priority"]).iterrows():
            end = pd.to_datetime(r.get("end"), errors="coerce")
            report = pd.to_datetime(r.get("report_date"), errors="coerce")
            accepted = pd.to_datetime(r.get("accepted_at"), errors="coerce", utc=True)
            end_utc = pd.to_datetime(r.get("end"), errors="coerce", utc=True)
            lag = None
            if pd.notna(accepted) and pd.notna(end_utc):
                lag = (accepted - end_utc).total_seconds() / 86400.0
            rows.append({
                "symbol": symbol,
                "cik": cik,
                "end": end,
                "report_date": report,
                "accepted_at": accepted,
                "accession": r.get("accession"),
                "tag": r.get("tag"),
                "taxonomy": r.get("taxonomy"),
                "unit": r.get("unit"),
                "duration_days": r.get("duration_days"),
                "end_matches_report": bool(pd.notna(end) and pd.notna(report) and end == report),
                "accepted_lag_days": lag,
                "has_accession_metadata": bool(pd.notna(r.get("accession")) and pd.notna(accepted)),
            })

    detail = pd.DataFrame(rows)
    summary = {
        "symbol": symbol,
        "cik": cik,
        "annual_eps_fact_periods": int(annual["end"].nunique()) if not annual.empty else 0,
        "annual_eps_fact_accessions": int(annual["accession"].nunique()) if not annual.empty else 0,
        "annual_eps_state_rows": int(len(state)),
        "annual_eps_state_accessions": int(state["accession"].nunique()) if not state.empty else 0,
        "fallback_state_rows": int(len(fallback)),
        "facts_missing_accepted_at": int(annual["accepted_at"].isna().sum()) if not annual.empty else 0,
        "facts_report_date_mismatch": int((pd.to_datetime(annual["end"], errors="coerce") != pd.to_datetime(annual["report_date"], errors="coerce")).sum()) if not annual.empty else 0,
    }
    return detail, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detail", type=Path, default=Path("data/processed/annual_standard_probe.csv"))
    parser.add_argument("--summary", type=Path, default=Path("data/processed/annual_standard_probe_summary.json"))
    args = parser.parse_args()

    client = SecClient()
    details = []
    summaries = []
    for symbol, cik in DEFAULT_SYMBOLS.items():
        print(f"Probe {symbol} CIK={cik}")
        detail, summary = probe_symbol(symbol, cik, client)
        details.append(detail)
        summaries.append(summary)

    out = pd.concat(details, ignore_index=True) if details else pd.DataFrame()
    args.detail.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.detail, index=False)
    args.summary.write_text(json.dumps(summaries, indent=2, default=str), encoding="utf-8")

    print("=== ANNUAL STANDARD PROBE ===")
    for row in summaries:
        print(row)
    print(f"Wrote {args.detail}")
    print(f"Wrote {args.summary}")


if __name__ == "__main__":
    main()
