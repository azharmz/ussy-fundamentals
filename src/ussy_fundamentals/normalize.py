from __future__ import annotations

import pandas as pd

FORMS = {"10-Q", "10-Q/A", "10-K", "10-K/A"}
EPS_TAGS = ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic"]
REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
    "SalesAndOtherOperatingRevenue",
]
NORMALIZER_VERSION = "sec-ca-v0.6.1"


def _dt(x):
    return pd.to_datetime(x, errors="coerce") if x else pd.NaT


def _duration_days(start, end):
    a, b = _dt(start), _dt(end)
    return None if pd.isna(a) or pd.isna(b) else (b - a).days


def accession_index(filings: list[dict]) -> dict[str, dict]:
    return {x.get("accessionNumber"): x for x in filings if x.get("accessionNumber")}


def fact_rows(companyfacts: dict, tags: list[str], metric: str, filings: dict[str, dict]) -> pd.DataFrame:
    facts = companyfacts.get("facts", {})
    namespaces = ["us-gaap"] + sorted(ns for ns in facts if ns != "us-gaap")
    rows = []
    for priority, tag in enumerate(tags):
        for namespace_rank, namespace in enumerate(namespaces):
            concept = facts.get(namespace, {}).get(tag)
            if not concept:
                continue
            for unit, entries in concept.get("units", {}).items():
                for f in entries:
                    if f.get("form") not in FORMS:
                        continue
                    meta = filings.get(f.get("accn"), {})
                    rows.append({
                        "metric": metric,
                        "tag": tag,
                        "taxonomy": namespace,
                        "tag_priority": priority * 1000 + namespace_rank,
                        "unit": unit,
                        "value": f.get("val"),
                        "start": _dt(f.get("start")),
                        "end": _dt(f.get("end")),
                        "duration_days": _duration_days(f.get("start"), f.get("end")),
                        "filed_at": _dt(meta.get("filingDate") or f.get("filed")),
                        "accepted_at": _dt(meta.get("acceptanceDateTime") or meta.get("acceptanceDatetime")),
                        "report_date": _dt(meta.get("reportDate")),
                        "accession": f.get("accn"),
                        "form": f.get("form"),
                        "fy": f.get("fy"),
                        "fp": f.get("fp"),
                        "frame": f.get("frame"),
                        "is_amendment": str(f.get("form", "")).endswith("/A"),
                    })
    return pd.DataFrame(rows)


def classify_period(days):
    if days is None or pd.isna(days):
        return "instant"
    if 60 <= days <= 120:
        return "quarterly"
    if 150 <= days <= 220:
        return "half_year"
    if 240 <= days <= 310:
        return "nine_month"
    if 300 <= days <= 430:
        return "annual"
    return "other"


def _dedupe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.sort_values(["tag_priority", "is_amendment", "accepted_at"], ascending=[True, True, False], na_position="last")
    return df.groupby(["metric", "accession", "end"], dropna=False, as_index=False).head(1)


def _current_period_only(df: pd.DataFrame) -> pd.DataFrame:
    """Keep facts that can defensibly represent the filing's current reported period.

    SEC Companyfacts can carry older comparative facts in later accessions. Matching
    end == reportDate is necessary but not sufficient when historical submission metadata
    is imperfect. For ordinary (non-amended) filings, reject observations accepted more
    than 200 days after period end. Amendments remain valid later restatement events.
    """
    if df.empty:
        return df
    report_date = pd.to_datetime(df["report_date"], errors="coerce")
    end = pd.to_datetime(df["end"], errors="coerce")
    accepted = pd.to_datetime(df["accepted_at"], errors="coerce", utc=True)
    end_utc = pd.to_datetime(df["end"], errors="coerce", utc=True)
    amendment = df.get("is_amendment", pd.Series(False, index=df.index)).fillna(False).astype(bool)

    matches_report = report_date.notna() & end.eq(report_date)
    lag_days = (accepted - end_utc).dt.total_seconds() / 86400.0
    plausible_lag = amendment | accepted.isna() | lag_days.between(0, 200, inclusive="both")
    return df[matches_report & plausible_lag].copy()


