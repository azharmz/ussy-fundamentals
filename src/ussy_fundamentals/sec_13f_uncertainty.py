from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .sec_13f_bulk_catalog import discover
from .sec_13f_bulk_events import _target_cusips
from .sec_13f_history_state import _extract_dataset
from .sec_client import SecClient


def _db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.executescript('''
    CREATE TABLE IF NOT EXISTS manager_period (
      manager_cik TEXT NOT NULL,
      period TEXT NOT NULL,
      status TEXT NOT NULL,
      PRIMARY KEY(manager_cik, period)
    );
    CREATE TABLE IF NOT EXISTS known_cusip (
      manager_cik TEXT NOT NULL,
      period TEXT NOT NULL,
      cusip TEXT NOT NULL,
      PRIMARY KEY(manager_cik, period, cusip)
    );
    CREATE TABLE IF NOT EXISTS uncertain_cusip (
      manager_cik TEXT NOT NULL,
      period TEXT NOT NULL,
      cusip TEXT NOT NULL,
      PRIMARY KEY(manager_cik, period, cusip)
    );
    CREATE TABLE IF NOT EXISTS aggregate_uncertainty (
      period TEXT NOT NULL,
      cusip TEXT NOT NULL,
      uncertain_manager_count INTEGER NOT NULL,
      PRIMARY KEY(period, cusip)
    );
    ''')
    return con


def _get_set(con: sqlite3.Connection, table: str, manager: str, period: str) -> set[str]:
    rows = con.execute(
        f'SELECT cusip FROM {table} WHERE manager_cik=? AND period=?',
        (manager, period),
    ).fetchall()
    return {str(r[0]) for r in rows}


def _set_rows(con: sqlite3.Connection, table: str, manager: str, period: str, values: set[str]) -> None:
    con.execute(f'DELETE FROM {table} WHERE manager_cik=? AND period=?', (manager, period))
    if values:
        con.executemany(
            f'INSERT INTO {table}(manager_cik,period,cusip) VALUES(?,?,?)',
            [(manager, period, c) for c in sorted(values)],
        )


def _apply(
    con: sqlite3.Connection,
    *,
    manager: str,
    period: str,
    accession: str,
    available_on: str,
    state: str,
    incoming: set[str],
) -> list[dict]:
    old_known = _get_set(con, 'known_cusip', manager, period)
    old_uncertain = _get_set(con, 'uncertain_cusip', manager, period)
    status_row = con.execute(
        'SELECT status FROM manager_period WHERE manager_cik=? AND period=?',
        (manager, period),
    ).fetchone()
    old_status = status_row[0] if status_row else None

    if state in {'BASE', 'AMENDMENT_RESTATEMENT'}:
        new_status = 'VALID'
        new_known = set(incoming)
        new_uncertain: set[str] = set()
    elif state == 'AMENDMENT_NEW_HOLDINGS' and old_status == 'VALID':
        new_status = 'VALID'
        new_known = old_known | set(incoming)
        new_uncertain = set(old_uncertain)
    else:
        # Unknown amendment semantics invalidate only the manager-period holdings that
        # could be affected. The union is conservative: previous known holdings plus
        # holdings reported by the ambiguous filing plus any already-uncertain names.
        new_status = 'AMBIGUOUS'
        new_known = set()
        new_uncertain = old_known | old_uncertain | set(incoming)

    events: list[dict] = []
    for cusip in sorted(old_uncertain | new_uncertain):
        old_present = cusip in old_uncertain
        new_present = cusip in new_uncertain
        if old_present == new_present:
            continue
        row = con.execute(
            'SELECT uncertain_manager_count FROM aggregate_uncertainty WHERE period=? AND cusip=?',
            (period, cusip),
        ).fetchone()
        count = int(row[0]) if row else 0
        count += int(new_present) - int(old_present)
        if count < 0:
            raise RuntimeError('negative uncertainty count')
        con.execute(
            'INSERT OR REPLACE INTO aggregate_uncertainty(period,cusip,uncertain_manager_count) VALUES(?,?,?)',
            (period, cusip, count),
        )
        events.append({
            'available_on': available_on,
            'period_of_report': period,
            'cusip': cusip,
            'uncertainty_delta': 1 if new_present else -1,
            'I_uncertain_manager_count': count,
            'manager_cik': manager,
            'source_accession': accession,
            'lineage_state': state,
        })

    _set_rows(con, 'known_cusip', manager, period, new_known)
    _set_rows(con, 'uncertain_cusip', manager, period, new_uncertain)
    con.execute(
        'INSERT OR REPLACE INTO manager_period(manager_cik,period,status) VALUES(?,?,?)',
        (manager, period, new_status),
    )
    return events


