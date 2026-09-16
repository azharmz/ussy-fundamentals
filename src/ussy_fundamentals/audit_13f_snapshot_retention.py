from __future__ import annotations

import json
from collections import defaultdict

from .universe_source import _env, r2_client

ROOT = "institutional_sponsorship/"
SNAP = ROOT + "snapshots/"
POINTER = ROOT + "current.json"


def main() -> None:
    client = r2_client(); bucket = _env("R2_BUCKET_NAME")
    pointer = json.loads(client.get_object(Bucket=bucket, Key=POINTER)["Body"].read())
    current_prefix = str(pointer.get("snapshot_prefix") or "").rstrip("/") + "/"
    current_manifest = str(pointer.get("manifest_key") or "")
    protected_keys = {v for k, v in pointer.items() if k.endswith("_key") and isinstance(v, str)}
    protected_keys.add(current_manifest)

    objects = []
    token = None
    while True:
        kw = {"Bucket": bucket, "Prefix": ROOT}
        if token: kw["ContinuationToken"] = token
        resp = client.list_objects_v2(**kw)
        objects.extend(resp.get("Contents", []))
        if not resp.get("IsTruncated"): break
        token = resp.get("NextContinuationToken")

    grouped = defaultdict(list)
    for obj in objects:
        key = obj["Key"]
        if not key.startswith(SNAP): continue
        rel = key[len(SNAP):]
        parts = rel.split("/")
        if len(parts) < 3 or not parts[1].startswith("run-"): continue
        prefix = f"{SNAP}{parts[0]}/{parts[1]}/"
        grouped[prefix].append(obj)

    snapshots = []
    for prefix, obs in sorted(grouped.items()):
        manifest_key = prefix + "manifest.json"
        manifest = None
        try:
            manifest = json.loads(client.get_object(Bucket=bucket, Key=manifest_key)["Body"].read())
        except Exception as exc:
            manifest_error = type(exc).__name__
        else:
            manifest_error = None
        keys = {o["Key"] for o in obs}
        pointer_refs = sorted(keys & protected_keys)
        snapshots.append({
            "prefix": prefix.rstrip("/"),
            "objects": len(obs),
            "bytes": sum(int(o.get("Size", 0)) for o in obs),
            "is_current_prefix": prefix == current_prefix,
            "pointer_referenced_keys": pointer_refs,
            "manifest_present": manifest_key in keys,
            "manifest_error": manifest_error,
            "publisher_run_id": manifest.get("publisher_run_id") if manifest else None,
            "produced_at": manifest.get("produced_at") if manifest else None,
            "history_source_run_id": manifest.get("history_source_run_id") if manifest else None,
            "uncertainty_source_run_id": manifest.get("uncertainty_source_run_id") if manifest else None,
            "live_source_run_id": manifest.get("live_source_run_id") if manifest else None,
            "deletion_candidate": prefix != current_prefix and not pointer_refs and manifest_key in keys,
        })

    out = {
        "audit": "13F_SNAPSHOT_RETENTION_READ_ONLY_V1",
        "pointer_key": POINTER,
        "current_prefix": current_prefix.rstrip("/"),
        "current_manifest": current_manifest,
        "snapshot_count": len(snapshots),
        "total_snapshot_bytes": sum(x["bytes"] for x in snapshots),
        "deletion_candidate_bytes": sum(x["bytes"] for x in snapshots if x["deletion_candidate"]),
        "snapshots": snapshots,
        "mutations_performed": False,
    }
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__": main()
