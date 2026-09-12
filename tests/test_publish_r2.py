import json
from pathlib import Path

from ussy_fundamentals.publish_r2 import DEFAULT_FILES, build_manifest, publish_snapshot


class FakeS3:
    def __init__(self):
        self.uploaded = []
        self.objects = []

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        self.uploaded.append((Path(filename).name, bucket, key, ExtraArgs or {}))

    def put_object(self, **kwargs):
        self.objects.append(kwargs)
        return {"ETag": "fake"}


def _seed(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name in DEFAULT_FILES:
        path = root / name
        if name == "fundamentals_final_production_summary.json":
            path.write_text(json.dumps({"requested_symbols": 1327, "ready_symbols": 901, "ready_pct": 67.9}), encoding="utf-8")
        elif name == "current_universe_source.json":
            path.write_text(json.dumps({
                "snapshot_date": "2026-08-28",
                "pointer_key": "universe/current.json",
                "membership_key": "universe/membership/2026-08-28.json",
                "security_master_key": "universe/security_master/2026-08-28.json",
                "confirmed_compliant": 1327,
            }), encoding="utf-8")
        else:
            path.write_bytes((name + "\n").encode("utf-8"))


def test_build_manifest_captures_provenance_and_checksums(tmp_path):
    _seed(tmp_path)
    manifest = build_manifest(
        root=tmp_path,
        prefix="fundamentals/snapshots/2026-09-09/run-123",
        source_run_id="123",
        source_commit="abc",
        produced_at="2026-09-09T10:00:00Z",
    )
    assert manifest["source_run_id"] == "123"
    assert manifest["source_commit"] == "abc"
    assert manifest["universe"]["snapshot_date"] == "2026-08-28"
    assert manifest["production"]["ready_symbols"] == 901
    assert len(manifest["artifacts"]) == len(DEFAULT_FILES)
    assert len(manifest["artifacts"]["fundamentals_point_in_time.parquet"]["sha256"]) == 64


def test_publish_writes_pointer_last(tmp_path):
    _seed(tmp_path)
    client = FakeS3()
    manifest_key, manifest, pointer = publish_snapshot(
        root=tmp_path,
        source_run_id="123",
        source_commit="abc",
        snapshot_date="2026-09-09",
        bucket="bucket",
        client=client,
    )

    assert len(client.uploaded) == len(DEFAULT_FILES)
    assert manifest_key == "fundamentals/snapshots/2026-09-09/run-123/manifest.json"
    assert [o["Key"] for o in client.objects] == [manifest_key, "fundamentals/current.json"]
    assert pointer["manifest_key"] == manifest_key
    assert pointer["production_ready"] == 901
    assert pointer["requested_symbols"] == 1327
    assert pointer["point_in_time_key"].endswith("fundamentals_point_in_time.parquet")
    assert manifest["status"] == "READY"
