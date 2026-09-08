from __future__ import annotations

import pandas as pd


CAN_SLIM_QUARTERLY_WINDOW = 8
CAN_SLIM_REQUIRED_YOY_OBSERVATIONS = 2
CAN_SLIM_MAX_STALE_QUARTERS = 1
CAN_SLIM_ANNUAL_FULL_YEARS = 5
CAN_SLIM_ANNUAL_FALLBACK_YEARS = 3


def _quarter_distance(latest_period: pd.Timestamp, observed_period: pd.Timestamp) -> int | None:
    if pd.isna(latest_period) or pd.isna(observed_period):
        return None
    latest = pd.Period(latest_period, freq="Q")
    observed = pd.Period(observed_period, freq="Q")
    return int(latest.ordinal - observed.ordinal)


def _distinct_annual_sources(symbol_rows: pd.DataFrame) -> pd.DataFrame:
    """Return one row per annual EPS state that was actually sourced from a filing.

    annual_eps is carried forward onto quarterly rows, so counting non-null rows would
    materially overstate annual history. Prefer the source accession as the identity;
    accepted-at is a defensive fallback for older datasets.
    """
    if "annual_eps" not in symbol_rows.columns:
        return pd.DataFrame()

    annual = symbol_rows[symbol_rows["annual_eps"].notna()].copy()
    if annual.empty:
        return annual

    if "annual_eps_source_accession" in annual.columns:
        source = annual["annual_eps_source_accession"].astype("string")
    else:
        source = pd.Series(pd.NA, index=annual.index, dtype="string")

    if "annual_eps_accepted_at" in annual.columns:
        accepted = pd.to_datetime(annual["annual_eps_accepted_at"], errors="coerce", utc=True)
        fallback = accepted.astype("string")
        source = source.fillna(fallback)
        annual["_annual_source_accepted_at"] = accepted
    else:
        annual["_annual_source_accepted_at"] = pd.NaT

    annual["_annual_source"] = source
    annual = annual[annual["_annual_source"].notna()].copy()
    if annual.empty:
        return annual

    annual = annual.sort_values("_annual_source_accepted_at")
    return annual.drop_duplicates("_annual_source", keep="last")


def _eps_nonpositive_base_periods(symbol_rows: pd.DataFrame, recent: pd.DataFrame) -> list[pd.Timestamp]:
    """Return recent periods whose EPS YoY is intentionally non-numeric.

    normalize.py deliberately does not calculate percentage EPS growth when the
    prior-year EPS denominator is <= 0. That is a feature-semantics decision, not a
    data-coverage failure. For production readiness, count such a period as evaluable
    when both the current-quarter EPS and a prior-year EPS observation are present.
    """
    if "quarterly_eps" not in symbol_rows.columns or "quarterly_eps_yoy" not in recent.columns:
        return []

    s = symbol_rows.copy()
    s["fiscal_period_end"] = pd.to_datetime(s["fiscal_period_end"], errors="coerce")
    out: list[pd.Timestamp] = []

    candidates = recent[
        recent["quarterly_eps_yoy"].isna() & recent["quarterly_eps"].notna()
    ].copy()

    for period in candidates["fiscal_period_end"].dropna().unique():
        period = pd.Timestamp(period)
        prior = s[
            (s["fiscal_period_end"] >= period - pd.Timedelta(days=400))
            & (s["fiscal_period_end"] <= period - pd.Timedelta(days=330))
            & s["quarterly_eps"].notna()
        ].copy()
        if prior.empty:
            continue

        prior = prior.sort_values("fiscal_period_end")
        prior_value = prior.iloc[-1]["quarterly_eps"]
        try:
            if float(prior_value) <= 0:
                out.append(period)
        except (TypeError, ValueError):
            continue

    return sorted(set(out))


