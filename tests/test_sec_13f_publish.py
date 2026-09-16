import gzip
import io
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ussy_fundamentals.sec_13f_publish import _apply_retention, publish

class FakeClient:
    def __init__(self): self.uploads=[]; self.objects=[]; self.store={}
    def upload_file(self,path,bucket,key,ExtraArgs=None): self.uploads.append((path,bucket,key,ExtraArgs)); self.store[key]=Path(path).read_bytes()
    def put_object(self,**kwargs):
        self.objects.append(kwargs); body=kwargs.get('Body',b''); body=body.encode() if isinstance(body,str) else body; self.store[kwargs['Key']]=body
    def get_object(self,Bucket,Key): return {'Body':io.BytesIO(self.store[Key])}
    def list_objects_v2(self,Bucket,Prefix,**kwargs): return {'Contents':[{'Key':k,'Size':len(v)} for k,v in self.store.items() if k.startswith(Prefix)],'IsTruncated':False}
    def delete_object(self,Bucket,Key): self.store.pop(Key,None)
class FailCompressedPutClient(FakeClient):
    def put_object(self,**kwargs):
        if str(kwargs.get('Key','')).endswith('.gz'): raise RuntimeError('simulated compressed upload failure')
        super().put_object(**kwargs)
class FailParquetPutClient(FakeClient):
    def put_object(self,**kwargs):
        if str(kwargs.get('Key','')).endswith('sponsorship_state_events.parquet'): raise RuntimeError('simulated parquet upload failure')
        super().put_object(**kwargs)
def _touch(root:Path,names):
    root.mkdir(parents=True,exist_ok=True)
    for name in names:
        p=root/name
        if name.endswith('.json'): p.write_text(json.dumps({'data_gate_pass':True,'accepted_at_complete_rate':1.0,'ambiguous_lineage_events':0,'deterministic_us_isin_count':1010,'non_us_isin_not_evaluable_count':317}) if name=='summary.json' else '{}')
        elif name.endswith('.parquet'): pq.write_table(pa.table({'id':[1,2,3],'value':[1.5,None,3.25]}),p,compression='snappy')
        else: p.write_bytes(b'x')
def roots(tmp_path):
    h,u,l=tmp_path/'h',tmp_path/'u',tmp_path/'l'; _touch(h,['sponsorship_state_events.parquet','final_period_state.parquet','summary.json']); _touch(u,['uncertainty_state_events.parquet','current_uncertainty_state.parquet']); (u/'uncertainty_summary.json').write_text('{}'); _touch(l,['sponsorship_mapped.csv','amendment_lineage.csv','summary.json']); return h,u,l
def _publish(tmp_path,client=None):
    h,u,l=roots(tmp_path); client=client or FakeClient(); result=publish(history_root=h,uncertainty_root=u,live_root=l,history_run_id='h1',history_commit='hc',uncertainty_run_id='u1',uncertainty_commit='uc',live_run_id='l1',live_commit='lc',publisher_run_id='p1',publisher_commit='pc',bucket='bucket',client=client); return h,u,l,client,result
def test_pointer_exposes_live_exact_accepted_at_state(tmp_path):
    _,_,_,client,(_,manifest,pointer)=_publish(tmp_path); assert manifest['schema_version']==2; assert manifest['semantics']['live_availability']=='exact_edgar_accepted_at'; assert manifest['semantics']['quarter_end_used_as_availability'] is False; assert pointer['live_source_run_id']=='l1'; assert pointer['storage_contract']=='lossless-gzip-v1-selective+parquet-zstd9-v1'; assert client.objects[-1]['Key']=='institutional_sponsorship/current.json'
def test_history_parquet_is_zstd9_semantically_equal_and_manifested(tmp_path):
    h,_,_,client,(_,manifest,_)=_publish(tmp_path)
    for logical in ('history/sponsorship_state_events.parquet','history/final_period_state.parquet'):
        meta=manifest['artifacts'][logical]; assert meta['representation']=='parquet-zstd'; assert meta['compression']=='zstd'; assert meta['compression_level']==9; assert meta['semantic_verification']=='full_schema_and_value_equality'; assert meta['key'].endswith('.parquet')
        stored=client.store[meta['key']]; target=tmp_path/'roundtrip.parquet'; target.write_bytes(stored); assert pq.read_table(target).equals(pq.read_table(h/Path(logical).name))
def test_selective_gzip_is_deterministic_lossless_and_manifested(tmp_path):
    h,u,l=roots(tmp_path); payload=('manager,period,value\n'+'0001,2026Q2,123456\n'*500).encode(); (l/'filings.csv').write_bytes(payload); client=FakeClient(); _,manifest,pointer=publish(history_root=h,uncertainty_root=u,live_root=l,history_run_id='h',history_commit='hc',uncertainty_run_id='u',uncertainty_commit='uc',live_run_id='l',live_commit='lc',publisher_run_id='p',publisher_commit='pc',bucket='b',client=client); meta=manifest['artifacts']['live/filings.csv']; assert meta['representation']=='gzip'; assert gzip.decompress(client.store[meta['key']])==payload; assert pointer['live_sponsorship_mapped_key'].endswith('/live/sponsorship_mapped.csv')
