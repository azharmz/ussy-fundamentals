from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .universe_source import _env, r2_client

DEFAULT_ROOT = Path("data/processed")
DEFAULT_POINTER_KEY = "fundamentals/current.json"
DEFAULT_FILES = [
    "fundamentals_point_in_time_long.parquet",
    "fundamentals_point_in_time.parquet",
    "fundamentals_run_manifest.parquet",
    "fundamentals_readiness_report.csv",
    "fundamentals_readiness_summary.json",
    "fundamentals_exception_taxonomy.csv",
    "fundamentals_exception_taxonomy_summary.json",
    "fundamentals_final_production_report.csv",
    "fundamentals_final_production_summary.json",
    "current_universe.csv",
    "current_universe_source.json",
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _content_type(path: Path) -> str:
    if path.suffix == ".parquet":
        return "application/vnd.apache.parquet"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def build_manifest(
    *,
    root: Path,
    prefix: str,
    source_run_id: str,
    source_commit: str,
    produced_at: str,
    filenames: list[str] | None = None,
) -> dict[str, Any]:
    filenames = filenames or DEFAULT_FILES
    missing = [name for name in filenames if not (root / name).exists()]
    if missing:
        raise FileNotFoundError(f"Missing production files: {missing}")

    summary = json.loads((root / "fundamentals_final_production_summary.json").read_text(encoding="utf-8"))
    universe = json.loads((root / "current_universe_source.json").read_text(encoding="utf-8"))

    artifacts: dict[str, Any] = {}
    for name in filenames:
        path = root / name
        artifacts[name] = {
            "key": f"{prefix}/{name}",
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }

    return {
        "schema_version": 1,
        "type": "fundamentals_production_snapshot",
        "status": "READY",
        "produced_at": produced_at,
        "source_run_id": str(source_run_id),
        "source_commit": source_commit,
        "snapshot_prefix": prefix,
        "universe": {
            "snapshot_date": universe.get("snapshot_date"),
            "pointer_key": universe.get("pointer_key"),
            "membership_key": universe.get("membership_key"),
            "security_master_key": universe.get("security_master_key"),
            "confirmed_compliant": universe.get("confirmed_compliant"),
        },
        "production": summary,
        "artifacts": artifacts,
    }


def publish_snapshot(
    *,
    root: Path = DEFAULT_ROOT,
    source_run_id: str,
    source_commit: str,
    snapshot_date: str | None = None,
    pointer_key: str = DEFAULT_POINTER_KEY,
    bucket: str | None = None,
    client=None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    client = client or r2_client()
    bucket = bucket or _env("R2_BUCKET_NAME")
    now = datetime.now(timezone.utc)
    produced_at = now.isoformat().replace("+00:00", "Z")
    snapshot_date = snapshot_date or now.date().isoformat()
    prefix = f"fundamentals/snapshots/{snapshot_date}/run-{source_run_id}"

    manifest = build_manifest(
        root=root,
        prefix=prefix,
        source_run_id=source_run_id,
        source_commit=source_commit,
        produced_at=produced_at,
    )

    # Publish immutable files first. The mutable current pointer is intentionally last.
    for name, meta in manifest["artifacts"].items():
        path = root / name
        client.upload_file(
            str(path),
            bucket,
            meta["key"],
            ExtraArgs={"ContentType": _content_type(path)},
        )

    manifest_key = f"{prefix}/manifest.json"
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    client.put_object(
        Bucket=bucket,
        Key=manifest_key,
        Body=manifest_bytes,
        ContentType="application/json",
    )

    production = manifest["production"]
    pointer = {
        "schema_version": 1,
        "type": "fundamentals_current_pointer",
        "status": "READY",
        "updated_at": produced_at,
        "snapshot_date": snapshot_date,
        "source_run_id": str(source_run_id),
        "source_commit": source_commit,
        "manifest_key": manifest_key,
        "snapshot_prefix": prefix,
        "universe_snapshot_date": manifest["universe"].get("snapshot_date"),
        "universe_membership_key": manifest["universe"].get("membership_key"),
        "requested_symbols": production.get("requested_symbols"),
        "production_ready": production.get("ready_symbols"),
        "ready_pct": production.get("ready_pct"),
        "point_in_time_key": manifest["artifacts"]["fundamentals_point_in_time.parquet"]["key"],
        "point_in_time_long_key": manifest["artifacts"]["fundamentals_point_in_time_long.parquet"]["key"],
        "readiness_report_key": manifest["artifacts"]["fundamentals_readiness_report.csv"]["key"],
        "final_report_key": manifest["artifacts"]["fundamentals_final_production_report.csv"]["key"],
    }
    client.put_object(
        Bucket=bucket,
        Key=pointer_key,
        Body=json.dumps(pointer, indent=2, sort_keys=True).encode("utf-8"),
        ContentType="application/json",
    )
    return manifest_key, manifest, pointer


def main() -> None:
    p = argparse.ArgumentParser(description="Publish an immutable production fundamentals snapshot to R2")
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--source-run-id", default=os.getenv("GITHUB_RUN_ID"))
    p.add_argument("--source-commit", default=os.getenv("GITHUB_SHA"))
    p.add_argument("--snapshot-date")
    p.add_argument("--pointer-key", default=DEFAULT_POINTER_KEY)
    args = p.parse_args()

    if not args.source_run_id:
        raise RuntimeError("Set --source-run-id or GITHUB_RUN_ID")
    if not args.source_commit:
        raise RuntimeError("Set --source-commit or GITHUB_SHA")

    manifest_key, _, pointer = publish_snapshot(
        root=args.root,
        source_run_id=str(args.source_run_id),
        source_commit=str(args.source_commit),
        snapshot_date=args.snapshot_date,
        pointer_key=args.pointer_key,
    )
    print("=== R2 FUNDAMENTALS PUBLISH ===")
    print(f"Manifest: {manifest_key}")
    print(f"Current pointer: {args.pointer_key}")
    print(f"Ready: {pointer.get('production_ready')}/{pointer.get('requested_symbols')} ({pointer.get('ready_pct')}%)")


if __name__ == "__main__":
    main()
