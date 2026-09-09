from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .annual_fallback import DILUTED_SHARES_TAGS, NET_INCOME_TAGS
from .normalize import EPS_TAGS
from .sec_client import SecClient

DEFAULT_INPUT = Path("data/processed/annual_lt3_verification.csv")
DEFAULT_DETAIL = Path("data/processed/annual_extraction_gap_diagnosis.csv")
DEFAULT_SUMMARY = Path("data/processed/annual_extraction_gap_diagnosis_summary.json")


def _annual_periods_for_tags(companyfacts: dict, tags: list[str]) -> set[str]:
    facts = companyfacts.get("facts", {})
    periods: set[str] = set()
    for namespace in facts.values():
        if not isinstance(namespace, dict):
            continue
        for tag in tags:
            concept = namespace.get(tag)
            if not concept:
                continue
            for entries in concept.get("units", {}).values():
                for fact in entries:
                    if fact.get("form") not in {"10-K", "10-K/A"}:
                        continue
                    start, end = fact.get("start"), fact.get("end")
                    if not start or not end:
                        continue
                    try:
                        days = (pd.Timestamp(end) - pd.Timestamp(start)).days
                    except Exception:
                        continue
                    if 300 <= days <= 430:
                        periods.add(str(end))
    return periods


def _paired_fallback_periods(companyfacts: dict) -> set[str]:
    income = _annual_periods_for_tags(companyfacts, NET_INCOME_TAGS)
    shares = _annual_periods_for_tags(companyfacts, DILUTED_SHARES_TAGS)
    return income & shares


def _semantic_hints(companyfacts: dict) -> list[str]:
    tags: list[str] = []
    for namespace in companyfacts.get("facts", {}).values():
        if isinstance(namespace, dict):
            tags.extend(str(tag) for tag in namespace)
    needles = (
        "LimitedPartner",
        "GeneralPartner",
        "PartnersCapital",
        "Partnership",
        "Royalty",
        "Trust",
        "PerUnit",
        "CommonUnit",
    )
    return sorted({tag for tag in tags if any(needle.lower() in tag.lower() for needle in needles)})[:25]


def classify_gap(usable_years: int, companyfacts: dict) -> tuple[str, dict]:
    eps_periods = _annual_periods_for_tags(companyfacts, EPS_TAGS)
    fallback_periods = _paired_fallback_periods(companyfacts)
    hints = _semantic_hints(companyfacts)

    if len(eps_periods) >= 3:
        cls = "STANDARD_EPS_HISTORY_AVAILABLE"
    elif len(fallback_periods) >= 3:
        cls = "FALLBACK_INPUT_HISTORY_AVAILABLE"
    elif hints:
        cls = "NONSTANDARD_ENTITY_EPS_SEMANTICS"
    else:
        cls = "NO_USABLE_EPS_EVIDENCE"

    return cls, {
        "standard_eps_annual_periods": len(eps_periods),
        "fallback_pair_annual_periods": len(fallback_periods),
        "first_standard_eps_period": min(eps_periods) if eps_periods else pd.NA,
        "latest_standard_eps_period": max(eps_periods) if eps_periods else pd.NA,
        "first_fallback_pair_period": min(fallback_periods) if fallback_periods else pd.NA,
        "latest_fallback_pair_period": max(fallback_periods) if fallback_periods else pd.NA,
        "semantic_hint_tags": ";".join(hints),
        "usable_annual_years": int(usable_years),
    }


def diagnose(input_df: pd.DataFrame, client: SecClient | None = None) -> tuple[pd.DataFrame, dict]:
    target = input_df[input_df["verification_class"].eq("DOMESTIC_HISTORY_EXTRACTION_GAP")].copy()
    client = client or SecClient()
    rows: list[dict] = []

    for _, row in target.iterrows():
        symbol = str(row["symbol"]).upper()
        cik = str(int(row["cik"])).zfill(10) if pd.notna(row["cik"]) else ""
        print(f"Diagnose annual extraction gap: {symbol} CIK={cik}")
        payload = client.get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
        cls, stats = classify_gap(int(row.get("usable_annual_years", 0)), payload)
        rows.append({
            "symbol": symbol,
            "cik": cik,
            "diagnosis_class": cls,
            "domestic_annual_filings": int(row.get("domestic_annual_filings", 0)),
            **stats,
        })

    detail = pd.DataFrame(rows)
    counts = detail["diagnosis_class"].value_counts().to_dict() if not detail.empty else {}
    summary = {
        "target_symbols": int(len(detail)),
        "diagnosis_class_counts": {str(k): int(v) for k, v in counts.items()},
        "interpretation": {
            "STANDARD_EPS_HISTORY_AVAILABLE": "SEC companyfacts has >=3 standard annual EPS periods; prioritize normalizer/source-selection diagnosis.",
            "FALLBACK_INPUT_HISTORY_AVAILABLE": "SEC has >=3 annual net-income/share pairs; prioritize annual fallback diagnosis.",
            "NONSTANDARD_ENTITY_EPS_SEMANTICS": "Standard EPS history is insufficient and companyfacts contains partnership/trust/unit semantic hints; review entity semantics before changing parser.",
            "NO_USABLE_EPS_EVIDENCE": "Neither standard EPS nor existing fallback inputs provide >=3 annual periods; do not invent EPS without new evidence.",
        },
    }
    return detail.sort_values(["diagnosis_class", "symbol"]), summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose domestic ANNUAL_LT_3Y extraction gaps by evidence class")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--detail", type=Path, default=DEFAULT_DETAIL)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()

    source = pd.read_csv(args.input)
    detail, summary = diagnose(source)
    args.detail.parent.mkdir(parents=True, exist_ok=True)
    detail.to_csv(args.detail, index=False)
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print("=== ANNUAL EXTRACTION GAP DIAGNOSIS ===")
    print(f"Target symbols: {summary['target_symbols']}")
    for key, value in summary["diagnosis_class_counts"].items():
        print(f"  {key}: {value}")
    print(f"Wrote {args.detail}")
    print(f"Wrote {args.summary}")


if __name__ == "__main__":
    main()
