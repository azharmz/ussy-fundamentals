from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .reconcile import DOMESTIC_FORMS
from .sec_client import SEC_DATA, SecClient, ticker_mapping


def _latest_baseline_acceptance(long_df: pd.DataFrame) -> dict[str, pd.Timestamp]:
    if long_df.empty or "symbol" not in long_df.columns or "accepted_at" not in long_df.columns:
        return {}
    df = long_df[["symbol", "accepted_at"]].copy()
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    df["accepted_at"] = pd.to_datetime(df["accepted_at"], utc=True, errors="coerce")
    return df.dropna(subset=["accepted_at"]).groupby("symbol")["accepted_at"].max().to_dict()


def _latest_supported_filing(client: SecClient, cik: str) -> pd.Timestamp | None:
    payload = client.get_json(f"{SEC_DATA}/submissions/CIK{cik}.json")
    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accepted = recent.get("acceptanceDateTime", [])
    filed = recent.get("filingDate", [])
    best: pd.Timestamp | None = None
    for i, form in enumerate(forms):
        if str(form).upper() not in DOMESTIC_FORMS:
            continue
        raw = accepted[i] if i < len(accepted) and accepted[i] else (filed[i] if i < len(filed) else None)
        if not raw:
            continue
        ts = pd.to_datetime(raw, utc=True, errors="coerce")
        if pd.isna(ts):
            continue
        if best is None or ts > best:
            best = ts
    return best


def build_incremental_plan(
    *,
    current_universe: pd.DataFrame,
    baseline_universe: pd.DataFrame,
    baseline_manifest: pd.DataFrame,
    baseline_long: pd.DataFrame,
    client: SecClient,
    mapping_cache: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    cur = current_universe.copy()
    old = baseline_universe.copy()
    for df in (cur, old):
        df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()

    cur_symbols = set(cur["symbol"])
    old_symbols = set(old["symbol"])
    added = sorted(cur_symbols - old_symbols)
    removed = sorted(old_symbols - cur_symbols)

    baseline_manifest = baseline_manifest.copy()
    baseline_manifest["symbol"] = baseline_manifest["symbol"].astype(str).str.upper().str.strip()
    cik_by_symbol = {
        row.symbol: str(row.cik).zfill(10)
        for row in baseline_manifest.itertuples()
        if pd.notna(row.cik) and str(row.cik).strip()
    }

    mapping = ticker_mapping(client, mapping_cache)
    mapping_dict = dict(zip(mapping["symbol"], mapping["cik"]))
    latest_baseline = _latest_baseline_acceptance(baseline_long)

    impacted: list[dict[str, Any]] = []
    new_filing_symbols: list[str] = []
    unresolved_scan: list[str] = []

    for symbol in sorted(cur_symbols):
        reason: str | None = None
        cik = cik_by_symbol.get(symbol) or mapping_dict.get(symbol)
        if symbol in added:
            reason = "UNIVERSE_ADDED"
        if cik:
            latest_sec = _latest_supported_filing(client, cik)
            baseline_ts = latest_baseline.get(symbol)
            if latest_sec is not None and (baseline_ts is None or latest_sec > baseline_ts):
                reason = "NEW_SEC_FILING" if reason is None else f"{reason}+NEW_SEC_FILING"
                new_filing_symbols.append(symbol)
        elif symbol in added:
            unresolved_scan.append(symbol)

        if reason:
            row = cur.loc[cur["symbol"].eq(symbol)].iloc[0].to_dict()
            row["incremental_reason"] = reason
            row["resolved_cik"] = cik
            impacted.append(row)

    plan = pd.DataFrame(impacted)
    summary = {
        "current_symbols": len(cur_symbols),
        "baseline_symbols": len(old_symbols),
        "added_symbols": added,
        "removed_symbols": removed,
        "new_sec_filing_symbols": sorted(set(new_filing_symbols)),
        "unresolved_added_symbols": unresolved_scan,
        "impacted_symbols": int(len(plan)),
        "needs_update": bool(len(plan) or removed),
    }
    return plan, summary


def main() -> None:
    p = argparse.ArgumentParser(description="Plan an incremental USSY fundamentals update")
    p.add_argument("--current-universe", type=Path, default=Path("data/processed/current_universe.csv"))
    p.add_argument("--baseline-universe", type=Path, default=Path("data/baseline/current_universe.csv"))
    p.add_argument("--baseline-manifest", type=Path, default=Path("data/baseline/fundamentals_run_manifest.parquet"))
    p.add_argument("--baseline-long", type=Path, default=Path("data/baseline/fundamentals_point_in_time_long.parquet"))
    p.add_argument("--output", type=Path, default=Path("data/incremental/impacted_universe.csv"))
    p.add_argument("--summary", type=Path, default=Path("data/incremental/plan_summary.json"))
    args = p.parse_args()

    current = pd.read_csv(args.current_universe, dtype=str)
    baseline_universe = pd.read_csv(args.baseline_universe, dtype=str)
    baseline_manifest = pd.read_parquet(args.baseline_manifest)
    baseline_long = pd.read_parquet(args.baseline_long)
    client = SecClient()
    plan, summary = build_incremental_plan(
        current_universe=current,
        baseline_universe=baseline_universe,
        baseline_manifest=baseline_manifest,
        baseline_long=baseline_long,
        client=client,
        mapping_cache=Path("data/incremental/sec_mapping"),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if plan.empty:
        current.head(0).to_csv(args.output, index=False)
    else:
        plan.to_csv(args.output, index=False)
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("=== INCREMENTAL FUNDAMENTALS PLAN ===")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"Wrote {args.output}")
    print(f"Wrote {args.summary}")


if __name__ == "__main__":
    main()
