from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd

from .sec_13f_filing import classify_amendment_state, parse_filing_meta, parse_information_table
from .sec_13f_index import MASTER_URL, filing_text_url, parse_master_index
from .sec_client import SEC_WWW, SecClient


def _normalize_period(value: str | None) -> str | None:
    if not value:
        return None
    ts = pd.to_datetime(value, errors='coerce')
    return None if pd.isna(ts) else ts.date().isoformat()


def _filing_state(form_type: str, meta) -> str:
    if str(form_type).upper() == '13F-HR':
        return 'BASE'
    return classify_amendment_state(meta)


def _get_with_retry(client: SecClient, url: str, attempts: int = 4) -> str:
    last = None
    for i in range(attempts):
        try:
            return client.get_text(url)
        except Exception as exc:  # retain exact failure type in caller
            last = exc
            if i + 1 < attempts:
                time.sleep(min(2 ** i, 8))
    assert last is not None
    raise last


def _target_map(universe_csv: Path) -> tuple[dict[str, list[dict]], int, int]:
    u = pd.read_csv(universe_csv, dtype='string')
    rows: dict[str, list[dict]] = defaultdict(list)
    us = 0
    non_us = 0
    for _, r in u.iterrows():
        sid = str(r.get('security_id') or '').strip().upper()
        ticker = str(r.get('ticker') or '').strip().upper()
        if len(sid) == 12 and sid.startswith('US'):
            cusip = sid[2:11]
            rows[cusip].append({'security_id': sid, 'ticker': ticker})
            us += 1
        else:
            non_us += 1
    return dict(rows), us, non_us


def _aggregate_filing_holdings(df: pd.DataFrame, target_cusips: set[str]) -> dict[str, dict]:
    if df.empty:
        return {}
    work = df.copy()
    work = work[work['cusip'].isin(target_cusips)]
    if 'put_call' in work.columns:
        work = work[work['put_call'].isna() | work['put_call'].astype('string').str.strip().eq('')]
    if work.empty:
        return {}
    agg = work.groupby('cusip', dropna=False).agg(
        reported_value_thousands=('value_thousands', 'sum'),
        reported_shares=('shares_or_principal', 'sum'),
        line_count=('cusip', 'size'),
    ).reset_index()
    return {
        str(r['cusip']): {
            'reported_value_thousands': float(r['reported_value_thousands']) if pd.notna(r['reported_value_thousands']) else None,
            'reported_shares': float(r['reported_shares']) if pd.notna(r['reported_shares']) else None,
            'line_count': int(r['line_count']),
        }
        for _, r in agg.iterrows()
    }


