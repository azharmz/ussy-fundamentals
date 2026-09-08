import pandas as pd

from ussy_fundamentals.audit import _cross_cik_overlaps
from ussy_fundamentals.pipeline import (
    _add_missing_reason_metadata,
    _drop_nonadditive_derived_eps,
    _symbol_ciks,
)


def test_drop_nonadditive_derived_eps_keeps_revenue():
    df = pd.DataFrame([
        {"metric": "eps", "period_type": "quarterly_derived_ytd", "value": 1.0},
        {"metric": "eps", "period_type": "quarterly_derived_q4", "value": 2.0},
        {"metric": "revenue", "period_type": "quarterly_derived_ytd", "value": 100.0},
        {"metric": "eps", "period_type": "quarterly", "value": 3.0},
    ])
    out = _drop_nonadditive_derived_eps(df)
    assert len(out) == 2
    assert ((out["metric"] == "revenue") & (out["period_type"] == "quarterly_derived_ytd")).any()
    assert ((out["metric"] == "eps") & (out["period_type"] == "quarterly")).any()
    assert not ((out["metric"] == "eps") & out["period_type"].str.startswith("quarterly_derived")).any()


def test_symbol_ciks_includes_predecessor_history():
    history = pd.DataFrame([
        {"symbol": "XOM", "cik": "0000034088", "role": "predecessor"},
    ])
    ciks = _symbol_ciks("XOM", "0002115436", history)
    assert ciks == ["0002115436", "0000034088"]


def test_missing_reason_metadata_distinguishes_policy_and_comparator_missing():
    wide = pd.DataFrame([
        {
            "fp": "Q4",
            "quarterly_eps": pd.NA,
            "quarterly_revenue": 100.0,
            "quarterly_eps_yoy": pd.NA,
            "quarterly_revenue_yoy": pd.NA,
            "annual_eps": 5.0,
            "annual_eps_growth": pd.NA,
        },
        {
            "fp": "Q2",
            "quarterly_eps": 1.0,
            "quarterly_revenue": pd.NA,
            "quarterly_eps_yoy": pd.NA,
            "quarterly_revenue_yoy": pd.NA,
            "annual_eps": pd.NA,
            "annual_eps_growth": pd.NA,
        },
    ])
    out = _add_missing_reason_metadata(wide)

    assert out.loc[0, "quarterly_eps_missing_reason"] == "Q4_EXCLUDED_POLICY"
    assert out.loc[0, "quarterly_eps_yoy_missing_reason"] == "Q4_EXCLUDED_POLICY"
    assert out.loc[0, "quarterly_revenue_yoy_missing_reason"] == "NO_COMPARATIVE_AVAILABLE"
    assert out.loc[0, "annual_eps_growth_missing_reason"] == "NO_COMPARATIVE_AVAILABLE"

    assert out.loc[1, "quarterly_revenue_missing_reason"] == "NO_DIRECT_QUARTER"
    assert out.loc[1, "quarterly_eps_yoy_missing_reason"] == "NO_COMPARATIVE_AVAILABLE"
    assert out.loc[1, "annual_eps_missing_reason"] == "TAG_NOT_FOUND"
    assert out.loc[1, "annual_eps_growth_missing_reason"] == "TAG_NOT_FOUND"


def test_cross_cik_overlap_detects_same_period_metric_across_ciks():
    long = pd.DataFrame([
        {
            "symbol": "XOM",
            "fiscal_period_end": "2026-06-30",
            "metric": "revenue",
            "cik": "0000034088",
            "accepted_at": "2026-07-25T00:00:00Z",
            "value": 100.0,
        },
        {
            "symbol": "XOM",
            "fiscal_period_end": "2026-06-30",
            "metric": "revenue",
            "cik": "0002115436",
            "accepted_at": "2026-07-30T00:00:00Z",
            "value": 101.0,
        },
        {
            "symbol": "XOM",
            "fiscal_period_end": "2026-06-30",
            "metric": "eps",
            "cik": "0002115436",
            "accepted_at": "2026-07-30T00:00:00Z",
            "value": 1.0,
        },
    ])

    overlaps = _cross_cik_overlaps(long)
    assert len(overlaps) == 2
    assert set(overlaps["metric"]) == {"revenue"}
    assert set(overlaps["cik"]) == {"0000034088", "0002115436"}
