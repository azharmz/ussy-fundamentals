from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
import zipfile
from collections import defaultdict
from io import BytesIO
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .sec_13f_bulk_catalog import discover
from .sec_13f_bulk_events import _amendment_state, _hydrate_ambiguous_amendments, _read_tsv, _target_cusips, _zip_member
from .sec_client import SecClient


def _schema() -> pa.Schema:
    return pa.schema([
        ('available_on', pa.string()),
        ('period_of_report', pa.string()),
        ('cusip', pa.string()),
        ('I_manager_count', pa.int64()),
        ('I_reported_value_total', pa.float64()),
        ('I_reported_share_total', pa.float64()),
        ('source_accession', pa.string()),
        ('manager_cik', pa.string()),
        ('lineage_action', pa.string()),
    ])


def _db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.executescript('''
    PRAGMA journal_mode=WAL;
    PRAGMA synchronous=NORMAL;
    CREATE TABLE IF NOT EXISTS manager_period (
      manager_cik TEXT NOT NULL,
      period TEXT NOT NULL,
      status TEXT NOT NULL,
      last_accession TEXT,
      last_available TEXT,
      PRIMARY KEY(manager_cik, period)
    );
    CREATE TABLE IF NOT EXISTS manager_position (
      manager_cik TEXT NOT NULL,
      period TEXT NOT NULL,
      cusip TEXT NOT NULL,
      value_thousands REAL,
      shares REAL,
      PRIMARY KEY(manager_cik, period, cusip)
    );
    CREATE TABLE IF NOT EXISTS aggregate_state (
      period TEXT NOT NULL,
      cusip TEXT NOT NULL,
      manager_count INTEGER NOT NULL,
      value_total REAL NOT NULL,
      share_total REAL NOT NULL,
      PRIMARY KEY(period, cusip)
    );
    ''')
    return con


def _clean_num(value) -> float:
    if value is None or pd.isna(value):
        return 0.0
    return float(value)


def _extract_dataset(url: str, target: set[str], client: SecClient) -> tuple[pd.DataFrame, dict[str, dict[str, tuple[float, float]]], dict]:
    raw = client.get_bytes(url)
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        sub = _read_tsv(zf, 'SUBMISSION.tsv')
        cover = _read_tsv(zf, 'COVERPAGE.tsv')
        sub = sub[sub['SUBMISSIONTYPE'].str.upper().isin(['13F-HR', '13F-HR/A'])].copy()
        meta = sub.merge(cover, how='left', on='ACCESSION_NUMBER', suffixes=('', '_COVER'))
        meta['filing_date_parsed'] = pd.to_datetime(meta['FILING_DATE'], format='%d-%b-%Y', errors='coerce')
        meta['period_parsed'] = pd.to_datetime(meta['PERIODOFREPORT'], format='%d-%b-%Y', errors='coerce')
        meta['available_on'] = (meta['filing_date_parsed'] + pd.Timedelta(days=1)).dt.date.astype('string')
        meta['period_of_report'] = meta['period_parsed'].dt.date.astype('string')
        meta['amendment_state'] = meta.apply(_amendment_state, axis=1)
        meta, attempted, resolved = _hydrate_ambiguous_amendments(meta, client)

        accession_set = set(meta['ACCESSION_NUMBER'].dropna())
        pieces = []
        total_info = 0
        target_rows = 0
        info_member = _zip_member(zf, 'INFOTABLE.tsv')
        reader = pd.read_csv(zf.open(info_member), sep='\t', dtype='string', chunksize=250_000, low_memory=False)
        for chunk in reader:
            total_info += len(chunk)
            chunk = chunk[chunk['ACCESSION_NUMBER'].isin(accession_set)].copy()
            chunk['CUSIP'] = chunk['CUSIP'].astype('string').str.upper().str.strip()
            chunk = chunk[chunk['CUSIP'].isin(target)].copy()
            if 'PUTCALL' in chunk.columns:
                chunk = chunk[chunk['PUTCALL'].isna() | chunk['PUTCALL'].astype('string').str.strip().eq('')].copy()
            target_rows += len(chunk)
            if not chunk.empty:
                chunk['VALUE'] = pd.to_numeric(chunk['VALUE'], errors='coerce')
                chunk['SSHPRNAMT'] = pd.to_numeric(chunk['SSHPRNAMT'], errors='coerce')
                pieces.append(chunk.groupby(['ACCESSION_NUMBER','CUSIP'], as_index=False).agg(
                    value=('VALUE','sum'), shares=('SSHPRNAMT','sum')
                ))

    h = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame(columns=['ACCESSION_NUMBER','CUSIP','value','shares'])
    if not h.empty:
        h = h.groupby(['ACCESSION_NUMBER','CUSIP'], as_index=False).agg(value=('value','sum'), shares=('shares','sum'))
    positions: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    for r in h.itertuples(index=False):
        positions[str(r.ACCESSION_NUMBER)][str(r.CUSIP)] = (_clean_num(r.value), _clean_num(r.shares))

    meta = meta.sort_values(['filing_date_parsed','ACCESSION_NUMBER'], na_position='last')
    summary = {
        'url': url,
        'bulk_size_bytes': len(raw),
        'filing_count': int(len(meta)),
        'target_raw_rows': int(target_rows),
        'target_accession_cusip_rows': int(len(h)),
        'infotable_rows': int(total_info),
        'ambiguous_hydration_attempted': int(attempted),
        'ambiguous_hydration_resolved': int(resolved),
        'unclassified_amendments': int(meta['amendment_state'].eq('AMENDMENT_UNCLASSIFIED').sum()),
        'filing_date_complete_rate': float(meta['FILING_DATE'].notna().mean()) if len(meta) else 0.0,
        'period_complete_rate': float(meta['period_of_report'].notna().mean()) if len(meta) else 0.0,
    }
    return meta, positions, summary


