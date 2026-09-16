from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import mimetypes
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .universe_source import _env, r2_client

SNAPSHOT_ROOT = 'institutional_sponsorship/snapshots/'
DEFAULT_RETAIN_SNAPSHOT_DATES = 2
STORAGE_CONTRACT = 'lossless-gzip-v1-selective+parquet-zstd9-v1'
GZIP_MIN_BYTES = 1024
GZIP_MAX_RATIO = 0.90
PARQUET_ZSTD_LEVEL = 9
PARQUET_ZSTD_PATHS = {'history/sponsorship_state_events.parquet','history/final_period_state.parquet'}
LEGACY_REPRESENTATION_PATHS = {'uncertainty/uncertainty_state_events.parquet','uncertainty/current_uncertainty_state.parquet','live/sponsorship_mapped.csv','live/amendment_lineage.csv','live/summary.json'}


def _sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda:fh.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def _sha256_bytes(data:bytes)->str: return hashlib.sha256(data).hexdigest()
def _content_type(path:Path)->str:
    if path.suffix=='.parquet': return 'application/vnd.apache.parquet'
    guessed,_=mimetypes.guess_type(path.name); return guessed or 'application/octet-stream'
def _gzip_bytes(data:bytes)->bytes: return gzip.compress(data,compresslevel=6,mtime=0)

def _tables_value_equal(left:pa.Table,right:pa.Table)->bool:
    if not right.schema.equals(left.schema,check_metadata=True) or right.num_rows!=left.num_rows: return False
    for name in left.column_names:
        a=left[name].combine_chunks(); b=right[name].combine_chunks()
        try:
            eq=pc.equal(a,b); valid_same=pc.equal(pc.is_null(a),pc.is_null(b)); eq=pc.if_else(pc.is_null(eq),valid_same,eq)
            if int(pc.sum(pc.invert(eq)).as_py() or 0): return False
        except Exception:
            if a.to_pylist()!=b.to_pylist(): return False
    return True

def _zstd_parquet_bytes(path:Path)->bytes:
    table=pq.read_table(path)
    with tempfile.NamedTemporaryFile(suffix='.parquet') as tmp:
        pq.write_table(table,tmp.name,compression='zstd',compression_level=PARQUET_ZSTD_LEVEL,use_dictionary=True); reread=pq.read_table(tmp.name)
        if not _tables_value_equal(table,reread): raise RuntimeError(f'Parquet ZSTD semantic verification failed for {path}')
        return Path(tmp.name).read_bytes()

def _collect(root:Path,prefix:str,group:str)->dict[str,dict[str,Any]]:
    files={}
    for path in sorted(p for p in root.rglob('*') if p.is_file()):
        rel=path.relative_to(root).as_posix(); logical_name=f'{group}/{rel}'; logical_size=path.stat().st_size; logical_sha=_sha256(path)
        meta={'local_path':path,'key':f'{prefix}/{rel}','sha256':logical_sha,'logical_sha256':logical_sha,'size_bytes':logical_size,'logical_size_bytes':logical_size,'stored_size_bytes':logical_size,'representation':'identity','content_type':_content_type(path)}
        if logical_name in PARQUET_ZSTD_PATHS:
            stored=_zstd_parquet_bytes(path); meta.update({'stored_body':stored,'stored_sha256':_sha256_bytes(stored),'stored_size_bytes':len(stored),'compression':'zstd','compression_level':PARQUET_ZSTD_LEVEL,'representation':'parquet-zstd','compression_ratio':len(stored)/logical_size if logical_size else 1.0,'semantic_verification':'full_schema_and_value_equality'})
        elif logical_name not in LEGACY_REPRESENTATION_PATHS and path.suffix.lower() in {'.csv','.json','.jsonl'} and logical_size>=GZIP_MIN_BYTES:
            raw=path.read_bytes(); stored=_gzip_bytes(raw); ratio=len(stored)/len(raw) if raw else 1.0
            if ratio<=GZIP_MAX_RATIO: meta.update({'key':f'{prefix}/{rel}.gz','stored_body':stored,'stored_sha256':_sha256_bytes(stored),'stored_size_bytes':len(stored),'compression':'gzip','content_encoding':'gzip','representation':'gzip','compression_ratio':ratio})
        files[rel]=meta
    return files