def run(universe_csv: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    target, us_count, non_us_count = _target_cusips(universe_csv)
    catalog = list(reversed(discover()))
    client = SecClient()
    schema = pa.schema([
        ('available_on', pa.string()),
        ('period_of_report', pa.string()),
        ('cusip', pa.string()),
        ('uncertainty_delta', pa.int64()),
        ('I_uncertain_manager_count', pa.int64()),
        ('manager_cik', pa.string()),
        ('source_accession', pa.string()),
        ('lineage_state', pa.string()),
    ])
    writer = None
    total_events = 0
    ambiguous_filings = 0

    with tempfile.TemporaryDirectory() as td:
        con = _db(Path(td) / 'uncertainty.sqlite')
        try:
            for n, ds in enumerate(catalog, start=1):
                print(f'uncertainty dataset {n}/{len(catalog)}: {ds["label"]}', flush=True)
                meta, positions, _ = _extract_dataset(ds['url'], target, client)
                batch: list[dict] = []
                for _, row in meta.iterrows():
                    period = str(row['period_of_report'])
                    available = str(row['available_on'])
                    if period in {'', '<NA>'} or available in {'', '<NA>'}:
                        continue
                    state = str(row['amendment_state'])
                    if state == 'AMENDMENT_UNCLASSIFIED':
                        ambiguous_filings += 1
                    accession = str(row['ACCESSION_NUMBER'])
                    incoming = set(positions.get(accession, {}))
                    batch.extend(_apply(
                        con,
                        manager=str(row['CIK']),
                        period=period,
                        accession=accession,
                        available_on=available,
                        state=state,
                        incoming=incoming,
                    ))
                if batch:
                    table = pa.Table.from_pylist(batch, schema=schema)
                    if writer is None:
                        writer = pq.ParquetWriter(output_dir / 'uncertainty_state_events.parquet', schema, compression='zstd')
                    writer.write_table(table)
                    total_events += len(batch)
                con.commit()

            current = pd.read_sql_query(
                'SELECT period,cusip,uncertain_manager_count FROM aggregate_uncertainty WHERE uncertain_manager_count > 0',
                con,
            )
            current.to_parquet(output_dir / 'current_uncertainty_state.parquet', index=False)
            unresolved_manager_periods = int(con.execute(
                "SELECT COUNT(*) FROM manager_period WHERE status='AMBIGUOUS'"
            ).fetchone()[0])
        finally:
            if writer is not None:
                writer.close()
            con.close()

    summary = {
        'datasets_processed': len(catalog),
        'ambiguous_filings_seen': ambiguous_filings,
        'uncertainty_state_change_events': total_events,
        'unresolved_manager_periods_at_end': unresolved_manager_periods,
        'current_uncertain_cusip_period_pairs': int(len(current)),
        'deterministic_us_isin_count': us_count,
        'non_us_isin_not_evaluable_count': non_us_count,
        'strategy_returns_inspected': False,
        'fwd1_modified': False,
        'semantics': 'CUSIP-period is NOT_EVALUABLE while I_uncertain_manager_count > 0',
    }
    (output_dir / 'uncertainty_summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True), encoding='utf-8')
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--universe', type=Path, default=Path('data/processed/current_universe.csv'))
    p.add_argument('--output-dir', type=Path, default=Path('data/13f/uncertainty'))
    a = p.parse_args()
    run(a.universe, a.output_dir)


if __name__ == '__main__':
    main()
