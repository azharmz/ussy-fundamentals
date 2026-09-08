import pandas as pd

from ussy_fundamentals.normalize import (
    _build_ytd_quarters,
    _same_filing_prior,
    classify_period,
)


def test_classify_period():
    assert classify_period(90) == "quarterly"
    assert classify_period(180) == "half_year"
    assert classify_period(270) == "nine_month"
    assert classify_period(365) == "annual"
    assert classify_period(None) == "instant"


def test_classify_period_nan():
    assert classify_period(pd.NA) == "instant"


def test_same_filing_prior_prefers_same_tag_and_unit():
    row = pd.Series({
        "metric": "eps",
        "accession": "A",
        "fiscal_period_end": pd.Timestamp("2025-03-31"),
        "tag": "EarningsPerShareDiluted",
        "unit": "USD/shares",
    })
    facts = pd.DataFrame([
        {
            "metric": "eps",
            "accession": "A",
            "end": pd.Timestamp("2024-03-31"),
            "duration_days": 90,
            "tag": "EarningsPerShareBasic",
            "unit": "USD/shares",
            "tag_priority": 0,
            "is_amendment": False,
            "value": 0.01,
        },
        {
            "metric": "eps",
            "accession": "A",
            "end": pd.Timestamp("2024-03-31"),
            "duration_days": 90,
            "tag": "EarningsPerShareDiluted",
            "unit": "USD/shares",
            "tag_priority": 1,
            "is_amendment": False,
            "value": 2.00,
        },
    ])
    assert _same_filing_prior(row, facts, 60, 120) == 2.00


def test_build_ytd_quarters_reconstructs_q2():
    accepted = pd.Timestamp("2025-08-01")
    report_date = pd.Timestamp("2025-06-30")
    facts = pd.DataFrame([
        {
            "metric": "revenue",
            "tag": "Revenues",
            "unit": "USD",
            "value": 100.0,
            "start": pd.Timestamp("2025-01-01"),
            "end": pd.Timestamp("2025-03-31"),
            "duration_days": 90,
            "accepted_at": pd.Timestamp("2025-05-01"),
            "report_date": pd.Timestamp("2025-03-31"),
            "accession": "Q1",
            "form": "10-Q",
            "fy": 2025,
            "fp": "Q1",
            "is_amendment": False,
            "tag_priority": 0,
            "period_type": "quarterly",
        },
        {
            "metric": "revenue",
            "tag": "Revenues",
            "unit": "USD",
            "value": 230.0,
            "start": pd.Timestamp("2025-01-01"),
            "end": report_date,
            "duration_days": 181,
            "accepted_at": accepted,
            "report_date": report_date,
            "accession": "Q2",
            "form": "10-Q",
            "fy": 2025,
            "fp": "Q2",
            "is_amendment": False,
            "tag_priority": 0,
            "period_type": "half_year",
        },
    ])
    direct_q = facts[facts["period_type"] == "quarterly"].copy()
    out = _build_ytd_quarters(facts, direct_q)
    assert len(out) == 1
    assert out.iloc[0]["fp"] == "Q2"
    assert out.iloc[0]["derived_from"] == "H1_MINUS_Q1"
    assert out.iloc[0]["value"] == 130.0
