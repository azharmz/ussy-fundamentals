import pandas as pd

from ussy_fundamentals.production_coverage import can_slim_production_coverage


def _symbol_rows(symbol: str, annual_sources: int, latest_eps_missing: bool = False) -> pd.DataFrame:
    periods = pd.date_range("2024-03-31", periods=8, freq="QE")
    rows = []
    for i, period in enumerate(periods):
        source_idx = min(i, max(annual_sources - 1, 0))
        annual_source = f"fy-{source_idx + 1}" if annual_sources else pd.NA
        annual_accepted = pd.Timestamp("2020-02-15", tz="UTC") + pd.DateOffset(years=source_idx)
        rows.append(
            {
                "symbol": symbol,
                "fiscal_period_end": period,
                "quarterly_eps": 1.0 + i / 10,
                "quarterly_eps_yoy": pd.NA if latest_eps_missing and i == 7 else 0.20 + i / 100,
                "quarterly_revenue_yoy": 0.10 + i / 100,
                "annual_eps": 1.0 + source_idx if annual_sources else pd.NA,
                "annual_eps_source_accession": annual_source,
                "annual_eps_accepted_at": annual_accepted if annual_sources else pd.NaT,
            }
        )

    if annual_sources:
        # Ensure exactly the requested number of distinct carried-forward annual states.
        boundaries = pd.Series(range(len(rows))).map(
            lambda i: min((i * annual_sources) // len(rows), annual_sources - 1)
        )
        for i, source_idx in enumerate(boundaries):
            rows[i]["annual_eps"] = 1.0 + source_idx
            rows[i]["annual_eps_source_accession"] = f"fy-{source_idx + 1}"
            rows[i]["annual_eps_accepted_at"] = (
                pd.Timestamp("2020-02-15", tz="UTC") + pd.DateOffset(years=int(source_idx))
            )
    return pd.DataFrame(rows)


def test_can_slim_full_pass_counts_distinct_annual_sources_not_carried_rows():
    wide = _symbol_rows("FULL", annual_sources=5)
    out = can_slim_production_coverage(wide)

    row = out.iloc[0]
    assert row["status"] == "PASS_FULL"
    assert row["annual_years"] == 5
    assert row["quarterly_eps_yoy_usable"] == 8
    assert row["quarterly_eps_yoy_evaluable"] == 8
    assert row["quarterly_revenue_yoy_usable"] == 8


def test_can_slim_three_year_fallback_is_not_a_failure():
    wide = _symbol_rows("FALLBACK", annual_sources=3)
    row = can_slim_production_coverage(wide).iloc[0]

    assert row["status"] == "PASS_3Y_FALLBACK"
    assert row["annual_years"] == 3
    assert pd.isna(row["failure_class"])


def test_can_slim_allows_one_quarter_eps_staleness_for_q4_policy_gap():
    wide = _symbol_rows("Q4POLICY", annual_sources=5, latest_eps_missing=True)
    row = can_slim_production_coverage(wide).iloc[0]

    assert row["status"] == "PASS_FULL"
    assert row["quarterly_eps_yoy_stale_quarters"] == 1
    assert row["quarterly_eps_yoy_usable"] == 7


def test_can_slim_nonpositive_eps_base_is_evaluable_not_missing_coverage():
    wide = _symbol_rows("NEGBASE", annual_sources=5)
    wide["quarterly_eps_yoy"] = pd.NA
    wide.loc[wide.index[0], "quarterly_eps_yoy"] = 0.25

    # Prior-year EPS for the four 2025 quarters is nonpositive. Percentage YoY is
    # intentionally undefined, but the underlying current/prior facts are present.
    wide.loc[wide.index[:4], "quarterly_eps"] = [-0.10, -0.20, -0.30, -0.40]
    wide.loc[wide.index[4:], "quarterly_eps"] = [0.05, -0.10, 0.20, 0.30]

    row = can_slim_production_coverage(wide).iloc[0]

    assert row["status"] == "PASS_FULL"
    assert row["quarterly_eps_yoy_usable"] == 1
    assert row["quarterly_eps_yoy_nonpositive_base"] == 4
    assert row["quarterly_eps_yoy_evaluable"] == 5
    assert row["quarterly_eps_yoy_stale_quarters"] == 0


def test_can_slim_fails_short_annual_history_and_material_recent_gap():
    wide = _symbol_rows("SHORT", annual_sources=2)
    wide.loc[wide.index[-3:], "quarterly_revenue_yoy"] = pd.NA
    row = can_slim_production_coverage(wide).iloc[0]

    assert row["status"] == "FAIL_PRODUCTION_COVERAGE"
    classes = set(row["failure_class"].split(";"))
    assert "ANNUAL_LT_3Y" in classes
    assert "QUARTERLY_REVENUE_YOY_STALE" in classes
