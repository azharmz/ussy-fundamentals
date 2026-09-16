from __future__ import annotations

import json
import os

from .universe_source import _env, r2_client

ROOT = "institutional_sponsorship/"
POINTER = ROOT + "current.json"
TARGET = os.getenv("TARGET_SNAPSHOT_PREFIX", "").rstrip("/") + "/"
APPLY = os.getenv("APPLY_DELETE", "false").lower() == "true"


def list_prefix(client, bucket: str, prefix: str) -> list[dict]:
    out = []; token = None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix}
        if token: kw["ContinuationToken"] = token
        resp = client.list_objects_v2(**kw); out.extend(resp.get("Contents", []))
        if not resp.get("IsTruncated"): return out
        token = resp["NextContinuationToken"]


def pointer_state(client, bucket: str) -> tuple[dict, str, set[str]]:
    p = json.loads(client.get_object(Bucket=bucket, Key=POINTER)["Body"].read())
    current = str(p.get("snapshot_prefix") or "").rstrip("/") + "/"
    refs = {v for k, v in p.items() if k.endswith("_key") and isinstance(v, str)}
    return p, current, refs


def main() -> None:
    if not TARGET.startswith(ROOT + "snapshots/") or "/run-" not in TARGET:
        raise RuntimeError("TARGET_SNAPSHOT_PREFIX must be an exact institutional snapshot run prefix")
    client = r2_client(); bucket = _env("R2_BUCKET_NAME")
    _, current_before, refs_before = pointer_state(client, bucket)
    objects = list_prefix(client, bucket, TARGET)
    keys = {o["Key"] for o in objects}
    manifest = TARGET + "manifest.json"
    if not objects: raise RuntimeError("target prefix is empty/not found")
    if manifest not in keys: raise RuntimeError("target manifest missing; fail closed")
    if TARGET == current_before: raise RuntimeError("refusing to delete current snapshot")
    if keys & refs_before: raise RuntimeError("target contains pointer-referenced keys")

    plan = {"target": TARGET.rstrip("/"), "objects": len(objects),
            "bytes": sum(int(o.get("Size", 0)) for o in objects),
            "current_prefix": current_before.rstrip("/"), "apply": APPLY}
    print(json.dumps({"pre_delete_plan": plan}, indent=2, sort_keys=True))
    if not APPLY: return

    # Re-read pointer immediately before mutation. Any movement/reference aborts deletion.
    _, current_now, refs_now = pointer_state(client, bucket)
    if current_now != current_before: raise RuntimeError("current pointer moved since preflight; abort")
    if TARGET == current_now or keys & refs_now: raise RuntimeError("target became protected; abort")

    for key in sorted(keys):
        client.delete_object(Bucket=bucket, Key=key)

    remaining = list_prefix(client, bucket, TARGET)
    _, current_after, refs_after = pointer_state(client, bucket)
    if remaining: raise RuntimeError(f"post-delete target not empty: {len(remaining)} objects remain")
    if current_after != current_before: raise RuntimeError("current pointer changed during cleanup")
    if not refs_after: raise RuntimeError("current pointer has no referenced artifact keys after cleanup")
    print(json.dumps({"cleanup_result": {**plan, "deleted_objects": len(keys),
        "deleted_bytes": plan["bytes"], "remaining_target_objects": 0,
        "current_pointer_unchanged": True}}, indent=2, sort_keys=True))


if __name__ == "__main__": main()
