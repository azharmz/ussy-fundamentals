import pandas as pd

from ussy_fundamentals.annual_history_verification import (
    _annual_periods,
    _normalize_cik,
    classify_annual_history,
)


def test_normalize_cik_from_csv_float_text():
    assert _normalize_cik("1030894.0") == "0001030894"
    assert _normalize_cik(72633) == "0000072633"


def test_domestic_history_is_extraction_gap_when_usable_annual_lt3():
    domestic = [pd.Timestamp("2022-12-31"), pd.Timestamp("2023-12-31"), pd.Timestamp("2024-12-31")]
    assert classify_annual_history(2, domestic, []) == "DOMESTIC_HISTORY_EXTRACTION_GAP"


def test_fpi_to_domestic_history_is_regime_transition():
    domestic = [pd.Timestamp("2024-12-31"), pd.Timestamp("2025-12-31")]
    fpi = [pd.Timestamp("2022-12-31"), pd.Timestamp("2023-12-31")]
    assert classify_annual_history(2, domestic, fpi) == "ISSUER_REGIME_TRANSITION"


def test_short_current_cik_history_is_not_assumed_to_be_tag_bug():
    domestic = [pd.Timestamp("2024-12-31"), pd.Timestamp("2025-12-31")]
    assert classify_annual_history(2, domestic, []) == "SHORT_CURRENT_CIK_HISTORY"


def test_annual_periods_deduplicate_amendment_same_report_date():
    rows = [
        {"form": "10-K", "reportDate": "2024-12-31"},
        {"form": "10-K/A", "reportDate": "2024-12-31"},
        {"form": "10-K", "reportDate": "2023-12-31"},
        {"form": "10-Q", "reportDate": "2024-09-30"},
    ]
    periods = _annual_periods(rows, {"10-K", "10-K/A"})
    assert periods == [pd.Timestamp("2023-12-31"), pd.Timestamp("2024-12-31")]
