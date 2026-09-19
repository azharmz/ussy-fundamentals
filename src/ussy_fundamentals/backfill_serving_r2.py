from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path

import pandas as pd

from .serving_projection import build_serving_projection
from .universe_source import _env, r2_client


DEFAULT_POINTER_KEY = "fundamentals/current.json"


def backfill_serving_projection(
    *,
    pointer_key: str = DEFAULT_POINTER_KEY,
    bucket: str | None = None,
    client=None,
) -> tuple[str, dict]:
    """Build a web-serving projection from the already-published immutable snapshot.

    This deliberately reuses the current R2 production checkpoint; it does not rerun
    SEC acquisition or normalization.
    """
    client = client or r2_client()
    bucket = bucket or _env("R2_BUCKET_NAME")

    pointer_obj = client.get_object(Bucket=bucket, Key=pointer_key)
    pointer = json.loads(pointer_obj["Body"].read().decode("utf-8"))
    if pointer.get("status") != "READY":
        raise RuntimeError(f"Current fundamentals pointer is not READY: {pointer.get('status')!r}")

    required_keys = ["point_in_time_key", "final_report_key"]
    missing = [k for k in required_keys if not pointer.get(k)]
    if missing:
        raise RuntimeError(f"Current pointer missing required keys: {missing}")

    universe_key = pointer.get("universe_membership_key")
    if not universe_key:
        raise RuntimeError("Current pointer missing universe_membership_key")

    wide_obj = client.get_object(Bucket=bucket, Key=pointer["point_in_time_key"])
    wide = pd.read_parquet(io.BytesIO(wide_obj["Body"].read()))

    report_obj = client.get_object(Bucket=bucket, Key=pointer["final_report_key"])
    report = pd.read_csv(io.BytesIO(report_obj["Body"].read()))

    universe_obj = client.get_object(Bucket=bucket, Key=universe_key)
    universe_payload = json.loads(universe_obj["Body"].read().decode("utf-8"))
    records = universe_payload.get("records")
    if not isinstance(records, list):
        raise RuntimeError(f"Universe membership has no records array: {universe_key}")
    universe = pd.DataFrame(records)
    if "ticker" in universe.columns and "symbol" not in universe.columns:
        universe["symbol"] = universe["ticker"]

    payload = build_serving_projection(
        wide,
        report,
        universe,
        source_run_id=str(pointer.get("source_run_id") or ""),
        source_commit=pointer.get("source_commit"),
        universe_snapshot_date=pointer.get("universe_snapshot_date"),
    )
    payload["source_pointer_key"] = pointer_key
    payload["source_point_in_time_key"] = pointer["point_in_time_key"]
    payload["source_final_report_key"] = pointer["final_report_key"]

    prefix = pointer.get("snapshot_prefix")
    if not prefix:
        raise RuntimeError("Current pointer missing snapshot_prefix")
    serving_key = f"{prefix}/fundamentals_serving_current.json"
    body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    client.put_object(
        Bucket=bucket,
        Key=serving_key,
        Body=body,
        ContentType="application/json",
    )

    # Re-read before mutating the pointer. Fail closed if another producer advanced it.
    current_obj = client.get_object(Bucket=bucket, Key=pointer_key)
    current = json.loads(current_obj["Body"].read().decode("utf-8"))
    identity = ("source_run_id", "source_commit", "snapshot_prefix")
    if any(current.get(k) != pointer.get(k) for k in identity):
        raise RuntimeError("Current fundamentals pointer changed during backfill; refusing to overwrite")

    current["serving_current_key"] = serving_key
    client.put_object(
        Bucket=bucket,
        Key=pointer_key,
        Body=json.dumps(current, indent=2, sort_keys=True).encode("utf-8"),
        ContentType="application/json",
    )
    return serving_key, payload


def main() -> None:
    p = argparse.ArgumentParser(description="Backfill web-serving fundamentals projection from current R2 snapshot")
    p.add_argument("--pointer-key", default=DEFAULT_POINTER_KEY)
    args = p.parse_args()
    key, payload = backfill_serving_projection(pointer_key=args.pointer_key)
    print("=== FUNDAMENTALS SERVING BACKFILL ===")
    print(f"Serving key: {key}")
    print(f"Rows: {payload['row_count']:,}")
    print(f"Source run: {payload.get('source_run_id')}")


if __name__ == "__main__":
    main()
