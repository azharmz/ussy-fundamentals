from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .sec_13f_filing import classify_amendment_state, parse_filing_meta, parse_information_table
from .sec_13f_index import MASTER_URL, filing_text_url, parse_master_index
from .sec_client import SEC_WWW, SecClient


def run(year: int, quarter: int, limit: int, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    client = SecClient()
    index_url = MASTER_URL.format(base=SEC_WWW, year=year, quarter=quarter)
    filings = parse_master_index(client.get_text(index_url))
    if filings.empty:
        raise RuntimeError('No 13F filings in index')
    selected = filings.head(max(int(limit), 1)).copy()

    meta_rows: list[dict] = []
    holdings_rows: list[pd.DataFrame] = []
    failures: list[dict] = []

    for _, row in selected.iterrows():
        url = filing_text_url(str(row['manager_cik']), str(row['accession_nodash']), str(row['archive_filename']))
        try:
            text = client.get_text(url)
            meta = parse_filing_meta(text)
            state = classify_amendment_state(meta)
            h = parse_information_table(text)
            meta_rows.append({
                **row.to_dict(),
                'accepted_at': meta.accepted_at,
                'period_of_report': meta.period_of_report,
                'is_amendment': meta.is_amendment,
                'amendment_type': meta.amendment_type,
                'amendment_state': state,
                'holdings_count': int(len(h)),
                'source_url': url,
            })
            if not h.empty:
                h.insert(0, 'accession_number', row['accession_number'])
                h.insert(0, 'manager_cik', row['manager_cik'])
                holdings_rows.append(h)
        except Exception as exc:
            failures.append({'accession_number': row['accession_number'], 'error_type': type(exc).__name__, 'error': str(exc)[:500], 'source_url': url})

    meta_df = pd.DataFrame(meta_rows)
    holdings_df = pd.concat(holdings_rows, ignore_index=True) if holdings_rows else pd.DataFrame()
    failures_df = pd.DataFrame(failures)
    meta_df.to_csv(output_dir / 'filings_sample.csv', index=False)
    holdings_df.to_csv(output_dir / 'holdings_sample.csv', index=False)
    failures_df.to_csv(output_dir / 'failures.csv', index=False)

    hydrated = int(meta_df['accepted_at'].notna().sum()) if not meta_df.empty else 0
    periods = int(meta_df['period_of_report'].notna().sum()) if not meta_df.empty else 0
    summary = {
        'year': year,
        'quarter': quarter,
        'index_filing_count': int(len(filings)),
        'sample_requested': int(len(selected)),
        'sample_success': int(len(meta_df)),
        'sample_failed': int(len(failures_df)),
        'accepted_at_hydrated': hydrated,
        'period_of_report_hydrated': periods,
        'holdings_rows': int(len(holdings_df)),
        'base_count': int(meta_df['amendment_state'].eq('BASE').sum()) if not meta_df.empty else 0,
        'restatement_count': int(meta_df['amendment_state'].eq('AMENDMENT_RESTATEMENT').sum()) if not meta_df.empty else 0,
        'new_holdings_amendment_count': int(meta_df['amendment_state'].eq('AMENDMENT_NEW_HOLDINGS').sum()) if not meta_df.empty else 0,
        'unclassified_amendment_count': int(meta_df['amendment_state'].str.startswith('AMENDMENT_').sum()) if not meta_df.empty else 0,
        'strategy_returns_inspected': False,
        'pit_attach_ready': False,
        'note': 'Filing-level parser validation only. Full PIT state requires complete quarter hydration and audited amendment lineage.',
    }
    (output_dir / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True), encoding='utf-8')
    return summary


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--year', type=int, default=2026)
    p.add_argument('--quarter', type=int, default=2)
    p.add_argument('--limit', type=int, default=50)
    p.add_argument('--output-dir', type=Path, default=Path('data/13f/sample'))
    a = p.parse_args()
    print(json.dumps(run(a.year, a.quarter, a.limit, a.output_dir), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
