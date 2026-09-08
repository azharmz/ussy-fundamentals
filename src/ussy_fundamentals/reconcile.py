from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .sec_client import columnar_to_rows, load_json


DEFAULT_WIDE = Path("data/processed/fundamentals_point_in_time.parquet")
DEFAULT_OUTPUT = Path("data/processed/fundamentals_run_manifest.parquet")
DOMESTIC_FORMS = {"10-Q", "10-Q/A", "10-K", "10-K/A"}
FPI_FORMS = {"20-F", "20-F/A", "6-K", "6-K/A", "40-F", "40-F/A"}


def _ticker_mapping_from_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    payload = load_json(path)
    out: dict[str, str] = {}
    for value in payload.values():
        symbol = str(value.get("ticker", "")).upper().strip()
        cik = value.get("cik_str")
        if symbol and cik is not None:
            out[symbol] = str(cik).zfill(10)
    return out


def _cached_submission_rows(cik: str, submissions_dir: Path) -> list[dict]:
    main_path = submissions_dir / f"CIK{cik}.json"
    if not main_path.exists():
        return []

    main = load_json(main_path)
    rows = columnar_to_rows(main.get("filings", {}).get("recent", {}))
    for meta in main.get("filings", {}).get("files", []):
        name = meta.get("name")
        if not name:
            continue
        historical_path = submissions_dir / cik / name
        if historical_path.exists():
            rows.extend(columnar_to_rows(load_json(historical_path)))
    return rows


def classify_dropped_symbol(
    filing_rows: list[dict],
    companyfacts_payload: dict | None,
) -> tuple[str, str]:
    """Classify why a requested symbol produced no normalized rows.

    FPI/ADR form families are kept separate from generic normalization failures so a
    full-universe run cannot silently turn unsupported 20-F/6-K issuers into TAG_NOT_FOUND.
    """
    forms = sorted({str(row.get("form", "")).upper() for row in filing_rows if row.get("form")})
    form_set = set(forms)
    forms_text = ",".join(forms)

    if not filing_rows:
        return "NO_SEC_FILINGS", forms_text
    if not (form_set & DOMESTIC_FORMS) and (form_set & FPI_FORMS):
        return "UNSUPPORTED_FPI", forms_text
    if form_set & DOMESTIC_FORMS:
        facts = (companyfacts_payload or {}).get("facts", {})
        if not facts:
            return "NO_SEC_FACTS", forms_text
        return "NORMALIZATION_EMPTY", forms_text
    return "NO_SUPPORTED_FILINGS", forms_text


def build_run_manifest(
    universe_path: Path,
    wide_path: Path = DEFAULT_WIDE,
    data_dir: Path = Path("data"),
) -> pd.DataFrame:
    universe = pd.read_csv(universe_path)
    if "symbol" not in universe.columns:
        raise ValueError(f"Universe has no symbol column: {universe_path}")
    requested = (
        universe["symbol"].astype(str).str.upper().str.strip().replace("", pd.NA).dropna().drop_duplicates()
    )

    if wide_path.exists():
        wide = pd.read_parquet(wide_path)
        normalized_counts = (
            wide.assign(symbol=wide["symbol"].astype(str).str.upper().str.strip())
            .groupby("symbol")
            .size()
            .to_dict()
        )
    else:
        normalized_counts = {}

    raw = data_dir / "raw"
    mapping = _ticker_mapping_from_cache(raw / "mapping" / "company_tickers.json")
    submissions_dir = raw / "submissions"
    companyfacts_dir = raw / "companyfacts"

    rows: list[dict] = []
    for symbol in requested:
        normalized_rows = int(normalized_counts.get(symbol, 0))
        cik = mapping.get(symbol)
        if normalized_rows:
            status = "NORMALIZED"
            forms_text = ""
        elif not cik:
            status = "CIK_NOT_FOUND"
            forms_text = ""
        else:
            filing_rows = _cached_submission_rows(cik, submissions_dir)
            facts_path = companyfacts_dir / f"CIK{cik}.json"
            facts_payload = load_json(facts_path) if facts_path.exists() else None
            status, forms_text = classify_dropped_symbol(filing_rows, facts_payload)

        rows.append(
            {
                "symbol": symbol,
                "cik": cik if cik else pd.NA,
                "status": status,
                "normalized_rows": normalized_rows,
                "forms_detected": forms_text,
            }
        )

    return pd.DataFrame(rows)


def print_reconciliation(manifest: pd.DataFrame) -> None:
    print("=== UNIVERSE RECONCILIATION ===")
    requested = len(manifest)
    normalized = int((manifest["status"] == "NORMALIZED").sum()) if not manifest.empty else 0
    dropped = requested - normalized
    print(f"Requested symbols: {requested:,}")
    print(f"Normalized symbols: {normalized:,}")
    print(f"Dropped symbols: {dropped:,}")

    if manifest.empty:
        print("No symbols in manifest")
        print()
        return

    print("\nStatus summary:")
    print(manifest["status"].value_counts().to_string())

    failures = manifest[manifest["status"] != "NORMALIZED"]
    if not failures.empty:
        print("\nDropped symbols:")
        print(failures[["symbol", "cik", "status", "forms_detected"]].to_string(index=False))
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile requested universe against normalized SEC output")
    parser.add_argument("--universe", required=True, type=Path)
    parser.add_argument("--wide", type=Path, default=DEFAULT_WIDE)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    manifest = build_run_manifest(args.universe, args.wide, args.data_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    manifest.to_parquet(args.output, index=False)
    print_reconciliation(manifest)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
