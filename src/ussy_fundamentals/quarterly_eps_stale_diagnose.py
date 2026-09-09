from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .annual_fallback import DILUTED_SHARES_TAGS, NET_INCOME_TAGS
from .normalize import EPS_TAGS, _current_period_only, accession_index, classify_period, fact_rows
from .quarterly_eps_gap_diagnose import _eps_like_tags
from .sec_client import SecClient, companyfacts, submissions

TARGET_CLASS = "STANDARD_EPS_EVIDENCE_STALE"


def _current_direct(payload: dict, idx: dict[str, dict], tags: list[str], metric: str) -> pd.DataFrame:
    df = fact_rows(payload, tags, metric, idx)
    if df.empty:
        return df
    df["period_type"] = df["duration_days"].map(classify_period)
    df = df[df["form"].isin(["10-Q", "10-Q/A"]) & df["period_type"].eq("quarterly")].copy()
    return _current_period_only(df)


def _latest_end(df: pd.DataFrame) -> pd.Timestamp:
    if df.empty:
        return pd.NaT
    return pd.to_datetime(df["end"], errors="coerce").max()


def _latest_report(filing_rows: list[dict]) -> pd.Timestamp:
    vals = []
    for row in filing_rows:
        if row.get("form") not in {"10-Q", "10-Q/A"}:
            continue
        dt = pd.to_datetime(row.get("reportDate"), errors="coerce")
        if pd.notna(dt):
            vals.append(dt)
    return max(vals) if vals else pd.NaT


def _post_cutoff_count(df: pd.DataFrame, cutoff: pd.Timestamp) -> int:
    if df.empty or pd.isna(cutoff):
        return 0
    end = pd.to_datetime(df["end"], errors="coerce")
    return int(end.gt(cutoff).sum())


def _fallback_pairs_after(payload: dict, idx: dict[str, dict], cutoff: pd.Timestamp) -> int:
    ni = _current_direct(payload, idx, NET_INCOME_TAGS, "net_income")
    sh = _current_direct(payload, idx, DILUTED_SHARES_TAGS, "diluted_shares")
    if ni.empty or sh.empty or pd.isna(cutoff):
        return 0
    pairs = ni[["accession", "end"]].drop_duplicates().merge(
        sh[["accession", "end"]].drop_duplicates(), on=["accession", "end"], how="inner"
    )
    end = pd.to_datetime(pairs["end"], errors="coerce")
    return int(end.gt(cutoff).sum())


def classify_stale(*, recent_10q_after_standard: int, fallback_pairs_after_standard: int,
                   nonstandard_direct_after_standard: int) -> str:
    if recent_10q_after_standard <= 0:
        return "NO_10Q_AFTER_STANDARD_EPS"
    if nonstandard_direct_after_standard > 0:
        return "TAG_TRANSITION_CANDIDATE"
    if fallback_pairs_after_standard >= 2:
        return "STANDARD_EPS_STOPPED_FALLBACK_INPUTS_CONTINUE"
    return "STANDARD_EPS_STOPPED_NO_USABLE_REPLACEMENT"


def diagnose_symbol(symbol: str, cik: str, payload: dict, filing_rows: list[dict]) -> dict:
    idx = accession_index(filing_rows)
    std = _current_direct(payload, idx, EPS_TAGS, "eps")
    latest_std = _latest_end(std)
    latest_q = _latest_report(filing_rows)

    recent_10q_after = 0
    if pd.notna(latest_std):
        for row in filing_rows:
            if row.get("form") not in {"10-Q", "10-Q/A"}:
                continue
            dt = pd.to_datetime(row.get("reportDate"), errors="coerce")
            if pd.notna(dt) and dt > latest_std:
                recent_10q_after += 1

    fallback_after = _fallback_pairs_after(payload, idx, latest_std)

    nonstandard_after = 0
    nonstandard_tags = []
    for qualified in _eps_like_tags(payload):
        namespace, tag = qualified.split(":", 1)
        if tag in EPS_TAGS:
            continue
        df = _current_direct(payload, idx, [tag], "eps_probe")
        if not df.empty:
            df = df[df["taxonomy"].eq(namespace)].copy()
        n = _post_cutoff_count(df, latest_std)
        if n:
            nonstandard_after += n
            nonstandard_tags.append(f"{namespace}:{tag}")

    cls = classify_stale(
        recent_10q_after_standard=recent_10q_after,
        fallback_pairs_after_standard=fallback_after,
        nonstandard_direct_after_standard=nonstandard_after,
    )
    return {
        "symbol": symbol,
        "cik": cik,
        "diagnosis_class": cls,
        "latest_standard_eps_end": str(latest_std.date()) if pd.notna(latest_std) else None,
        "latest_10q_report_date": str(latest_q.date()) if pd.notna(latest_q) else None,
        "quarterly_filings_after_standard_eps": recent_10q_after,
        "fallback_pairs_after_standard_eps": fallback_after,
        "nonstandard_direct_periods_after_standard_eps": nonstandard_after,
        "nonstandard_tags_after_standard_eps": ";".join(sorted(set(nonstandard_tags))),
    }


def diagnose(input_path: Path, output: Path, summary_output: Path) -> tuple[pd.DataFrame, dict]:
    src = pd.read_csv(input_path, dtype={"cik": str})
    targets = src[src["diagnosis_class"].eq(TARGET_CLASS)].copy()
    client = SecClient()
    rows = []
    for _, row in targets.sort_values("symbol").iterrows():
        symbol = str(row["symbol"]).upper()
        cik = str(row["cik"]).zfill(10)
        print(f"Diagnose stale quarterly EPS: {symbol} CIK={cik}")
        filing_rows = submissions(client, cik, Path("data/quarterly_eps_stale_probe/submissions"))
        payload = companyfacts(client, cik, Path("data/quarterly_eps_stale_probe/companyfacts"))
        rows.append(diagnose_symbol(symbol, cik, payload, filing_rows))

    out = pd.DataFrame(rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output, index=False)
    counts = out["diagnosis_class"].value_counts().to_dict() if not out.empty else {}
    summary = {
        "target_symbols": int(len(out)),
        "diagnosis_class_counts": {str(k): int(v) for k, v in counts.items()},
        "policy": "Diagnostic only; do not derive quarterly EPS from net income/shares or expand tags without separate validation.",
    }
    summary_output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("=== STALE QUARTERLY EPS DIAGNOSIS ===")
    print(f"Target symbols: {len(out)}")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    print(out.to_csv(index=False))
    return out, summary


def main() -> None:
    p = argparse.ArgumentParser(description="Diagnose STANDARD_EPS_EVIDENCE_STALE symbols")
    p.add_argument("--input", type=Path, default=Path("data/processed/quarterly_eps_gap_diagnosis.csv"))
    p.add_argument("--output", type=Path, default=Path("data/processed/quarterly_eps_stale_diagnosis.csv"))
    p.add_argument("--summary", type=Path, default=Path("data/processed/quarterly_eps_stale_diagnosis_summary.json"))
    args = p.parse_args()
    diagnose(args.input, args.output, args.summary)


if __name__ == "__main__":
    main()