def test_failed_compressed_upload_never_moves_current_pointer(tmp_path):
    h,u,l=roots(tmp_path); (l/'filings.csv').write_bytes(b'a,b\n1,2\n'*1000); client=FailCompressedPutClient(); old={'snapshot_prefix':'institutional_sponsorship/snapshots/2026-09-14/run-old','manifest_key':'institutional_sponsorship/snapshots/2026-09-14/run-old/manifest.json'}; client.store['institutional_sponsorship/current.json']=json.dumps(old).encode()
    with pytest.raises(RuntimeError,match='compressed upload failure'): publish(history_root=h,uncertainty_root=u,live_root=l,history_run_id='h',history_commit='hc',uncertainty_run_id='u',uncertainty_commit='uc',live_run_id='l',live_commit='lc',publisher_run_id='p',publisher_commit='pc',bucket='b',client=client)
    assert json.loads(client.store['institutional_sponsorship/current.json'])==old
def test_failed_parquet_upload_never_moves_current_pointer(tmp_path):
    h,u,l=roots(tmp_path); client=FailParquetPutClient(); old={'snapshot_prefix':'institutional_sponsorship/snapshots/2026-09-14/run-old','manifest_key':'institutional_sponsorship/snapshots/2026-09-14/run-old/manifest.json'}; client.store['institutional_sponsorship/current.json']=json.dumps(old).encode()
    with pytest.raises(RuntimeError,match='parquet upload failure'): publish(history_root=h,uncertainty_root=u,live_root=l,history_run_id='h',history_commit='hc',uncertainty_run_id='u',uncertainty_commit='uc',live_run_id='l',live_commit='lc',publisher_run_id='p',publisher_commit='pc',bucket='b',client=client)
    assert json.loads(client.store['institutional_sponsorship/current.json'])==old
def test_retention_keeps_current_and_previous_distinct_date_and_rejects_lt_two():
    c=FakeClient(); root='institutional_sponsorship/snapshots/'; current=f'{root}2026-09-16/run-3'; previous=f'{root}2026-09-14/run-2'; old=f'{root}2026-09-12/run-1'
    for p in (current,previous,old): c.store[p+'/manifest.json']=b'{}'; c.store[p+'/data.bin']=b'x'
    ptr={'snapshot_prefix':current,'manifest_key':current+'/manifest.json','history_state_events_key':current+'/data.bin'}; c.store['institutional_sponsorship/current.json']=json.dumps(ptr).encode()
    with pytest.raises(RuntimeError,match='at least two'): _apply_retention(client=c,bucket='b',pointer_key='institutional_sponsorship/current.json',retain_dates=1)
    result=_apply_retention(client=c,bucket='b',pointer_key='institutional_sponsorship/current.json',retain_dates=2); assert result['kept_dates']==['2026-09-14','2026-09-16']; assert not any(k.startswith(old+'/') for k in c.store); assert json.loads(c.store['institutional_sponsorship/current.json'])==ptr
def test_retention_removes_superseded_run_on_current_date():
    c=FakeClient(); root='institutional_sponsorship/snapshots/'; current=f'{root}2026-09-16/run-200'; superseded=f'{root}2026-09-16/run-100'; previous=f'{root}2026-09-14/run-50'
    for p in (current,superseded,previous): c.store[p+'/manifest.json']=b'{}'; c.store[p+'/data.bin']=b'x'
    ptr={'snapshot_prefix':current,'manifest_key':current+'/manifest.json','history_state_events_key':current+'/data.bin'}; c.store['institutional_sponsorship/current.json']=json.dumps(ptr).encode()
    result=_apply_retention(client=c,bucket='b',pointer_key='institutional_sponsorship/current.json',retain_dates=2)
    assert result['same_date_deleted_prefixes']==[superseded]; assert result['single_run_current_date']=='2026-09-16'; assert not any(k.startswith(superseded+'/') for k in c.store); assert any(k.startswith(current+'/') for k in c.store); assert any(k.startswith(previous+'/') for k in c.store); assert json.loads(c.store['institutional_sponsorship/current.json'])==ptr
def test_live_gate_fails_closed(tmp_path):
    h,u,l=roots(tmp_path); s=json.loads((l/'summary.json').read_text()); s['data_gate_pass']=False; (l/'summary.json').write_text(json.dumps(s))
    with pytest.raises(RuntimeError,match='data gate'): publish(history_root=h,uncertainty_root=u,live_root=l,history_run_id='h',history_commit='hc',uncertainty_run_id='u',uncertainty_commit='uc',live_run_id='l',live_commit='lc',publisher_run_id='p',publisher_commit='pc',bucket='b',client=FakeClient())
def test_ambiguous_live_lineage_fails_closed(tmp_path):
    h,u,l=roots(tmp_path); s=json.loads((l/'summary.json').read_text()); s['ambiguous_lineage_events']=1; (l/'summary.json').write_text(json.dumps(s))
    with pytest.raises(RuntimeError,match='unambiguous'): publish(history_root=h,uncertainty_root=u,live_root=l,history_run_id='h',history_commit='hc',uncertainty_run_id='u',uncertainty_commit='uc',live_run_id='l',live_commit='lc',publisher_run_id='p',publisher_commit='pc',bucket='b',client=FakeClient())