def _same_filing_prior(row: pd.Series, all_period_facts: pd.DataFrame, min_duration: int, max_duration: int):
    """Find prior-year comparable as presented in the same accession."""
    target_lo = row["fiscal_period_end"] - pd.Timedelta(days=400)
    target_hi = row["fiscal_period_end"] - pd.Timedelta(days=330)
    base = all_period_facts[
        (all_period_facts["metric"] == row["metric"])
        & (all_period_facts["accession"] == row["accession"])
        & (all_period_facts["end"] >= target_lo)
        & (all_period_facts["end"] <= target_hi)
        & (all_period_facts["duration_days"].between(min_duration, max_duration, inclusive="both"))
    ].copy()
    if base.empty:
        return None

    exact = base[(base["tag"] == row.get("tag")) & (base["unit"] == row.get("unit"))].copy()
    c = exact if not exact.empty else base[base["unit"] == row.get("unit")].copy()
    if c.empty:
        return None

    c = c.sort_values(["tag_priority", "is_amendment"], ascending=[True, True])
    return c.iloc[0]["value"]


def _build_ytd_quarters(facts: pd.DataFrame, direct_q: pd.DataFrame) -> pd.DataFrame:
    """Reconstruct Q2/Q3 from cumulative 6M/9M facts when direct quarter facts are absent."""
    if facts.empty:
        return pd.DataFrame()

    ytd = facts[
        facts["form"].isin(["10-Q", "10-Q/A"])
        & facts["period_type"].isin(["half_year", "nine_month"])
    ].copy()
    ytd = _current_period_only(ytd)
    if ytd.empty:
        return pd.DataFrame()

    out = []
    direct = direct_q.copy()

    for _, row in ytd.iterrows():
        if pd.isna(row["accepted_at"]):
            continue

        same = facts[
            (facts["metric"] == row["metric"])
            & (facts["tag"] == row["tag"])
            & (facts["unit"] == row["unit"])
            & (facts["accepted_at"] <= row["accepted_at"])
        ].copy()

        if row["period_type"] == "half_year":
            q1 = same[
                (same["period_type"] == "quarterly")
                & (same["end"] < row["end"])
                & (same["end"] >= row["start"])
            ].sort_values(["end", "accepted_at"]).tail(1)
            if q1.empty:
                continue
            value = row["value"] - q1.iloc[-1]["value"]
            fp = "Q2"
            derived_from = "H1_MINUS_Q1"
        else:
            h1 = same[
                (same["period_type"] == "half_year")
                & (same["end"] < row["end"])
                & (same["end"] >= row["start"])
            ].sort_values(["end", "accepted_at"]).tail(1)
            if h1.empty:
                continue
            value = row["value"] - h1.iloc[-1]["value"]
            fp = "Q3"
            derived_from = "9M_MINUS_H1"

        exists = direct[(direct["metric"] == row["metric"]) & (direct["end"] == row["end"])]
        if not exists.empty:
            continue

        derived = row.copy()
        derived["value"] = value
        derived["duration_days"] = 90
        derived["period_type"] = "quarterly_derived_ytd"
        derived["fp"] = fp
        derived["derived_from"] = derived_from
        out.append(derived)

    return pd.DataFrame(out)


def _build_annual_eps_state(all_annual_facts: pd.DataFrame) -> pd.DataFrame:
    if all_annual_facts.empty:
        return pd.DataFrame()

    current = _dedupe(_current_period_only(all_annual_facts))
    current = current[current["metric"] == "eps"].copy()
    if current.empty:
        return current

    current["fiscal_period_end"] = current["end"]
    current["annual_eps"] = current["value"]
    current["annual_eps_growth"] = pd.NA
    current["annual_growth_source"] = pd.NA

    for i, row in current.iterrows():
        prev = _same_filing_prior(row, all_annual_facts, 300, 430)
        source = None
        if prev is not None:
            source = "SAME_FILING_COMPARATIVE"
        else:
            prior = current[
                (current["fiscal_period_end"] >= row["fiscal_period_end"] - pd.Timedelta(days=400))
                & (current["fiscal_period_end"] <= row["fiscal_period_end"] - pd.Timedelta(days=330))
                & (current["accepted_at"] <= row["accepted_at"])
                & (current["unit"] == row["unit"])
            ]
            same_tag = prior[prior["tag"] == row["tag"]]
            if not same_tag.empty:
                prior = same_tag
            if not prior.empty:
                prev = prior.sort_values("accepted_at").iloc[-1]["annual_eps"]
                source = "PRIOR_PIT_OBSERVATION"
        if prev in (None, 0) or prev <= 0:
            continue
        current.at[i, "annual_eps_growth"] = row["annual_eps"] / prev - 1
        current.at[i, "annual_growth_source"] = source

    return current.sort_values("accepted_at")


