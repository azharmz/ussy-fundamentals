import pandas as pd

import ussy_fundamentals.annual_fallback as annual_fallback


def _base_row(annual_accepted: str, annual_eps: float) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "accepted_at": pd.Timestamp("2025-08-01T20:00:00Z"),
            "annual_eps": annual_eps,
            "annual_eps_growth": 0.10,
            "annual_eps_accepted_at": pd.Timestamp(annual_accepted),
            "annual_eps_filed_at": pd.Timestamp(annual_accepted).tz_localize(None),
            "annual_eps_source_accession": "DIRECT-OLD",
            "annual_growth_source": "SAME_FILING_COMPARATIVE",
        }
    ])


def _derived_state() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "accepted_at": pd.Timestamp("2025-03-01T20:00:00Z"),
            "filed_at": pd.Timestamp("2025-03-01"),
            "accession": "DERIVED-NEW",
            "annual_eps": 2.5,
            "annual_eps_growth": 0.25,
            "annual_growth_source": "DERIVED_NET_INCOME_OVER_SHARES",
        }
    ])


def test_newer_derived_state_replaces_stale_carried_direct(monkeypatch):
    normalized = _base_row("2023-03-01T20:00:00Z", 1.0)
    monkeypatch.setattr(annual_fallback, "_derived_state", lambda *_: _derived_state())

    out = annual_fallback.fill_missing_annual_eps(normalized, {}, [])

    assert out.iloc[0]["annual_eps"] == 2.5
    assert out.iloc[0]["annual_eps_source_accession"] == "DERIVED-NEW"
    assert out.iloc[0]["annual_growth_source"] == "DERIVED_NET_INCOME_OVER_SHARES"


def test_equally_or_more_recent_direct_state_remains_preferred(monkeypatch):
    normalized = _base_row("2025-04-01T20:00:00Z", 3.0)
    monkeypatch.setattr(annual_fallback, "_derived_state", lambda *_: _derived_state())

    out = annual_fallback.fill_missing_annual_eps(normalized, {}, [])

    assert out.iloc[0]["annual_eps"] == 3.0
    assert out.iloc[0]["annual_eps_source_accession"] == "DIRECT-OLD"
    assert out.iloc[0]["annual_growth_source"] == "SAME_FILING_COMPARATIVE"