def _build_final_states(meta_df: pd.DataFrame, filing_positions: dict[str, dict[str, dict]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    output: list[dict] = []
    lineage_rows: list[dict] = []
    if meta_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    meta_df = meta_df.copy()
    meta_df['accepted_sort'] = pd.to_datetime(meta_df['accepted_at'], errors='coerce')
    for (manager_cik, period), grp in meta_df.groupby(['manager_cik', 'period_of_report'], dropna=False):
        grp = grp.sort_values(['accepted_sort', 'accession_number'], na_position='last')
        current: dict[str, dict] = {}
        lineage_valid = True
        base_seen = 0
        last_accepted = None
        for _, row in grp.iterrows():
            accession = str(row['accession_number'])
            state = str(row['amendment_state'])
            positions = filing_positions.get(accession, {})
            action = 'NONE'
            if state in {'BASE', 'AMENDMENT_RESTATEMENT'}:
                if state == 'BASE':
                    base_seen += 1
                current = {k: dict(v) for k, v in positions.items()}
                action = 'REPLACE'
            elif state == 'AMENDMENT_NEW_HOLDINGS':
                action = 'ADD'
                for cusip, pos in positions.items():
                    if cusip not in current:
                        current[cusip] = dict(pos)
                    else:
                        for field in ('reported_value_thousands', 'reported_shares'):
                            a = current[cusip].get(field)
                            b = pos.get(field)
                            if a is None:
                                current[cusip][field] = b
                            elif b is not None:
                                current[cusip][field] = a + b
                        current[cusip]['line_count'] = int(current[cusip].get('line_count', 0)) + int(pos.get('line_count', 0))
            else:
                lineage_valid = False
                action = 'AMBIGUOUS'
            if pd.notna(row['accepted_sort']):
                last_accepted = row['accepted_sort'].isoformat()
            lineage_rows.append({
                'manager_cik': manager_cik,
                'period_of_report': period,
                'accession_number': accession,
                'accepted_at': row['accepted_at'],
                'form_type': row['form_type'],
                'amendment_state': state,
                'lineage_action': action,
            })

        if base_seen > 1:
            lineage_valid = False
        if not lineage_valid or pd.isna(period):
            continue
        for cusip, pos in current.items():
            output.append({
                'manager_cik': manager_cik,
                'period_of_report': period,
                'available_at': last_accepted,
                'cusip': cusip,
                **pos,
            })
    return pd.DataFrame(output), pd.DataFrame(lineage_rows)


def run(year: int, quarter: int, universe_csv: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    target_map, us_count, non_us_count = _target_map(universe_csv)
    target_cusips = set(target_map)
    client = SecClient()
    index_url = MASTER_URL.format(base=SEC_WWW, year=year, quarter=quarter)
    index_df = parse_master_index(client.get_text(index_url))
    if index_df.empty:
        raise RuntimeError('No 13F filings in index')

    meta_rows: list[dict] = []
    failures: list[dict] = []
    filing_positions: dict[str, dict[str, dict]] = {}

    for i, (_, row) in enumerate(index_df.iterrows(), start=1):
        url = filing_text_url(str(row['manager_cik']), str(row['accession_nodash']), str(row['archive_filename']))
        try:
            text = _get_with_retry(client, url)
            meta = parse_filing_meta(text)
            state = _filing_state(str(row['form_type']), meta)
            period = _normalize_period(meta.period_of_report)
            holdings = parse_information_table(text)
            accession = str(row['accession_number'])
            filing_positions[accession] = _aggregate_filing_holdings(holdings, target_cusips)
            meta_rows.append({
                **row.to_dict(),
                'accepted_at': meta.accepted_at,
                'period_of_report': period,
                'is_amendment': meta.is_amendment,
                'amendment_type': meta.amendment_type,
                'amendment_state': state,
                'matched_target_cusips': len(filing_positions[accession]),
                'source_url': url,
            })
        except Exception as exc:
            failures.append({
                'accession_number': row['accession_number'],
                'manager_cik': row['manager_cik'],
                'form_type': row['form_type'],
                'error_type': type(exc).__name__,
                'error': str(exc)[:500],
                'source_url': url,
            })
        if i % 500 == 0:
            print(f'processed {i}/{len(index_df)} filings; failures={len(failures)}', flush=True)

    meta_df = pd.DataFrame(meta_rows)
    failures_df = pd.DataFrame(failures)
    final_state, lineage = _build_final_states(meta_df, filing_positions)

    if not final_state.empty:
        sponsor = final_state.groupby(['period_of_report', 'cusip'], dropna=False).agg(
            I_manager_count=('manager_cik', 'nunique'),
            I_reported_value_total=('reported_value_thousands', 'sum'),
            I_reported_share_total=('reported_shares', 'sum'),
            I_available_at=('available_at', 'max'),
        ).reset_index()
        mapped_rows: list[dict] = []
        for _, r in sponsor.iterrows():
            for ident in target_map.get(str(r['cusip']), []):
                mapped_rows.append({**ident, **r.to_dict(), 'I_mapping_method': 'US_ISIN_BODY_TO_CUSIP9', 'I_state': 'EVALUABLE'})
        sponsor_mapped = pd.DataFrame(mapped_rows)
    else:
        sponsor = pd.DataFrame()
        sponsor_mapped = pd.DataFrame()

    meta_df.to_csv(output_dir / 'filings.csv', index=False)
    failures_df.to_csv(output_dir / 'failures.csv', index=False)
    lineage.to_csv(output_dir / 'amendment_lineage.csv', index=False)
    sponsor.to_csv(output_dir / 'sponsorship_by_cusip.csv', index=False)
    sponsor_mapped.to_csv(output_dir / 'sponsorship_mapped.csv', index=False)

    successful = len(meta_df)
    accepted_ok = int(meta_df['accepted_at'].notna().sum()) if successful else 0
    period_ok = int(meta_df['period_of_report'].notna().sum()) if successful else 0
    amendments = meta_df['form_type'].astype('string').str.upper().eq('13F-HR/A') if successful else pd.Series(dtype=bool)
    amendment_total = int(amendments.sum()) if successful else 0
    amendment_classified = int(meta_df.loc[amendments, 'amendment_state'].isin(['AMENDMENT_RESTATEMENT', 'AMENDMENT_NEW_HOLDINGS']).sum()) if amendment_total else 0
    ambiguous_lineages = int((lineage['lineage_action'] == 'AMBIGUOUS').sum()) if not lineage.empty else 0
    fetch_rate = successful / len(index_df)
    accepted_rate = accepted_ok / successful if successful else 0
    period_rate = period_ok / successful if successful else 0
    amendment_rate = amendment_classified / amendment_total if amendment_total else 1.0

    # Pre-specified data-quality gate for I1 ingestion readiness; no strategy returns are inspected.
    data_gate_pass = (
        fetch_rate >= 0.99
        and accepted_rate == 1.0
        and period_rate == 1.0
        and amendment_rate >= 0.99
        and ambiguous_lineages == 0
    )

    latest_period = None
    latest_mapped = 0
    if not sponsor_mapped.empty:
        latest_period = sponsor_mapped['period_of_report'].dropna().max()
        latest_mapped = int(sponsor_mapped.loc[sponsor_mapped['period_of_report'].eq(latest_period), 'security_id'].nunique())

    summary = {
        'source_url': index_url,
        'year': year,
        'filing_calendar_quarter': quarter,
        'index_filing_count': int(len(index_df)),
        'filing_success_count': successful,
        'filing_failure_count': int(len(failures_df)),
        'fetch_success_rate': fetch_rate,
        'accepted_at_complete_rate': accepted_rate,
        'period_of_report_complete_rate': period_rate,
        'amendment_count': amendment_total,
        'amendment_classified_count': amendment_classified,
        'amendment_classified_rate': amendment_rate,
        'ambiguous_lineage_events': ambiguous_lineages,
        'universe_count': us_count + non_us_count,
        'deterministic_us_isin_count': us_count,
        'non_us_isin_not_evaluable_count': non_us_count,
        'latest_period_of_report': latest_period,
        'latest_period_mapped_security_count': latest_mapped,
        'data_gate_pass': bool(data_gate_pass),
        'strategy_returns_inspected': False,
        'fwd1_modified': False,
        'note': 'PIT ingestion/data-quality evidence only. No I performance rule is evaluated here.',
    }
    (output_dir / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True), encoding='utf-8')
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--year', type=int, default=2026)
    p.add_argument('--quarter', type=int, default=3)
    p.add_argument('--universe', type=Path, default=Path('data/processed/current_universe.csv'))
    p.add_argument('--output-dir', type=Path, default=Path('data/13f/current_state'))
    a = p.parse_args()
    run(a.year, a.quarter, a.universe, a.output_dir)


if __name__ == '__main__':
    main()