def _attach_annual_state(q: pd.DataFrame, annual_state: pd.DataFrame) -> pd.DataFrame:
    out = q.copy()
    cols = [
        "annual_eps",
        "annual_eps_growth",
        "annual_eps_accepted_at",
        "annual_eps_filed_at",
        "annual_eps_source_accession",
        "annual_growth_source",
    ]
    for c in cols:
        out[c] = pd.NA

    if annual_state.empty:
        return out

    state = annual_state[[
        "accepted_at", "filed_at", "accession", "annual_eps", "annual_eps_growth", "annual_growth_source",
    ]].copy().sort_values("accepted_at")

    for i, row in out.iterrows():
        available = state[state["accepted_at"] <= row["accepted_at"]]
        if available.empty:
            continue
        latest = available.iloc[-1]
        out.at[i, "annual_eps"] = latest["annual_eps"]
        out.at[i, "annual_eps_growth"] = latest["annual_eps_growth"]
        out.at[i, "annual_eps_accepted_at"] = latest["accepted_at"]
        out.at[i, "annual_eps_filed_at"] = latest["filed_at"]
        out.at[i, "annual_eps_source_accession"] = latest["accession"]
        out.at[i, "annual_growth_source"] = latest["annual_growth_source"]

    return out


def normalize_company(symbol: str, cik: str, companyfacts: dict, filing_rows: list[dict]) -> pd.DataFrame:
    idx = accession_index(filing_rows)
    eps = fact_rows(companyfacts, EPS_TAGS, "eps", idx)
    rev = fact_rows(companyfacts, REVENUE_TAGS, "revenue", idx)
    facts = pd.concat([eps, rev], ignore_index=True)
    if facts.empty:
        return facts
    facts["period_type"] = facts["duration_days"].map(classify_period)

    # Direct discrete quarters can be reported in either 10-Q (Q1-Q3) or 10-K (Q4).
    direct_quarter_facts = facts[
        (facts["period_type"] == "quarterly")
        & facts["form"].isin(["10-Q", "10-Q/A", "10-K", "10-K/A"])
    ].copy()
    direct_quarter_facts = _current_period_only(direct_quarter_facts)
    direct_quarter_facts.loc[
        direct_quarter_facts["form"].isin(["10-K", "10-K/A"]), "period_type"
    ] = "quarterly_direct_q4"
    direct_quarter_facts.loc[
        direct_quarter_facts["form"].isin(["10-K", "10-K/A"]), "fp"
    ] = "Q4"
    q = _dedupe(direct_quarter_facts)

    ytd_q = _build_ytd_quarters(facts, q)
    if not ytd_q.empty:
        q = pd.concat([q, ytd_q], ignore_index=True)
        q = _dedupe(q)

    all_annual_facts = facts[(facts["period_type"] == "annual") & facts["form"].isin(["10-K", "10-K/A"])].copy()
    annual = _dedupe(_current_period_only(all_annual_facts))
    annual_state = _build_annual_eps_state(all_annual_facts)

    q4_rows = []
    for _, fy in annual.iterrows():
        if pd.isna(fy["accepted_at"]):
            continue

        # Never derive Q4 when a direct discrete Q4 exists for the same metric/end.
        direct_q4 = q[
            (q["metric"] == fy["metric"])
            & (q["end"] == fy["end"])
            & (q["period_type"] == "quarterly_direct_q4")
        ]
        if not direct_q4.empty:
            continue

        prior = q[
            (q["metric"] == fy["metric"])
            & (q["tag"] == fy["tag"])
            & (q["unit"] == fy["unit"])
            & (q["accepted_at"] <= fy["accepted_at"])
            & (q["end"] < fy["end"])
            & (q["end"] >= fy["start"])
        ].copy()
        if prior.empty:
            continue
        prior = prior.sort_values("accepted_at").groupby("end", as_index=False).tail(1).sort_values("end")
        if len(prior) != 3:
            continue
        row = fy.copy()
        row["value"] = fy["value"] - prior["value"].sum()
        row["period_type"] = "quarterly_derived_q4"
        row["fp"] = "Q4"
        row["derived_from"] = "FY_MINUS_Q1_Q2_Q3"
        q4_rows.append(row)

    if q4_rows:
        q = pd.concat([q, pd.DataFrame(q4_rows)], ignore_index=True)
    if q.empty:
        return q

    q["symbol"] = symbol
    q["cik"] = cik
    q["fiscal_period_end"] = q["end"]
    q["normalizer_version"] = NORMALIZER_VERSION
    q["yoy"] = pd.NA
    q["yoy_source"] = pd.NA

    for metric in q["metric"].unique():
        m = q[q["metric"] == metric].sort_values(["fiscal_period_end", "accepted_at"])
        for i, row in m.iterrows():
            prev = None
            source = None
            if str(row.get("period_type", "")).startswith("quarterly"):
                prev = _same_filing_prior(row, facts, 60, 120)
                if prev is not None:
                    source = "SAME_FILING_COMPARATIVE"
            if prev is None:
                prior = m[
                    (m["fiscal_period_end"] >= row["fiscal_period_end"] - pd.Timedelta(days=400))
                    & (m["fiscal_period_end"] <= row["fiscal_period_end"] - pd.Timedelta(days=330))
                    & (m["accepted_at"] <= row["accepted_at"])
                    & (m["unit"] == row["unit"])
                ]
                same_tag = prior[prior["tag"] == row["tag"]]
                if not same_tag.empty:
                    prior = same_tag
                if not prior.empty:
                    prev = prior.sort_values("accepted_at").iloc[-1]["value"]
                    source = "PRIOR_PIT_OBSERVATION"
            if prev in (None, 0):
                continue
            if metric == "eps" and prev <= 0:
                continue
            q.at[i, "yoy"] = row["value"] / prev - 1
            q.at[i, "yoy_source"] = source

    return _attach_annual_state(q, annual_state)


