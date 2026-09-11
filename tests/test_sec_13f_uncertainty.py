from pathlib import Path

from ussy_fundamentals.sec_13f_uncertainty import _apply, _db


def test_ambiguous_marks_union_and_restatement_clears(tmp_path: Path):
    con = _db(tmp_path / 'u.sqlite')
    try:
        # Base holding is known and therefore not uncertain.
        assert _apply(
            con,
            manager='1', period='2025-12-31', accession='A', available_on='2026-02-15',
            state='BASE', incoming={'AAA'}
        ) == []

        # Unknown amendment could alter both the old holding and newly reported holding.
        events = _apply(
            con,
            manager='1', period='2025-12-31', accession='B', available_on='2026-02-16',
            state='AMENDMENT_UNCLASSIFIED', incoming={'BBB'}
        )
        by = {e['cusip']: e for e in events}
        assert set(by) == {'AAA', 'BBB'}
        assert all(e['I_uncertain_manager_count'] == 1 for e in events)
        assert all(e['uncertainty_delta'] == 1 for e in events)

        # A valid restatement restores a determinate manager-period state and clears mask.
        events = _apply(
            con,
            manager='1', period='2025-12-31', accession='C', available_on='2026-02-17',
            state='AMENDMENT_RESTATEMENT', incoming={'CCC'}
        )
        by = {e['cusip']: e for e in events}
        assert set(by) == {'AAA', 'BBB'}
        assert all(e['I_uncertain_manager_count'] == 0 for e in events)
        assert all(e['uncertainty_delta'] == -1 for e in events)
    finally:
        con.close()


def test_unclassified_without_base_masks_reported_names(tmp_path: Path):
    con = _db(tmp_path / 'u.sqlite')
    try:
        events = _apply(
            con,
            manager='9', period='2025-09-30', accession='X', available_on='2025-11-15',
            state='AMENDMENT_UNCLASSIFIED', incoming={'AAA', 'BBB'}
        )
        assert {e['cusip'] for e in events} == {'AAA', 'BBB'}
        assert all(e['I_uncertain_manager_count'] == 1 for e in events)
    finally:
        con.close()
