from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .universe_source import _env, r2_client, read_json

DEFAULT_POINTER_KEY = "fundamentals/current.json"


def pull_current_snapshot(*, output_dir: Path, pointer_key: str = DEFAULT_POINTER_KEY, client=None, bucket: str | None = None) -> dict[str, Any]:
    client = client or r2_client()
    bucket = bucket or _env("R2_BUCKET_NAME")
    pointer = read_json(client, bucket, pointer_key)
    manifest_key = pointer.get("manifest_key")
    if not manifest_key:
        raise ValueError(f"Pointer {pointer_key} has no manifest_key")
    manifest = read_json(client, bucket, manifest_key)
    artifacts = manifest.get("artifacts", {})
    if not artifacts:
        raise ValueError(f"Manifest {manifest_key} has no artifacts")

    output_dir.mkdir(parents=True, exist_ok=True)
    for name, meta in artifacts.items():
        key = meta.get("key")
        if not key:
            raise ValueError(f"Artifact {name} has no key")
        client.download_file(bucket, key, str(output_dir / name))

    (output_dir / "fundamentals_current_pointer.json").write_text(
        json.dumps(pointer, indent=2, sort_keys=True), encoding="utf-8"
    )
    (output_dir / "fundamentals_snapshot_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return pointer


def main() -> None:
    p = argparse.ArgumentParser(description="Download the current immutable fundamentals snapshot from R2")
    p.add_argument("--output-dir", type=Path, default=Path("data/baseline"))
    p.add_argument("--pointer-key", default=DEFAULT_POINTER_KEY)
    args = p.parse_args()
    pointer = pull_current_snapshot(output_dir=args.output_dir, pointer_key=args.pointer_key)
    print("=== CURRENT FUNDAMENTALS SNAPSHOT ===")
    print(f"Manifest: {pointer.get('manifest_key')}")
    print(f"Source run: {pointer.get('source_run_id')}")
    print(f"Ready: {pointer.get('production_ready')}/{pointer.get('requested_symbols')}")
    print(f"Wrote {args.output_dir}")


if __name__ == "__main__":
    main()
