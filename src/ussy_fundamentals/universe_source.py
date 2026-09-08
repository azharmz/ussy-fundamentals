from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import boto3
import pandas as pd


DEFAULT_POINTER_KEY = "universe/current.json"
DEFAULT_OUTPUT = Path("data/processed/current_universe.csv")
DEFAULT_METADATA_OUTPUT = Path("data/processed/current_universe_source.json")


def _env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Set {name}")
    return value


def r2_client():
    return boto3.client(
        "s3",
        endpoint_url=_env("R2_ENDPOINT"),
        aws_access_key_id=_env("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=_env("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
    )


def read_json(client, bucket: str, key: str) -> dict[str, Any]:
    response = client.get_object(Bucket=bucket, Key=key)
    body = response["Body"].read()
    return json.loads(body.decode("utf-8"))


def _pointer_identity(pointer: dict[str, Any]) -> tuple[Any, Any, Any]:
    return (
        pointer.get("snapshot_date"),
        pointer.get("membership_key"),
        pointer.get("security_master_key"),
    )


def eligible_membership(membership: dict[str, Any]) -> pd.DataFrame:
    records = membership.get("records", [])
    eligible = [
        row for row in records
        if row.get("security_id")
        and row.get("ticker")
        and row.get("sharia_compliance") == "COMPLIANT"
    ]
    out = pd.DataFrame(eligible)
    if out.empty:
        return pd.DataFrame(columns=["symbol", "security_id", "ticker"])

    out["ticker"] = out["ticker"].astype(str).str.upper().str.strip()
    out["security_id"] = out["security_id"].astype(str).str.strip()
    if out["security_id"].duplicated().any():
        dupes = sorted(out.loc[out["security_id"].duplicated(keep=False), "security_id"].unique())
        raise ValueError(f"Duplicate security_id in canonical membership: {dupes[:10]}")

    out.insert(0, "symbol", out["ticker"])
    return out


def fetch_current_universe(
    client=None,
    bucket: str | None = None,
    pointer_key: str = DEFAULT_POINTER_KEY,
    max_attempts: int = 3,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read a stable canonical current universe from ussy-data R2.

    The pointer is read before and after the immutable membership snapshot. If it moves
    during the read, retry so one fundamentals run cannot mix universe versions.
    """
    if client is None:
        client = r2_client()
    if bucket is None:
        bucket = _env("R2_BUCKET_NAME")

    for attempt in range(1, max_attempts + 1):
        pointer_before = read_json(client, bucket, pointer_key)
        membership_key = pointer_before.get("membership_key")
        if not membership_key:
            raise ValueError(f"Pointer {pointer_key} has no membership_key")

        membership = read_json(client, bucket, membership_key)
        pointer_after = read_json(client, bucket, pointer_key)

        if _pointer_identity(pointer_before) != _pointer_identity(pointer_after):
            if attempt == max_attempts:
                raise RuntimeError("Universe pointer changed during read; retry the run")
            continue

        universe = eligible_membership(membership)
        confirmed = pointer_before.get("confirmed_compliant")
        if confirmed is not None and len(universe) != int(confirmed):
            raise ValueError(
                f"Eligible universe count {len(universe)} != pointer confirmed_compliant {confirmed}"
            )

        snapshot_date = pointer_before.get("snapshot_date")
        membership_snapshot_date = membership.get("snapshot_date")
        if snapshot_date and membership_snapshot_date and snapshot_date != membership_snapshot_date:
            raise ValueError(
                f"Pointer snapshot_date {snapshot_date} != membership snapshot_date {membership_snapshot_date}"
            )

        metadata = {
            "pointer_key": pointer_key,
            "snapshot_date": snapshot_date,
            "membership_key": membership_key,
            "security_master_key": pointer_before.get("security_master_key"),
            "change_key": pointer_before.get("change_key"),
            "confirmed_compliant": int(confirmed) if confirmed is not None else len(universe),
            "eligible_count": len(universe),
        }
        return universe, metadata

    raise RuntimeError("Unable to read stable universe pointer")


def write_current_universe(
    output: Path = DEFAULT_OUTPUT,
    metadata_output: Path = DEFAULT_METADATA_OUTPUT,
    client=None,
    bucket: str | None = None,
    pointer_key: str = DEFAULT_POINTER_KEY,
) -> tuple[Path, Path, dict[str, Any]]:
    universe, metadata = fetch_current_universe(client=client, bucket=bucket, pointer_key=pointer_key)
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata_output.parent.mkdir(parents=True, exist_ok=True)
    universe.to_csv(output, index=False)
    metadata_output.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return output, metadata_output, metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch canonical current USSY universe from private R2")
    parser.add_argument("--pointer-key", default=DEFAULT_POINTER_KEY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--metadata-output", type=Path, default=DEFAULT_METADATA_OUTPUT)
    args = parser.parse_args()

    output, metadata_output, metadata = write_current_universe(
        output=args.output,
        metadata_output=args.metadata_output,
        pointer_key=args.pointer_key,
    )
    print("=== CURRENT UNIVERSE ===")
    print(f"Snapshot date: {metadata['snapshot_date']}")
    print(f"Membership key: {metadata['membership_key']}")
    print(f"Eligible symbols: {metadata['eligible_count']:,}")
    print(f"Wrote {output}")
    print(f"Wrote {metadata_output}")


if __name__ == "__main__":
    main()