def _manifest_meta(meta:dict[str,Any])->dict[str,Any]: return {k:v for k,v in meta.items() if k not in {'local_path','stored_body'}}
def _list_snapshot_objects(client,bucket:str)->list[dict]:
    out=[]; token=None
    while True:
        kw={'Bucket':bucket,'Prefix':SNAPSHOT_ROOT}
        if token: kw['ContinuationToken']=token
        resp=client.list_objects_v2(**kw); out.extend(resp.get('Contents',[]))
        if not resp.get('IsTruncated'): return out
        token=resp['NextContinuationToken']

def _apply_retention(*,client,bucket:str,pointer_key:str,retain_dates:int=DEFAULT_RETAIN_SNAPSHOT_DATES)->dict:
    if retain_dates<2: raise RuntimeError('13F retention must preserve at least two distinct snapshot dates')
    pointer=json.loads(client.get_object(Bucket=bucket,Key=pointer_key)['Body'].read()); current=str(pointer.get('snapshot_prefix') or '').rstrip('/')+'/'
    if not current.startswith(SNAPSHOT_ROOT) or '/run-' not in current: raise RuntimeError('current 13F pointer has invalid snapshot prefix; retention aborted')
    current_date=current[len(SNAPSHOT_ROOT):].split('/')[0]
    refs={v for k,v in pointer.items() if k.endswith('_key') and isinstance(v,str)}; objects=_list_snapshot_objects(client,bucket); groups={}
    for obj in objects:
        key=obj['Key']; parts=key[len(SNAPSHOT_ROOT):].split('/')
        if len(parts)<3 or not parts[1].startswith('run-'): continue
        prefix=f'{SNAPSHOT_ROOT}{parts[0]}/{parts[1]}/'; groups.setdefault(prefix,[]).append(obj)
    dates=sorted({p[len(SNAPSHOT_ROOT):].split('/')[0] for p in groups},reverse=True); keep_dates=set(dates[:retain_dates]); deleted_objects=deleted_bytes=0; deleted_prefixes=[]; same_date_deleted_prefixes=[]
    for prefix,obs in sorted(groups.items()):
        date=prefix[len(SNAPSHOT_ROOT):].split('/')[0]
        superseded_same_date=(date==current_date and prefix!=current)
        expired_date=date not in keep_dates
        if not superseded_same_date and not expired_date: continue
        if prefix==current: continue
        keys={o['Key'] for o in obs}
        if keys & refs: raise RuntimeError(f'retention candidate contains current pointer reference: {prefix}')
        if prefix+'manifest.json' not in keys: raise RuntimeError(f'retention candidate missing manifest: {prefix}')
        now=json.loads(client.get_object(Bucket=bucket,Key=pointer_key)['Body'].read()); now_current=str(now.get('snapshot_prefix') or '').rstrip('/')+'/'; now_refs={v for k,v in now.items() if k.endswith('_key') and isinstance(v,str)}
        if now_current!=current or prefix==now_current or keys & now_refs: raise RuntimeError('13F pointer moved or candidate became protected; retention aborted')
        for key in sorted(keys): client.delete_object(Bucket=bucket,Key=key)
        if any(o['Key'].startswith(prefix) for o in _list_snapshot_objects(client,bucket)): raise RuntimeError(f'post-delete verification failed: {prefix}')
        deleted_prefixes.append(prefix.rstrip('/'))
        if superseded_same_date: same_date_deleted_prefixes.append(prefix.rstrip('/'))
        deleted_objects+=len(obs); deleted_bytes+=sum(int(o.get('Size',0)) for o in obs)
    return {'retain_distinct_snapshot_dates':retain_dates,'kept_dates':sorted(keep_dates),'single_run_current_date':current_date,'same_date_deleted_prefixes':same_date_deleted_prefixes,'deleted_prefixes':deleted_prefixes,'deleted_objects':deleted_objects,'deleted_bytes':deleted_bytes}

