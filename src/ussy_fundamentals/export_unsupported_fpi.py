from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import pandas as pd

from .universe_source import _env, r2_client


DEFAULT_POINTER_KEY = "fundamentals/current.json"
DEFAULT_OUTPUT_CSV = Path("data/processed/unsupported_fpi.csv")
DEFAULT_OUTPUT_JSON = Path("data/processed/unsupported_fpi.json")


def export_unsupported_fpi(
    *,
    pointer_key: str = DEFAULT_POINTER_KEY,
    output_csv: Path = DEFAULT_OUTPUT_CSV,
    output_json: Path = DEFAULT_OUTPUT_JSON,
    bucket: str | None = None,
    client=None,
) -> tuple[pd.DataFrame, dict]:
    client = client or r2_client()
    bucket = bucket or _env("R2_BUCKET_NAME")

    pointer_obj = client.get_object(Bucket=bucket, Key=pointer_key)
    pointer = json.loads(pointer_obj["Body"].read().decode("utf-8"))

    if pointer.get("status") != "READY":
        raise RuntimeError(f"Current fundamentals pointer is not READY: {pointer.get('status')!r}")

    report_key = pointer.get("final_report_key")
    if not report_key:
        raise RuntimeError("Current fundamentals pointer has no final_report_key")

    report_obj = client.get_object(Bucket=bucket, Key=report_key)
    report = pd.read_csv(io.BytesIO(report_obj["Body"].read()))

    required = {"symbol", "production_status"}
    missing = required - set(report.columns)
    if missing:
        raise RuntimeError(f"Final production report missing required columns: {sorted(missing)}")

    rows = report.loc[
        report["production_status"].astype(str).eq("UNSUPPORTED_FPI")
    ].copy()
    rows["symbol"] = rows["symbol"].astype(str).str.upper().str.strip()
    rows = rows.drop_duplicates("symbol").sort_values("symbol").reset_index(drop=True)

    keep = [
        c
        for c in [
            "symbol",
            "cik",
            "production_status",
            "final_bucket",
            "exception_family",
            "exception_class",
            "failure_class",
        ]
        if c in rows.columns
    ]
    rows = rows[keep]

    lineage = {
        "schema_version": 1,
        "type": "unsupported_fpi_export",
        "source_pointer_key": pointer_key,
        "source_pointer_updated_at": pointer.get("updated_at"),
        "source_run_id": pointer.get("source_run_id"),
        "source_commit": pointer.get("source_commit"),
        "source_final_report_key": report_key,
        "count": int(len(rows)),
        "symbols": rows["symbol"].tolist(),
    }

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    rows.to_csv(output_csv, index=False)
    output_json.write_text(json.dumps(lineage, indent=2, sort_keys=True), encoding="utf-8")
    return rows, lineage


def main() -> None:
    p = argparse.ArgumentParser(description="Export canonical UNSUPPORTED_FPI symbols from the current R2 fundamentals snapshot")
    p.add_argument("--pointer-key", default=DEFAULT_POINTER_KEY)
    p.add_argument("--output-csv", type=Path, default=DEFAULT_OUTPUT_CSV)
    p.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    args = p.parse_args()

    rows, lineage = export_unsupported_fpi(
        pointer_key=args.pointer_key,
        output_csv=args.output_csv,
        output_json=args.output_json,
    )

    print("=== UNSUPPORTED FPI EXPORT ===")
    print(f"Source report: {lineage['source_final_report_key']}")
    print(f"Source run: {lineage.get('source_run_id')}")
    print(f"Count: {len(rows):,}")
    print(f"Wrote: {args.output_csv}")
    print(f"Wrote: {args.output_json}")


if __name__ == "__main__":
    main()
