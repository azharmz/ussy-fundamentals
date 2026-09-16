from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .universe_source import _env, r2_client

POINTER_KEY = 'institutional_sponsorship/current.json'
TARGETS = ('history/sponsorship_state_events.parquet', 'history/final_period_state.parquet')
CODECS = (('zstd', 3), ('zstd', 6), ('zstd', 9))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _semantic_fingerprint(table: pa.Table) -> str:
    # Arrow IPC serialization gives a deterministic fingerprint of schema + ordered values.
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return hashlib.sha256(sink.getvalue().to_pybytes()).hexdigest()


def main() -> None:
    client = r2_client(); bucket = _env('R2_BUCKET_NAME')
    pointer = json.loads(client.get_object(Bucket=bucket, Key=POINTER_KEY)['Body'].read())
    manifest = json.loads(client.get_object(Bucket=bucket, Key=pointer['manifest_key'])['Body'].read())
    report = {'pointer_snapshot_prefix': pointer['snapshot_prefix'], 'manifest_key': pointer['manifest_key'], 'targets': {}}
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for logical in TARGETS:
            meta = manifest['artifacts'][logical]; key = meta['key']; src = root / Path(logical).name
            client.download_file(bucket, key, str(src))
            t0=time.perf_counter(); table=pq.read_table(src); read_s=time.perf_counter()-t0
            fp=_semantic_fingerprint(table)
            entry={'source_key':key,'source_bytes':src.stat().st_size,'source_sha256':_sha256(src),'rows':table.num_rows,'columns':table.num_columns,'semantic_sha256':fp,'source_read_seconds':read_s,'candidates':[]}
            for codec, level in CODECS:
                out=root/f'{src.name}.{codec}{level}.parquet'
                t0=time.perf_counter(); pq.write_table(table,out,compression=codec,compression_level=level,use_dictionary=True); write_s=time.perf_counter()-t0
                t0=time.perf_counter(); reread=pq.read_table(out); reread_s=time.perf_counter()-t0
                same_schema=reread.schema.equals(table.schema, check_metadata=True); fp2=_semantic_fingerprint(reread)
                entry['candidates'].append({'codec':codec,'level':level,'bytes':out.stat().st_size,'ratio':out.stat().st_size/src.stat().st_size,'bytes_saved':src.stat().st_size-out.stat().st_size,'write_seconds':write_s,'read_seconds':reread_s,'schema_equal':same_schema,'semantic_sha256':fp2,'semantic_equal':same_schema and fp2==fp})
            report['targets'][logical]=entry
    print(json.dumps(report,indent=2,sort_keys=True))

if __name__ == '__main__': main()