def publish(*,history_root:Path,uncertainty_root:Path,live_root:Path,history_run_id:str,history_commit:str,uncertainty_run_id:str,uncertainty_commit:str,live_run_id:str,live_commit:str,publisher_run_id:str,publisher_commit:str,pointer_key:str='institutional_sponsorship/current.json',bucket:str|None=None,client=None)->tuple[str,dict,dict]:
    client=client or r2_client(); bucket=bucket or _env('R2_BUCKET_NAME'); now=datetime.now(timezone.utc); produced_at=now.isoformat().replace('+00:00','Z'); prefix=f'{SNAPSHOT_ROOT}{now.date().isoformat()}/run-{publisher_run_id}'
    hs=history_root/'summary.json'; us=uncertainty_root/'uncertainty_summary.json'; ls=live_root/'summary.json'
    if not hs.exists() or not us.exists() or not ls.exists(): raise FileNotFoundError('Historical, uncertainty, and live summary files are required')
    history_summary=json.loads(hs.read_text()); uncertainty_summary=json.loads(us.read_text()); live_summary=json.loads(ls.read_text())
    if not live_summary.get('data_gate_pass'): raise RuntimeError('Live 13F current-state data gate is not PASS')
    if float(live_summary.get('accepted_at_complete_rate',0))!=1.0: raise RuntimeError('Live 13F accepted_at completeness must be 100%')
    if int(live_summary.get('ambiguous_lineage_events',1))!=0: raise RuntimeError('Live 13F amendment lineage must be unambiguous')
    required={'history':(history_root,['sponsorship_state_events.parquet','final_period_state.parquet','summary.json']),'uncertainty':(uncertainty_root,['uncertainty_state_events.parquet','current_uncertainty_state.parquet','uncertainty_summary.json']),'live':(live_root,['sponsorship_mapped.csv','amendment_lineage.csv','summary.json'])}
    missing=[str(root/name) for root,names in required.values() for name in names if not (root/name).exists()]
    if missing: raise FileNotFoundError(f'Missing canonical 13F files: {missing}')
    artifacts={}
    for group,(root,_) in required.items():
        for rel,meta in _collect(root,f'{prefix}/{group}',group).items(): artifacts[f'{group}/{rel}']=meta
    logical_bytes=sum(int(m['logical_size_bytes']) for m in artifacts.values()); stored_bytes=sum(int(m['stored_size_bytes']) for m in artifacts.values()); compressed_count=sum(m.get('representation')!='identity' for m in artifacts.values())
    manifest={'schema_version':2,'type':'institutional_sponsorship_snapshot','status':'READY','produced_at':produced_at,'snapshot_prefix':prefix,'publisher_run_id':str(publisher_run_id),'publisher_commit':publisher_commit,'history_source_run_id':str(history_run_id),'history_source_commit':history_commit,'uncertainty_source_run_id':str(uncertainty_run_id),'uncertainty_source_commit':uncertainty_commit,'live_source_run_id':str(live_run_id),'live_source_commit':live_commit,'storage_contract':STORAGE_CONTRACT,'storage_summary':{'logical_bytes':logical_bytes,'stored_bytes':stored_bytes,'bytes_saved':logical_bytes-stored_bytes,'compressed_artifact_count':compressed_count,'gzip_min_bytes':GZIP_MIN_BYTES,'gzip_max_ratio':GZIP_MAX_RATIO,'parquet_zstd_level':PARQUET_ZSTD_LEVEL},'history_summary':history_summary,'uncertainty_summary':uncertainty_summary,'live_summary':live_summary,'semantics':{'historical_availability':'filing_date_plus_1_calendar_day_conservative_v1','live_availability':'exact_edgar_accepted_at','live_identity_mapping':'US_ISIN_BODY_TO_CUSIP9','quarter_end_used_as_availability':False,'ambiguity':'fail_closed'},'artifacts':{name:_manifest_meta(meta) for name,meta in artifacts.items()}}
    for meta in artifacts.values():
        path=meta['local_path']
        if meta.get('representation')=='gzip': client.put_object(Bucket=bucket,Key=meta['key'],Body=meta['stored_body'],ContentType=meta['content_type'],ContentEncoding='gzip')
        elif meta.get('representation')=='parquet-zstd': client.put_object(Bucket=bucket,Key=meta['key'],Body=meta['stored_body'],ContentType=meta['content_type'])
        else: client.upload_file(str(path),bucket,meta['key'],ExtraArgs={'ContentType':meta['content_type']})
    manifest_key=f'{prefix}/manifest.json'; client.put_object(Bucket=bucket,Key=manifest_key,Body=json.dumps(manifest,indent=2,sort_keys=True).encode(),ContentType='application/json')
    pointer={'schema_version':2,'type':'institutional_sponsorship_current_pointer','status':'READY','updated_at':produced_at,'snapshot_prefix':prefix,'manifest_key':manifest_key,'publisher_run_id':str(publisher_run_id),'history_source_run_id':str(history_run_id),'uncertainty_source_run_id':str(uncertainty_run_id),'live_source_run_id':str(live_run_id),'storage_contract':STORAGE_CONTRACT,'history_state_events_key':manifest['artifacts']['history/sponsorship_state_events.parquet']['key'],'final_period_state_key':manifest['artifacts']['history/final_period_state.parquet']['key'],'uncertainty_state_events_key':manifest['artifacts']['uncertainty/uncertainty_state_events.parquet']['key'],'current_uncertainty_state_key':manifest['artifacts']['uncertainty/current_uncertainty_state.parquet']['key'],'live_sponsorship_mapped_key':manifest['artifacts']['live/sponsorship_mapped.csv']['key'],'live_amendment_lineage_key':manifest['artifacts']['live/amendment_lineage.csv']['key'],'live_summary_key':manifest['artifacts']['live/summary.json']['key'],'live_availability':'exact_edgar_accepted_at','deterministic_us_isin_count':live_summary.get('deterministic_us_isin_count'),'non_us_isin_not_evaluable_count':live_summary.get('non_us_isin_not_evaluable_count'),'strategy_returns_inspected':False,'fwd1_modified':False}
    client.put_object(Bucket=bucket,Key=pointer_key,Body=json.dumps(pointer,indent=2,sort_keys=True).encode(),ContentType='application/json'); retention=_apply_retention(client=client,bucket=bucket,pointer_key=pointer_key); pointer['retention_result']=retention
    return manifest_key,manifest,pointer