def can_slim_production_coverage(
    wide: pd.DataFrame,
    quarterly_window: int = CAN_SLIM_QUARTERLY_WINDOW,
    required_yoy_observations: int = CAN_SLIM_REQUIRED_YOY_OBSERVATIONS,
    max_stale_quarters: int = CAN_SLIM_MAX_STALE_QUARTERS,
    annual_full_years: int = CAN_SLIM_ANNUAL_FULL_YEARS,
    annual_fallback_years: int = CAN_SLIM_ANNUAL_FALLBACK_YEARS,
) -> pd.DataFrame:
    """Classify symbol readiness for the CAN SLIM strategy window.

    The audit is deliberately strategy-aware rather than a full-history completeness
    test. Quarterly readiness is based only on the most recent fiscal periods and
    requires two evaluable observations for EPS and two usable YoY observations for
    revenue so that latest, previous, and simple acceleration logic are possible.

    EPS percentage growth is intentionally undefined when prior-year EPS <= 0. Those
    periods count as evaluable coverage when both current and prior EPS facts exist;
    downstream strategy logic can treat the negative-base/turnaround state separately.
    A one-quarter staleness allowance keeps the intentional Q4 EPS exclusion policy from
    turning every policy gap into a production failure.

    Annual readiness counts distinct source filings, not carried-forward rows:
    5+ years -> PASS_FULL, 3-4 years -> PASS_3Y_FALLBACK, <3 years -> failure.
    """
    required = {"symbol", "fiscal_period_end"}
    if wide.empty or not required.issubset(wide.columns):
        return pd.DataFrame()

    x = wide.copy()
    x["symbol"] = x["symbol"].astype(str).str.upper()
    x["fiscal_period_end"] = pd.to_datetime(x["fiscal_period_end"], errors="coerce")
    x = x.dropna(subset=["symbol", "fiscal_period_end"])

    rows: list[dict] = []
    for symbol, s in x.groupby("symbol", sort=True):
        periods = sorted(s["fiscal_period_end"].dropna().unique())
        recent_periods = periods[-quarterly_window:]
        recent = s[s["fiscal_period_end"].isin(recent_periods)].copy()
        latest_period = pd.Timestamp(recent_periods[-1]) if recent_periods else pd.NaT

        metric_stats: dict[str, int | pd.Timestamp | None] = {}
        failures: list[str] = []

        # EPS: distinguish numeric YoY from intentional nonpositive-base cases.
        if "quarterly_eps_yoy" in recent.columns:
            eps_numeric_periods = sorted(
                recent.loc[recent["quarterly_eps_yoy"].notna(), "fiscal_period_end"].dropna().unique()
            )
        else:
            eps_numeric_periods = []
        eps_nonpositive_base_periods = _eps_nonpositive_base_periods(s, recent)
        eps_evaluable_periods = sorted(
            set(pd.Timestamp(p) for p in eps_numeric_periods)
            | set(eps_nonpositive_base_periods)
        )
        eps_latest = eps_evaluable_periods[-1] if eps_evaluable_periods else pd.NaT
        eps_stale = _quarter_distance(latest_period, eps_latest)
        metric_stats["quarterly_eps_yoy_usable"] = len(eps_numeric_periods)
        metric_stats["quarterly_eps_yoy_nonpositive_base"] = len(eps_nonpositive_base_periods)
        metric_stats["quarterly_eps_yoy_evaluable"] = len(eps_evaluable_periods)
        metric_stats["quarterly_eps_yoy_latest_period"] = eps_latest
        metric_stats["quarterly_eps_yoy_stale_quarters"] = eps_stale

        if len(eps_evaluable_periods) < required_yoy_observations:
            failures.append("QUARTERLY_EPS_YOY_INSUFFICIENT")
        elif eps_stale is None or eps_stale > max_stale_quarters:
            failures.append("QUARTERLY_EPS_YOY_STALE")

        # Revenue: ordinary numeric YoY availability is sufficient.
        if "quarterly_revenue_yoy" in recent.columns:
            revenue_usable_periods = sorted(
                recent.loc[recent["quarterly_revenue_yoy"].notna(), "fiscal_period_end"].dropna().unique()
            )
        else:
            revenue_usable_periods = []
        revenue_count = len(revenue_usable_periods)
        revenue_latest = pd.Timestamp(revenue_usable_periods[-1]) if revenue_usable_periods else pd.NaT
        revenue_stale = _quarter_distance(latest_period, revenue_latest)
        metric_stats["quarterly_revenue_yoy_usable"] = revenue_count
        metric_stats["quarterly_revenue_yoy_latest_period"] = revenue_latest
        metric_stats["quarterly_revenue_yoy_stale_quarters"] = revenue_stale

        if revenue_count < required_yoy_observations:
            failures.append("QUARTERLY_REVENUE_YOY_INSUFFICIENT")
        elif revenue_stale is None or revenue_stale > max_stale_quarters:
            failures.append("QUARTERLY_REVENUE_YOY_STALE")

        annual = _distinct_annual_sources(s)
        annual_years = len(annual)
        if annual_years < annual_fallback_years:
            failures.append("ANNUAL_LT_3Y")

        if failures:
            status = "FAIL_PRODUCTION_COVERAGE"
        elif annual_years >= annual_full_years:
            status = "PASS_FULL"
        else:
            status = "PASS_3Y_FALLBACK"

        rows.append(
            {
                "symbol": symbol,
                "status": status,
                "quarterly_window": quarterly_window,
                "quarter_periods_present": len(recent_periods),
                "latest_fiscal_period": latest_period,
                **metric_stats,
                "annual_years": annual_years,
                "failure_class": ";".join(failures) if failures else pd.NA,
            }
        )

    return pd.DataFrame(rows)


