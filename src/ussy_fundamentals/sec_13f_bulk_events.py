from __future__ import annotations

import argparse
import json
import zipfile
from io import BytesIO
from pathlib import Path

import pandas as pd

from .sec_client import SecClient

DEFAULT_URL = 'https://www.sec.gov/files/structureddata/data/form-13f-data-sets/01mar2026-31may2026_form13f.zip'


def _target_cusips(universe_csv: Path) -> tuple[set[str], int, int]:
    u = pd.read_csv(universe_csv, dtype='string')
    target: set[str] = set()
    us = non_us = 0
    for sid in u['security_id'].fillna('').astype(str).str.upper().str.strip():
        if len(sid) == 12 and sid.startswith('US'):
            target.add(sid[2:11])
            us += 1
        else:
            non_us += 1
    return target, us, non_us


def _read_tsv(zf: zipfile.ZipFile, name: str, **kwargs) -> pd.DataFrame:
    return pd.read_csv(zf.open(name), sep='\t', dtype='string', low_memory=False, **kwargs)


def _amendment_state(row: pd.Series) -> str:
    form = str(row.get('SUBMISSIONTYPE') or '').upper().strip()
    if form == '13F-HR':
        return 'BASE'
    typ = str(row.get('AMENDMENTTYPE') or '').upper().strip()
    if 'RESTATEMENT' in typ:
        return 'AMENDMENT_RESTATEMENT'
    if 'NEW' in typ and 'HOLDING' in typ:
        return 'AMENDMENT_NEW_HOLDINGS'
    return 'AMENDMENT_UNCLASSIFIED'


def run(url: str, universe_csv: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    target, us_count, non_us_count = _target_cusips(universe_csv)
    raw = SecClient().get_bytes(url)
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        sub = _read_tsv(zf, 'SUBMISSION.tsv')
        cover = _read_tsv(zf, 'COVERPAGE.tsv')
        sub = sub[sub['SUBMISSIONTYPE'].str.upper().isin(['13F-HR', '13F-HR/A'])].copy()
        meta = sub.merge(cover, how='left', on='ACCESSION_NUMBER', suffixes=('', '_COVER'))
        meta['FILING_DATE_PARSED'] = pd.to_datetime(meta['FILING_DATE'], errors='coerce')
        meta['PERIOD_PARSED'] = pd.to_datetime(meta['PERIODOFREPORT'], errors='coerce')
        meta['available_on'] = (meta['FILING_DATE_PARSED'] + pd.Timedelta(days=1)).dt.date.astype('string')
        meta['period_of_report'] = meta['PERIOD_PARSED'].dt.date.astype('string')
        meta['amendment_state'] = meta.apply(_amendment_state, axis=1)

        accession_set = set(meta['ACCESSION_NUMBER'].dropna())
        filtered_parts = []
        total_info_rows = 0
        matched_raw_rows = 0
        reader = pd.read_csv(zf.open('INFOTABLE.tsv'), sep='\t', dtype='string', chunksize=250_000, low_memory=False)
        for chunk in reader:
            total_info_rows += len(chunk)
            chunk = chunk[chunk['ACCESSION_NUMBER'].isin(accession_set)]
            chunk['CUSIP'] = chunk['CUSIP'].astype('string').str.upper().str.strip()
            chunk = chunk[chunk['CUSIP'].isin(target)]
            if 'PUTCALL' in chunk.columns:
                chunk = chunk[chunk['PUTCALL'].isna() | chunk['PUTCALL'].astype('string').str.strip().eq('')]
            matched_raw_rows += len(chunk)
            if not chunk.empty:
                for c in ['VALUE', 'SSHPRNAMT']:
                    chunk[c] = pd.to_numeric(chunk[c], errors='coerce')
                part = chunk.groupby(['ACCESSION_NUMBER', 'CUSIP'], as_index=False).agg(
                    reported_value_thousands=('VALUE', 'sum'),
                    reported_shares=('SSHPRNAMT', 'sum'),
                    source_line_count=('CUSIP', 'size'),
                )
                filtered_parts.append(part)

    holdings = pd.concat(filtered_parts, ignore_index=True) if filtered_parts else pd.DataFrame(columns=['ACCESSION_NUMBER','CUSIP','reported_value_thousands','reported_shares','source_line_count'])
    if not holdings.empty:
        holdings = holdings.groupby(['ACCESSION_NUMBER','CUSIP'], as_index=False).agg(
            reported_value_thousands=('reported_value_thousands','sum'),
            reported_shares=('reported_shares','sum'),
            source_line_count=('source_line_count','sum'),
        )

    keep = ['ACCESSION_NUMBER','CIK','FILING_DATE','SUBMISSIONTYPE','period_of_report','available_on','ISAMENDMENT','AMENDMENTNO','AMENDMENTTYPE','amendment_state']
    meta_out = meta[[c for c in keep if c in meta.columns]].copy()
    events = holdings.merge(meta_out, how='left', on='ACCESSION_NUMBER')
    meta_out.to_csv(output_dir / 'filing_events.csv', index=False)
    events.to_csv(output_dir / 'target_holding_events.csv', index=False)

    ambiguous = int(meta_out['amendment_state'].eq('AMENDMENT_UNCLASSIFIED').sum())
    summary = {
        'source_url': url,
        'bulk_size_bytes': len(raw),
        'eligible_filing_count': int(len(meta_out)),
        'base_filing_count': int(meta_out['amendment_state'].eq('BASE').sum()),
        'restatement_count': int(meta_out['amendment_state'].eq('AMENDMENT_RESTATEMENT').sum()),
        'new_holdings_count': int(meta_out['amendment_state'].eq('AMENDMENT_NEW_HOLDINGS').sum()),
        'unclassified_amendment_count': ambiguous,
        'filing_date_complete_rate': float(meta_out['FILING_DATE'].notna().mean()) if len(meta_out) else 0.0,
        'period_complete_rate': float(meta_out['period_of_report'].notna().mean()) if len(meta_out) else 0.0,
        'infotable_total_rows': int(total_info_rows),
        'target_stock_raw_rows': int(matched_raw_rows),
        'target_accession_cusip_events': int(len(events)),
        'deterministic_us_isin_count': us_count,
        'non_us_isin_not_evaluable_count': non_us_count,
        'historical_availability_semantics': 'filing_date_plus_1_calendar_day_conservative_v1',
        'quarter_end_used_as_availability': False,
        'strategy_returns_inspected': False,
        'pit_attach_ready': False,
        'note': 'Filtered as-filed event extraction. Cross-bulk amendment lineage must be consolidated before strategy attachment.',
    }
    (output_dir / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True), encoding='utf-8')
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--url', default=DEFAULT_URL)
    p.add_argument('--universe', type=Path, default=Path('data/processed/current_universe.csv'))
    p.add_argument('--output-dir', type=Path, default=Path('data/13f/bulk_events'))
    a = p.parse_args()
    run(a.url, a.universe, a.output_dir)


if __name__ == '__main__':
    main()
