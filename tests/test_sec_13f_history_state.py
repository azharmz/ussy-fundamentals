from pathlib import Path
import sqlite3

from ussy_fundamentals.sec_13f_history_state import _apply_filing, _db


def test_event_state_restatement_and_ambiguity(tmp_path: Path):
    con = _db(tmp_path / 'state.sqlite')
    try:
        e1 = _apply_filing(con, '1', '2025-12-31', 'A', '2026-02-15', 'BASE', {'AAA': (10.0, 100.0)})
        assert e1[-1]['I_manager_count'] == 1

        e2 = _apply_filing(con, '1', '2025-12-31', 'B', '2026-02-16', 'AMENDMENT_RESTATEMENT', {'BBB': (20.0, 200.0)})
        by = {e['cusip']: e for e in e2}
        assert by['AAA']['I_manager_count'] == 0
        assert by['BBB']['I_manager_count'] == 1

        e3 = _apply_filing(con, '1', '2025-12-31', 'C', '2026-02-17', 'AMENDMENT_UNCLASSIFIED', {})
        assert e3[-1]['cusip'] == 'BBB'
        assert e3[-1]['I_manager_count'] == 0
    finally:
        con.close()


def test_new_holdings_adds_to_valid_base(tmp_path: Path):
    con = _db(tmp_path / 'state.sqlite')
    try:
        _apply_filing(con, '1', '2025-12-31', 'A', '2026-02-15', 'BASE', {'AAA': (10.0, 100.0)})
        events = _apply_filing(con, '1', '2025-12-31', 'B', '2026-02-16', 'AMENDMENT_NEW_HOLDINGS', {'BBB': (5.0, 50.0)})
        assert events[-1]['cusip'] == 'BBB'
        rows = con.execute('SELECT cusip FROM manager_position WHERE manager_cik=? AND period=? ORDER BY cusip', ('1','2025-12-31')).fetchall()
        assert rows == [('AAA',), ('BBB',)]
    finally:
        con.close()