def wide_table(long_df: pd.DataFrame) -> pd.DataFrame:
    if long_df.empty:
        return long_df

    state_cols = [
        "annual_eps", "annual_eps_growth", "annual_eps_accepted_at", "annual_eps_filed_at",
        "annual_eps_source_accession", "annual_growth_source",
    ]
    keys = [
        "symbol", "cik", "accepted_at", "filed_at", "accession", "form", "fiscal_period_end", "fp",
        "is_amendment", "normalizer_version",
    ] + state_cols

    base = long_df[keys + ["metric", "value", "yoy"]].copy()
    grouped = base.groupby(keys + ["metric"], dropna=False, as_index=False).agg(
        value=("value", "first"),
        yoy=("yoy", "first"),
    )

    values = grouped.pivot(index=keys, columns="metric", values="value").reset_index()
    yoy = grouped.pivot(index=keys, columns="metric", values="yoy").reset_index().rename(
        columns={"eps": "quarterly_eps_yoy", "revenue": "quarterly_revenue_yoy"}
    )

    out = values.merge(yoy, on=keys, how="outer").rename(
        columns={"eps": "quarterly_eps", "revenue": "quarterly_revenue"}
    )
    out["C_eps_25"] = out.get("quarterly_eps_yoy", pd.Series(index=out.index, dtype="float64")) >= 0.25
    out["C_revenue_25"] = out.get("quarterly_revenue_yoy", pd.Series(index=out.index, dtype="float64")) >= 0.25
    out["A_eps_20"] = out.get("annual_eps_growth", pd.Series(index=out.index, dtype="float64")) >= 0.20
    return out.sort_values(["symbol", "accepted_at", "fiscal_period_end"])
