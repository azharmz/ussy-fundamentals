import io
import json
from pathlib import Path

import pytest

from ussy_fundamentals.sec_13f_publish import publish


class FakeClient:
    def __init__(self):
        self.uploads = []
        self.objects = []
        self.store = {}
    def upload_file(self, path, bucket, key, ExtraArgs=None):
        self.uploads.append((path, bucket, key, ExtraArgs))
        self.store[key] = Path(path).read_bytes()
    def put_object(self, **kwargs):
        self.objects.append(kwargs)
        body = kwargs.get('Body', b'')
        if isinstance(body, str):
            body = body.encode()
        self.store[kwargs['Key']] = body
    def get_object(self, Bucket, Key):
        return {'Body': io.BytesIO(self.store[Key])}
    def list_objects_v2(self, Bucket, Prefix, **kwargs):
        contents = [{'Key': k, 'Size': len(v)} for k, v in self.store.items() if k.startswith(Prefix)]
        return {'Contents': contents, 'IsTruncated': False}
    def delete_object(self, Bucket, Key):
        self.store.pop(Key, None)


def _touch(root: Path, names):
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        p = root / name
        if name.endswith('.json'):
            if name == 'summary.json':
                p.write_text(json.dumps({
                    'data_gate_pass': True,
                    'accepted_at_complete_rate': 1.0,
                    'ambiguous_lineage_events': 0,
                    'deterministic_us_isin_count': 1010,
                    'non_us_isin_not_evaluable_count': 317,
                }))
            else:
                p.write_text('{}')
        else:
            p.write_bytes(b'x')


def roots(tmp_path):
    h, u, l = tmp_path/'h', tmp_path/'u', tmp_path/'l'
    _touch(h, ['sponsorship_state_events.parquet','final_period_state.parquet','summary.json'])
    _touch(u, ['uncertainty_state_events.parquet','current_uncertainty_state.parquet'])
    (u/'uncertainty_summary.json').write_text('{}')
    _touch(l, ['sponsorship_mapped.csv','amendment_lineage.csv','summary.json'])
    return h,u,l


def test_pointer_exposes_live_exact_accepted_at_state(tmp_path):
    h,u,l = roots(tmp_path)
    client = FakeClient()
    _, manifest, pointer = publish(
        history_root=h, uncertainty_root=u, live_root=l,
        history_run_id='h1', history_commit='hc', uncertainty_run_id='u1', uncertainty_commit='uc',
        live_run_id='l1', live_commit='lc', publisher_run_id='p1', publisher_commit='pc',
        bucket='bucket', client=client)
    assert manifest['schema_version'] == 2
    assert manifest['semantics']['live_availability'] == 'exact_edgar_accepted_at'
    assert manifest['semantics']['quarter_end_used_as_availability'] is False
    assert pointer['live_source_run_id'] == 'l1'
    assert pointer['live_availability'] == 'exact_edgar_accepted_at'
    assert pointer['live_sponsorship_mapped_key'].endswith('/live/sponsorship_mapped.csv')
    assert pointer['live_amendment_lineage_key'].endswith('/live/amendment_lineage.csv')
    assert client.objects[-1]['Key'] == 'institutional_sponsorship/current.json'


def test_live_gate_fails_closed(tmp_path):
    h,u,l = roots(tmp_path)
    s = json.loads((l/'summary.json').read_text())
    s['data_gate_pass'] = False
    (l/'summary.json').write_text(json.dumps(s))
    with pytest.raises(RuntimeError, match='data gate'):
        publish(history_root=h, uncertainty_root=u, live_root=l,
                history_run_id='h', history_commit='hc', uncertainty_run_id='u', uncertainty_commit='uc',
                live_run_id='l', live_commit='lc', publisher_run_id='p', publisher_commit='pc',
                bucket='b', client=FakeClient())


def test_ambiguous_live_lineage_fails_closed(tmp_path):
    h,u,l = roots(tmp_path)
    s = json.loads((l/'summary.json').read_text())
    s['ambiguous_lineage_events'] = 1
    (l/'summary.json').write_text(json.dumps(s))
    with pytest.raises(RuntimeError, match='unambiguous'):
        publish(history_root=h, uncertainty_root=u, live_root=l,
                history_run_id='h', history_commit='hc', uncertainty_run_id='u', uncertainty_commit='uc',
                live_run_id='l', live_commit='lc', publisher_run_id='p', publisher_commit='pc',
                bucket='b', client=FakeClient())