def main()->None:
    p=argparse.ArgumentParser(); p.add_argument('--history-root',type=Path,required=True); p.add_argument('--uncertainty-root',type=Path,required=True); p.add_argument('--live-root',type=Path,required=True); p.add_argument('--history-run-id',required=True); p.add_argument('--history-commit',required=True); p.add_argument('--uncertainty-run-id',required=True); p.add_argument('--uncertainty-commit',required=True); p.add_argument('--live-run-id',required=True); p.add_argument('--live-commit',required=True); p.add_argument('--publisher-run-id',default=os.getenv('GITHUB_RUN_ID')); p.add_argument('--publisher-commit',default=os.getenv('GITHUB_SHA')); p.add_argument('--pointer-key',default='institutional_sponsorship/current.json'); a=p.parse_args()
    if not a.publisher_run_id or not a.publisher_commit: raise RuntimeError('publisher run id and commit are required')
    manifest_key,_,pointer=publish(history_root=a.history_root,uncertainty_root=a.uncertainty_root,live_root=a.live_root,history_run_id=a.history_run_id,history_commit=a.history_commit,uncertainty_run_id=a.uncertainty_run_id,uncertainty_commit=a.uncertainty_commit,live_run_id=a.live_run_id,live_commit=a.live_commit,publisher_run_id=str(a.publisher_run_id),publisher_commit=str(a.publisher_commit),pointer_key=a.pointer_key); print(json.dumps({'manifest_key':manifest_key,'pointer':pointer},indent=2,sort_keys=True))

if __name__=='__main__': main()
