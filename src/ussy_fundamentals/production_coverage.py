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
    requires two usable YoY observations for both EPS and revenue so that latest,
    previous, and simple acceleration logic are possible. A one-quarter staleness
    allowance keeps the intentional Q4 EPS exclusion policy from turning every legacy
    or policy gap into a production failure.

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
        for metric, label in [
            ("quarterly_eps_yoy", "EPS_YOY"),
            ("quarterly_revenue_yoy", "REVENUE_YOY"),
        ]:
            if metric in recent.columns:
                usable_periods = sorted(
                    recent.loc[recent[metric].notna(), "fiscal_period_end"].dropna().unique()
                )
            else:
                usable_periods = []

            count = len(usable_periods)
            latest_usable = pd.Timestamp(usable_periods[-1]) if usable_periods else pd.NaT
            stale = _quarter_distance(latest_period, latest_usable)
            metric_stats[f"{metric}_usable"] = count
            metric_stats[f"{metric}_latest_period"] = latest_usable
            metric_stats[f"{metric}_stale_quarters"] = stale

            if count < required_yoy_observations:
                failures.append(f"QUARTERLY_{label}_INSUFFICIENT")
            elif stale is None or stale > max_stale_quarters:
                failures.append(f"QUARTERLY_{label}_STALE")

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
        f"need >= {CAN_SLIM_REQUIRED_YOY_OBSERVATIONS} usable EPS YoY and revenue YoY | "
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
            "quarterly_eps_yoy_stale_quarters",
            "quarterly_revenue_yoy_usable",
            "quarterly_revenue_yoy_stale_quarters",
            "annual_years",
            "failure_class",
        ]
        print(failures[show].to_string(index=False))
    print()
    return coverage
