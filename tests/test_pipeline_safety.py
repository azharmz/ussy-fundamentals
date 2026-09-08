import pandas as pd

from ussy_fundamentals.pipeline import _drop_nonadditive_derived_eps, _symbol_ciks


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
