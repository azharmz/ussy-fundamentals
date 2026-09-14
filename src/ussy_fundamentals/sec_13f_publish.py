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


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _content_type(path: Path) -> str:
    if path.suffix == '.parquet':
        return 'application/vnd.apache.parquet'
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or 'application/octet-stream'


def _collect(root: Path, prefix: str) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(p for p in root.rglob('*') if p.is_file()):
        rel = path.relative_to(root).as_posix()
        files[rel] = {
            'local_path': path,
            'key': f'{prefix}/{rel}',
            'sha256': _sha256(path),
            'size_bytes': path.stat().st_size,
        }
    return files


def publish(*, history_root: Path, uncertainty_root: Path, live_root: Path,
            history_run_id: str, history_commit: str,
            uncertainty_run_id: str, uncertainty_commit: str,
            live_run_id: str, live_commit: str,
            publisher_run_id: str, publisher_commit: str,
            pointer_key: str = 'institutional_sponsorship/current.json',
            bucket: str | None = None, client=None) -> tuple[str, dict, dict]:
    client = client or r2_client()
    bucket = bucket or _env('R2_BUCKET_NAME')
    now = datetime.now(timezone.utc)
    produced_at = now.isoformat().replace('+00:00', 'Z')
    prefix = f'institutional_sponsorship/snapshots/{now.date().isoformat()}/run-{publisher_run_id}'

    history_summary_path = history_root / 'summary.json'
    uncertainty_summary_path = uncertainty_root / 'uncertainty_summary.json'
    live_summary_path = live_root / 'summary.json'
    if not history_summary_path.exists() or not uncertainty_summary_path.exists() or not live_summary_path.exists():
        raise FileNotFoundError('Historical, uncertainty, and live summary files are required')
    history_summary = json.loads(history_summary_path.read_text(encoding='utf-8'))
    uncertainty_summary = json.loads(uncertainty_summary_path.read_text(encoding='utf-8'))
    live_summary = json.loads(live_summary_path.read_text(encoding='utf-8'))
    if not live_summary.get('data_gate_pass'):
        raise RuntimeError('Live 13F current-state data gate is not PASS')
    if float(live_summary.get('accepted_at_complete_rate', 0)) != 1.0:
        raise RuntimeError('Live 13F accepted_at completeness must be 100%')
    if int(live_summary.get('ambiguous_lineage_events', 1)) != 0:
        raise RuntimeError('Live 13F amendment lineage must be unambiguous')

    required = {
        'history': (history_root, ['sponsorship_state_events.parquet', 'final_period_state.parquet', 'summary.json']),
        'uncertainty': (uncertainty_root, ['uncertainty_state_events.parquet', 'current_uncertainty_state.parquet', 'uncertainty_summary.json']),
        'live': (live_root, ['sponsorship_mapped.csv', 'amendment_lineage.csv', 'summary.json']),
    }
    missing = [str(root / name) for root, names in required.values() for name in names if not (root / name).exists()]
    if missing:
        raise FileNotFoundError(f'Missing canonical 13F files: {missing}')

    artifacts: dict[str, dict[str, Any]] = {}
    for group, (root, _) in required.items():
        for rel, meta in _collect(root, f'{prefix}/{group}').items():
            artifacts[f'{group}/{rel}'] = meta

    manifest = {
        'schema_version': 2,
        'type': 'institutional_sponsorship_snapshot',
        'status': 'READY',
        'produced_at': produced_at,
        'snapshot_prefix': prefix,
        'publisher_run_id': str(publisher_run_id),
        'publisher_commit': publisher_commit,
        'history_source_run_id': str(history_run_id), 'history_source_commit': history_commit,
        'uncertainty_source_run_id': str(uncertainty_run_id), 'uncertainty_source_commit': uncertainty_commit,
        'live_source_run_id': str(live_run_id), 'live_source_commit': live_commit,
        'history_summary': history_summary,
        'uncertainty_summary': uncertainty_summary,
        'live_summary': live_summary,
        'semantics': {
            'historical_availability': 'filing_date_plus_1_calendar_day_conservative_v1',
            'live_availability': 'exact_edgar_accepted_at',
            'live_identity_mapping': 'US_ISIN_BODY_TO_CUSIP9',
            'quarter_end_used_as_availability': False,
            'ambiguity': 'fail_closed',
        },
        'artifacts': {name: {k: v for k, v in meta.items() if k != 'local_path'} for name, meta in artifacts.items()},
    }

    for meta in artifacts.values():
        path: Path = meta['local_path']
        client.upload_file(str(path), bucket, meta['key'], ExtraArgs={'ContentType': _content_type(path)})

    manifest_key = f'{prefix}/manifest.json'
    client.put_object(Bucket=bucket, Key=manifest_key,
                      Body=json.dumps(manifest, indent=2, sort_keys=True).encode('utf-8'),
                      ContentType='application/json')

    pointer = {
        'schema_version': 2,
        'type': 'institutional_sponsorship_current_pointer',
        'status': 'READY', 'updated_at': produced_at,
        'snapshot_prefix': prefix, 'manifest_key': manifest_key,
        'publisher_run_id': str(publisher_run_id),
        'history_source_run_id': str(history_run_id),
        'uncertainty_source_run_id': str(uncertainty_run_id),
        'live_source_run_id': str(live_run_id),
        'history_state_events_key': manifest['artifacts']['history/sponsorship_state_events.parquet']['key'],
        'final_period_state_key': manifest['artifacts']['history/final_period_state.parquet']['key'],
        'uncertainty_state_events_key': manifest['artifacts']['uncertainty/uncertainty_state_events.parquet']['key'],
        'current_uncertainty_state_key': manifest['artifacts']['uncertainty/current_uncertainty_state.parquet']['key'],
        'live_sponsorship_mapped_key': manifest['artifacts']['live/sponsorship_mapped.csv']['key'],
        'live_amendment_lineage_key': manifest['artifacts']['live/amendment_lineage.csv']['key'],
        'live_summary_key': manifest['artifacts']['live/summary.json']['key'],
        'live_availability': 'exact_edgar_accepted_at',
        'deterministic_us_isin_count': live_summary.get('deterministic_us_isin_count'),
        'non_us_isin_not_evaluable_count': live_summary.get('non_us_isin_not_evaluable_count'),
        'strategy_returns_inspected': False, 'fwd1_modified': False,
    }
    client.put_object(Bucket=bucket, Key=pointer_key,
                      Body=json.dumps(pointer, indent=2, sort_keys=True).encode('utf-8'),
                      ContentType='application/json')
    return manifest_key, manifest, pointer


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--history-root', type=Path, required=True)
    p.add_argument('--uncertainty-root', type=Path, required=True)
    p.add_argument('--live-root', type=Path, required=True)
    p.add_argument('--history-run-id', required=True); p.add_argument('--history-commit', required=True)
    p.add_argument('--uncertainty-run-id', required=True); p.add_argument('--uncertainty-commit', required=True)
    p.add_argument('--live-run-id', required=True); p.add_argument('--live-commit', required=True)
    p.add_argument('--publisher-run-id', default=os.getenv('GITHUB_RUN_ID'))
    p.add_argument('--publisher-commit', default=os.getenv('GITHUB_SHA'))
    p.add_argument('--pointer-key', default='institutional_sponsorship/current.json')
    a = p.parse_args()
    if not a.publisher_run_id or not a.publisher_commit:
        raise RuntimeError('publisher run id and commit are required')
    manifest_key, _, pointer = publish(
        history_root=a.history_root, uncertainty_root=a.uncertainty_root, live_root=a.live_root,
        history_run_id=a.history_run_id, history_commit=a.history_commit,
        uncertainty_run_id=a.uncertainty_run_id, uncertainty_commit=a.uncertainty_commit,
        live_run_id=a.live_run_id, live_commit=a.live_commit,
        publisher_run_id=str(a.publisher_run_id), publisher_commit=str(a.publisher_commit), pointer_key=a.pointer_key)
    print(json.dumps({'manifest_key': manifest_key, 'pointer': pointer}, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
