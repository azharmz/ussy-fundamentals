from __future__ import annotations

import hashlib
import json
import math
import tempfile
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
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


def _ipc_fingerprint(table: pa.Table) -> str:
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return hashlib.sha256(sink.getvalue().to_pybytes()).hexdigest()


def _column_diagnostics(left: pa.Table, right: pa.Table) -> dict:
    result = {}
    for name in left.column_names:
        a = left[name].combine_chunks(); b = right[name].combine_chunks()
        item = {'type': str(a.type), 'nulls_left': a.null_count, 'nulls_right': b.null_count}
        try:
            eq = pc.equal(a, b)
            # Null/null is semantically equal; fill null comparisons with equality of validity.
            valid_same = pc.equal(pc.is_null(a), pc.is_null(b))
            eq = pc.if_else(pc.is_null(eq), valid_same, eq)
            mismatches = int(pc.sum(pc.invert(eq)).as_py() or 0)
            item['value_mismatches'] = mismatches
        except Exception as exc:
            item['value_compare_error'] = f'{type(exc).__name__}: {exc}'
            item['python_equal'] = a.to_pylist() == b.to_pylist()
        # NaN counts explain equality/fingerprint surprises without treating NaN as corruption.
        if pa.types.is_floating(a.type):
            item['nan_left'] = int(pc.sum(pc.fill_null(pc.is_nan(a), False)).as_py() or 0)
            item['nan_right'] = int(pc.sum(pc.fill_null(pc.is_nan(b), False)).as_py() or 0)
        result[name] = item
    return result


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
            fp=_ipc_fingerprint(table)
            entry={'source_key':key,'source_bytes':src.stat().st_size,'source_sha256':_sha256(src),'rows':table.num_rows,'columns':table.num_columns,'ipc_sha256':fp,'source_read_seconds':read_s,'candidates':[]}
            for codec, level in CODECS:
                out=root/f'{src.name}.{codec}{level}.parquet'
                t0=time.perf_counter(); pq.write_table(table,out,compression=codec,compression_level=level,use_dictionary=True); write_s=time.perf_counter()-t0
                t0=time.perf_counter(); reread=pq.read_table(out); reread_s=time.perf_counter()-t0
                same_schema=reread.schema.equals(table.schema, check_metadata=True); fp2=_ipc_fingerprint(reread)
                diag = _column_diagnostics(table, reread)
                values_equal = same_schema and all(x.get('value_mismatches', 0) == 0 and x.get('python_equal', True) for x in diag.values())
                entry['candidates'].append({'codec':codec,'level':level,'bytes':out.stat().st_size,'ratio':out.stat().st_size/src.stat().st_size,'bytes_saved':src.stat().st_size-out.stat().st_size,'write_seconds':write_s,'read_seconds':reread_s,'schema_equal':same_schema,'ipc_sha256':fp2,'ipc_equal':fp2==fp,'values_equal':values_equal,'column_diagnostics':diag if not (fp2==fp) else {}})
            report['targets'][logical]=entry
    print(json.dumps(report,indent=2,sort_keys=True))

if __name__ == '__main__': main()