def print_can_slim_production_coverage(wide: pd.DataFrame) -> pd.DataFrame:
    coverage = can_slim_production_coverage(wide)

    print("=== CAN SLIM PRODUCTION COVERAGE ===")
    print(
        f"Quarterly window: last {CAN_SLIM_QUARTERLY_WINDOW} fiscal quarters | "
        f"need >= {CAN_SLIM_REQUIRED_YOY_OBSERVATIONS} evaluable EPS periods and usable revenue YoY | "
        f"max staleness {CAN_SLIM_MAX_STALE_QUARTERS} quarter"
    )
    print(
        f"Annual window: {CAN_SLIM_ANNUAL_FULL_YEARS} FY target | "
        f"{CAN_SLIM_ANNUAL_FALLBACK_YEARS} FY fallback"
    )

    if coverage.empty:
        print("No symbols available for production coverage audit")
        print()
        return coverage

    print("\nStatus summary:")
    print(coverage["status"].value_counts().to_string())

    nonpositive = coverage["quarterly_eps_yoy_nonpositive_base"].fillna(0).astype(int)
    if nonpositive.sum() > 0:
        print("\nEPS YoY nonpositive-base periods (data present; percentage intentionally undefined):")
        print(f"Periods: {int(nonpositive.sum())} | Symbols: {int((nonpositive > 0).sum())}")

    failures = coverage[coverage["status"] == "FAIL_PRODUCTION_COVERAGE"]
    if not failures.empty:
        print("\nFailure classes:")
        exploded = failures["failure_class"].dropna().str.split(";").explode()
        print(exploded.value_counts().to_string())
        print("\nFailed symbols:")
        show = [
            "symbol",
            "quarter_periods_present",
            "quarterly_eps_yoy_usable",
            "quarterly_eps_yoy_nonpositive_base",
            "quarterly_eps_yoy_evaluable",
            "quarterly_eps_yoy_stale_quarters",
            "quarterly_revenue_yoy_usable",
            "quarterly_revenue_yoy_stale_quarters",
            "annual_years",
            "failure_class",
        ]
        print(failures[show].to_string(index=False))
    print()
    return coverage