def _old_positions(con: sqlite3.Connection, manager: str, period: str) -> tuple[str | None, dict[str, tuple[float,float]]]:
    row = con.execute('SELECT status FROM manager_period WHERE manager_cik=? AND period=?', (manager, period)).fetchone()
    status = row[0] if row else None
    if status != 'VALID':
        return status, {}
    rows = con.execute('SELECT cusip,value_thousands,shares FROM manager_position WHERE manager_cik=? AND period=?', (manager, period)).fetchall()
    return status, {c: (_clean_num(v), _clean_num(s)) for c,v,s in rows}


def _apply_filing(con: sqlite3.Connection, manager: str, period: str, accession: str, available: str, state: str, incoming: dict[str, tuple[float,float]]) -> list[dict]:
    old_status, old = _old_positions(con, manager, period)
    action = 'NONE'
    if state in {'BASE','AMENDMENT_RESTATEMENT'}:
        new_status = 'VALID'
        new = dict(incoming)
        action = 'REPLACE'
    elif state == 'AMENDMENT_NEW_HOLDINGS' and old_status == 'VALID':
        new_status = 'VALID'
        new = dict(old)
        for cusip, (v,s) in incoming.items():
            ov, os = new.get(cusip, (0.0,0.0))
            new[cusip] = (ov+v, os+s)
        action = 'ADD'
    elif state == 'AMENDMENT_NEW_HOLDINGS' and old_status is None:
        new_status = 'AMBIGUOUS'
        new = {}
        action = 'AMBIGUOUS_NO_BASE'
    else:
        new_status = 'AMBIGUOUS'
        new = {}
        action = 'AMBIGUOUS'

    events = []
    for cusip in sorted(set(old) | set(new)):
        old_present = cusip in old
        new_present = cusip in new
        ov, os = old.get(cusip, (0.0,0.0))
        nv, ns = new.get(cusip, (0.0,0.0))
        row = con.execute('SELECT manager_count,value_total,share_total FROM aggregate_state WHERE period=? AND cusip=?', (period,cusip)).fetchone()
        count, value, shares = (row if row else (0,0.0,0.0))
        count = int(count) + int(new_present) - int(old_present)
        value = float(value) + nv - ov
        shares = float(shares) + ns - os
        if count < 0:
            raise RuntimeError('negative aggregate manager_count')
        con.execute('INSERT OR REPLACE INTO aggregate_state(period,cusip,manager_count,value_total,share_total) VALUES(?,?,?,?,?)', (period,cusip,count,value,shares))
        if old_present != new_present or abs(nv-ov) > 1e-9 or abs(ns-os) > 1e-9:
            events.append({
                'available_on': available,
                'period_of_report': period,
                'cusip': cusip,
                'I_manager_count': count,
                'I_reported_value_total': value,
                'I_reported_share_total': shares,
                'source_accession': accession,
                'manager_cik': manager,
                'lineage_action': action,
            })

    con.execute('DELETE FROM manager_position WHERE manager_cik=? AND period=?', (manager, period))
    if new_status == 'VALID':
        con.executemany('INSERT INTO manager_position(manager_cik,period,cusip,value_thousands,shares) VALUES(?,?,?,?,?)', [(manager,period,c,v,s) for c,(v,s) in new.items()])
    con.execute('INSERT OR REPLACE INTO manager_period(manager_cik,period,status,last_accession,last_available) VALUES(?,?,?,?,?)', (manager,period,new_status,accession,available))
    return events


