import pandas as pd

from ussy_fundamentals.sec_13f_current_state import _build_final_states


def _meta(rows):
    return pd.DataFrame(rows)


def test_restatement_replaces_prior_state():
    meta = _meta([
        {'manager_cik':'0001','period_of_report':'2026-06-30','accepted_at':'2026-08-01T12:00:00','accession_number':'A','form_type':'13F-HR','amendment_state':'BASE'},
        {'manager_cik':'0001','period_of_report':'2026-06-30','accepted_at':'2026-08-05T12:00:00','accession_number':'B','form_type':'13F-HR/A','amendment_state':'AMENDMENT_RESTATEMENT'},
    ])
    pos = {
        'A': {'AAA': {'reported_value_thousands':10.0,'reported_shares':100.0,'line_count':1}},
        'B': {'BBB': {'reported_value_thousands':20.0,'reported_shares':200.0,'line_count':1}},
    }
    state, lineage = _build_final_states(meta, pos)
    assert set(state['cusip']) == {'BBB'}
    assert list(lineage['lineage_action']) == ['REPLACE','REPLACE']


def test_new_holdings_supplements_prior_state():
    meta = _meta([
        {'manager_cik':'0001','period_of_report':'2026-06-30','accepted_at':'2026-08-01T12:00:00','accession_number':'A','form_type':'13F-HR','amendment_state':'BASE'},
        {'manager_cik':'0001','period_of_report':'2026-06-30','accepted_at':'2026-08-05T12:00:00','accession_number':'B','form_type':'13F-HR/A','amendment_state':'AMENDMENT_NEW_HOLDINGS'},
    ])
    pos = {
        'A': {'AAA': {'reported_value_thousands':10.0,'reported_shares':100.0,'line_count':1}},
        'B': {'BBB': {'reported_value_thousands':20.0,'reported_shares':200.0,'line_count':1}},
    }
    state, _ = _build_final_states(meta, pos)
    assert set(state['cusip']) == {'AAA','BBB'}


def test_ambiguous_amendment_excludes_lineage():
    meta = _meta([
        {'manager_cik':'0001','period_of_report':'2026-06-30','accepted_at':'2026-08-01T12:00:00','accession_number':'A','form_type':'13F-HR','amendment_state':'BASE'},
        {'manager_cik':'0001','period_of_report':'2026-06-30','accepted_at':'2026-08-05T12:00:00','accession_number':'B','form_type':'13F-HR/A','amendment_state':'AMENDMENT_UNCLASSIFIED'},
    ])
    state, lineage = _build_final_states(meta, {'A': {}, 'B': {}})
    assert state.empty
    assert 'AMBIGUOUS' in set(lineage['lineage_action'])
