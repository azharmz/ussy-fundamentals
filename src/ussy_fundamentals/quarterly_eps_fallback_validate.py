from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .annual_fallback import DILUTED_SHARES_TAGS, NET_INCOME_TAGS
from .normalize import EPS_TAGS, _current_period_only, accession_index, classify_period, fact_rows
from .sec_client import SecClient, companyfacts, submissions

TARGET_CLASS = "STANDARD_EPS_STOPPED_FALLBACK_INPUTS_CONTINUE"


def _direct_current(payload: dict, idx: dict[str, dict], tags: list[str], metric: str) -> pd.DataFrame:
    df = fact_rows(payload, tags, metric, idx)
    if df.empty:
        return df
    df["period_type"] = df["duration_days"].map(classify_period)
    df = df[df["form"].isin(["10-Q", "10-Q/A"]) & df["period_type"].eq("quarterly")].copy()
    return _current_period_only(df)


def _pick(df: pd.DataFrame):
    if df.empty:
        return None
    return df.sort_values(["tag_priority", "is_amendment"], ascending=[True, True]).iloc[0]


def compare_symbol(symbol: str, cik: str, payload: dict, filing_rows: list[dict]) -> pd.DataFrame:
    idx = accession_index(filing_rows)
    eps = _direct_current(payload, idx, EPS_TAGS, "eps")
    ni = _direct_current(payload, idx, NET_INCOME_TAGS, "net_income")
    sh = _direct_current(payload, idx, DILUTED_SHARES_TAGS, "diluted_shares")
    if eps.empty or ni.empty or sh.empty:
        return pd.DataFrame()

    rows = []
    keys = sorted(set(zip(eps["accession"], eps["end"])) & set(zip(ni["accession"], ni["end"])) & set(zip(sh["accession"], sh["end"])))
    for accn, end in keys:
        e = _pick(eps[(eps["accession"] == accn) & (eps["end"] == end)])
        n = _pick(ni[(ni["accession"] == accn) & (ni["end"] == end)])
        s = _pick(sh[(sh["accession"] == accn) & (sh["end"] == end)])
        if e is None or n is None or s is None or not s["value"]:
            continue
        reported = float(e["value"])
        derived = float(n["value"]) / float(s["value"])
        abs_error = abs(derived - reported)
        rel_error = abs_error / max(abs(reported), 0.01)
        rows.append({
            "symbol": symbol, "cik": cik, "fiscal_period_end": end,
            "accepted_at": e["accepted_at"], "accession": accn,
            "eps_tag": e["tag"], "net_income_tag": n["tag"], "shares_tag": s["tag"],
            "reported_eps": reported, "derived_eps": derived,
            "abs_error": abs_error, "relative_error": rel_error,
            "within_005": abs_error <= 0.05, "within_010": abs_error <= 0.10,
        })
    return pd.DataFrame(rows)


def _targets(diagnosis: pd.DataFrame) -> pd.DataFrame:
    if "diagnosis_class" not in diagnosis.columns:
        raise ValueError("diagnosis input must contain diagnosis_class")
    return diagnosis[diagnosis["diagnosis_class"].eq(TARGET_CLASS)].copy()


def validate(diagnosis_path: Path, output: Path, summary_output: Path) -> tuple[pd.DataFrame, dict]:
    diagnosis = pd.read_csv(diagnosis_path, dtype={"cik": str})
    targets = _targets(diagnosis)
    client = SecClient()
    parts = []
    target_symbols = sorted(targets["symbol"].astype(str).str.upper().tolist())
    for _, row in targets.sort_values("symbol").iterrows():
        symbol = str(row["symbol"]).upper()
        cik = str(row["cik"]).zfill(10)
        print(f"Validate stale quarterly EPS fallback: {symbol} CIK={cik}")
        filings = submissions(client, cik, Path("data/quarterly_eps_fallback_validate/submissions"))
        payload = companyfacts(client, cik, Path("data/quarterly_eps_fallback_validate/companyfacts"))
        part = compare_symbol(symbol, cik, payload, filings)
        if not part.empty:
            parts.append(part)

    out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output, index=False)

    summary = {"target_symbols": int(len(targets)), "target_symbol_list": target_symbols,
               "comparison_rows": int(len(out)), "symbols_with_overlap": int(out["symbol"].nunique()) if not out.empty else 0}
    if not out.empty:
        summary.update({
            "median_abs_error": float(out["abs_error"].median()),
            "p90_abs_error": float(out["abs_error"].quantile(0.90)),
            "within_005_rate": float(out["within_005"].mean()),
            "within_010_rate": float(out["within_010"].mean()),
            "by_symbol": {
                str(symbol): {
                    "rows": int(len(group)),
                    "median_abs_error": float(group["abs_error"].median()),
                    "p90_abs_error": float(group["abs_error"].quantile(0.90)),
                    "within_005_rate": float(group["within_005"].mean()),
                    "within_010_rate": float(group["within_010"].mean()),
                }
                for symbol, group in out.groupby("symbol")
            },
            "by_net_income_tag": {str(k): int(v) for k, v in out["net_income_tag"].value_counts().to_dict().items()},
        })
    summary["policy"] = "Validation only. Production fallback remains disabled until overlap evidence is reviewed."
    summary_output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("=== STALE QUARTERLY EPS FALLBACK VALIDATION ===")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"Wrote {output}")
    print(f"Wrote {summary_output}")
    return out, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate stale EPS fallback using historical reported-EPS overlap")
    parser.add_argument("--diagnosis", type=Path, default=Path("data/processed/quarterly_eps_stale_diagnosis.csv"))
    parser.add_argument("--output", type=Path, default=Path("data/processed/quarterly_eps_fallback_validation.csv"))
    parser.add_argument("--summary", type=Path, default=Path("data/processed/quarterly_eps_fallback_validation_summary.json"))
    args = parser.parse_args()
    validate(args.diagnosis, args.output, args.summary)


if __name__ == "__main__":
    main()