def run(universe_csv: Path, output_dir: Path, max_datasets: int | None = None) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    target, us_count, non_us_count = _target_cusips(universe_csv)
    catalog = list(reversed(discover()))
    if max_datasets is not None:
        catalog = catalog[:max_datasets]
    client = SecClient()

    dataset_summaries = []
    total_events = 0
    ambiguous_filing_events = 0
    no_base_new_holdings = 0
    writer = None
    schema = _schema()

    with tempfile.TemporaryDirectory() as td:
        con = _db(Path(td) / 'state.sqlite')
        try:
            for n, ds in enumerate(catalog, start=1):
                print(f'dataset {n}/{len(catalog)}: {ds["label"]}', flush=True)
                meta, positions, ds_summary = _extract_dataset(ds['url'], target, client)
                ds_summary['label'] = ds['label']
                event_batch = []
                for _, row in meta.iterrows():
                    manager = str(row['CIK'])
                    period = str(row['period_of_report'])
                    accession = str(row['ACCESSION_NUMBER'])
                    available = str(row['available_on'])
                    state = str(row['amendment_state'])
                    if not period or period == '<NA>' or not available or available == '<NA>':
                        ambiguous_filing_events += 1
                        continue
                    events = _apply_filing(con, manager, period, accession, available, state, positions.get(accession, {}))
                    if state == 'AMENDMENT_UNCLASSIFIED':
                        ambiguous_filing_events += 1
                    if events and events[0]['lineage_action'] == 'AMBIGUOUS_NO_BASE':
                        no_base_new_holdings += 1
                    event_batch.extend(events)
                    if len(event_batch) >= 100_000:
                        table = pa.Table.from_pylist(event_batch, schema=schema)
                        if writer is None:
                            writer = pq.ParquetWriter(output_dir / 'sponsorship_state_events.parquet', schema, compression='zstd')
                        writer.write_table(table)
                        total_events += len(event_batch)
                        event_batch.clear()
                if event_batch:
                    table = pa.Table.from_pylist(event_batch, schema=schema)
                    if writer is None:
                        writer = pq.ParquetWriter(output_dir / 'sponsorship_state_events.parquet', schema, compression='zstd')
                    writer.write_table(table)
                    total_events += len(event_batch)
                con.commit()
                dataset_summaries.append(ds_summary)
                print(f'  cumulative state events={total_events}', flush=True)

            final_rows = con.execute('SELECT period,cusip,manager_count,value_total,share_total FROM aggregate_state').fetchall()
            final = pd.DataFrame(final_rows, columns=['period_of_report','cusip','I_manager_count','I_reported_value_total','I_reported_share_total'])
            final.to_parquet(output_dir / 'final_period_state.parquet', index=False)
            statuses = pd.read_sql_query('SELECT status,COUNT(*) n FROM manager_period GROUP BY status', con)
            statuses.to_csv(output_dir / 'manager_period_status_counts.csv', index=False)
        finally:
            if writer is not None:
                writer.close()
            con.close()

    (output_dir / 'dataset_summaries.json').write_text(json.dumps(dataset_summaries, indent=2), encoding='utf-8')
    total_filings = sum(x['filing_count'] for x in dataset_summaries)
    unresolved = sum(x['unclassified_amendments'] for x in dataset_summaries)
    summary = {
        'official_dataset_count_discovered': len(discover()),
        'datasets_processed': len(catalog),
        'total_13f_filings_processed': int(total_filings),
        'state_change_events': int(total_events),
        'unclassified_amendment_filings': int(unresolved),
        'ambiguous_filing_events': int(ambiguous_filing_events),
        'new_holdings_without_valid_base': int(no_base_new_holdings),
        'deterministic_us_isin_count': int(us_count),
        'non_us_isin_not_evaluable_count': int(non_us_count),
        'historical_availability_semantics': 'filing_date_plus_1_calendar_day_conservative_v1',
        'quarter_end_used_as_availability': False,
        'strategy_returns_inspected': False,
        'fwd1_modified': False,
        'pit_attach_ready': unresolved == 0 and ambiguous_filing_events == 0 and no_base_new_holdings == 0,
        'note': 'Event-based historical state. Ambiguous manager-period lineages are excluded from aggregate counts until a valid replacing filing restores them.',
    }
    (output_dir / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True), encoding='utf-8')
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--universe', type=Path, default=Path('data/processed/current_universe.csv'))
    p.add_argument('--output-dir', type=Path, default=Path('data/13f/history_state'))
    p.add_argument('--max-datasets', type=int)
    a = p.parse_args()
    run(a.universe, a.output_dir, a.max_datasets)


if __name__ == '__main__':
    main()
