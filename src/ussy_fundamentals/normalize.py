from __future__ import annotations

import pandas as pd

FORMS = {"10-Q", "10-Q/A", "10-K", "10-K/A"}
EPS_TAGS = ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic"]
REVENUE_TAGS = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]
NORMALIZER_VERSION = "sec-ca-v0.3.0"


def _dt(x):
    return pd.to_datetime(x, errors="coerce") if x else pd.NaT


def _duration_days(start, end):
    a, b = _dt(start), _dt(end)
    return None if pd.isna(a) or pd.isna(b) else (b - a).days


def accession_index(filings: list[dict]) -> dict[str, dict]:
    return {x.get("accessionNumber"): x for x in filings if x.get("accessionNumber")}


def fact_rows(companyfacts: dict, tags: list[str], metric: str, filings: dict[str, dict]) -> pd.DataFrame:
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    rows = []
    for priority, tag in enumerate(tags):
        concept = gaap.get(tag)
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
                    "tag_priority": priority,
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
    """Keep current reported-period observations; comparative facts remain available for PIT YoY."""
    if df.empty:
        return df
    report_date = pd.to_datetime(df["report_date"], errors="coerce")
    end = pd.to_datetime(df["end"], errors="coerce")
    return df[report_date.notna() & end.eq(report_date)].copy()


def _same_filing_prior(row: pd.Series, all_period_facts: pd.DataFrame):
    """Find prior-year comparable value as presented in the SAME filing.

    This matters especially for per-share metrics after stock splits: a later filing may
    retrospectively restate the comparative EPS, while the old filing retains its
    pre-split value. Using the same accession preserves what the issuer presented at the
    current filing's availability timestamp without look-ahead.
    """
    target_lo = row["fiscal_period_end"] - pd.Timedelta(days=400)
    target_hi = row["fiscal_period_end"] - pd.Timedelta(days=330)
    c = all_period_facts[
        (all_period_facts["metric"] == row["metric"])
        & (all_period_facts["accession"] == row["accession"])
        & (all_period_facts["end"] >= target_lo)
        & (all_period_facts["end"] <= target_hi)
        & (all_period_facts["duration_days"].between(60, 120, inclusive="both"))
    ].copy()
    if c.empty:
        return None
    c = c.sort_values(["tag_priority", "is_amendment"], ascending=[True, True])
    return c.iloc[0]["value"]


def normalize_company(symbol: str, cik: str, companyfacts: dict, filing_rows: list[dict]) -> pd.DataFrame:
    idx = accession_index(filing_rows)
    eps = fact_rows(companyfacts, EPS_TAGS, "eps", idx)
    rev = fact_rows(companyfacts, REVENUE_TAGS, "revenue", idx)
    facts = pd.concat([eps, rev], ignore_index=True)
    if facts.empty:
        return facts
    facts["period_type"] = facts["duration_days"].map(classify_period)

    all_quarter_facts = facts[(facts["period_type"] == "quarterly") & facts["form"].isin(["10-Q", "10-Q/A"])].copy()
    q = _dedupe(_current_period_only(all_quarter_facts))

    annual = facts[(facts["period_type"] == "annual") & facts["form"].isin(["10-K", "10-K/A"])].copy()
    annual = _dedupe(_current_period_only(annual))

    q4_rows = []
    for _, fy in annual.iterrows():
        if pd.isna(fy["accepted_at"]):
            continue
        prior = q[(q["metric"] == fy["metric"]) & (q["accepted_at"] <= fy["accepted_at"]) & (q["end"] < fy["end"]) & (q["end"] >= fy["start"])].copy()
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

            # Preferred: comparative quarter as restated/presented in the same filing.
            if row.get("period_type") == "quarterly":
                prev = _same_filing_prior(row, all_quarter_facts)
                if prev is not None:
                    source = "SAME_FILING_COMPARATIVE"

            # Fallback for derived Q4 or filings lacking a comparable fact.
            if prev is None:
                prior = m[(m["fiscal_period_end"] >= row["fiscal_period_end"] - pd.Timedelta(days=400)) & (m["fiscal_period_end"] <= row["fiscal_period_end"] - pd.Timedelta(days=330)) & (m["accepted_at"] <= row["accepted_at"])]
                if not prior.empty:
                    prev = prior.sort_values("accepted_at").iloc[-1]["value"]
                    source = "PRIOR_PIT_OBSERVATION"

            if prev in (None, 0):
                continue
            if metric == "eps" and prev <= 0:
                continue
            q.at[i, "yoy"] = row["value"] / prev - 1
            q.at[i, "yoy_source"] = source
    return q


def wide_table(long_df: pd.DataFrame) -> pd.DataFrame:
    if long_df.empty:
        return long_df
    keys = ["symbol", "cik", "accepted_at", "filed_at", "accession", "form", "fiscal_period_end", "fp", "is_amendment", "normalizer_version"]
    values = long_df.pivot_table(index=keys, columns="metric", values="value", aggfunc="first").reset_index()
    yoy = long_df.pivot_table(index=keys, columns="metric", values="yoy", aggfunc="first").reset_index().rename(columns={"eps": "quarterly_eps_yoy", "revenue": "quarterly_revenue_yoy"})
    out = values.merge(yoy, on=keys, how="outer").rename(columns={"eps": "quarterly_eps", "revenue": "quarterly_revenue"})
    out["C_eps_25"] = out.get("quarterly_eps_yoy", pd.Series(index=out.index, dtype="float64")) >= 0.25
    out["C_revenue_25"] = out.get("quarterly_revenue_yoy", pd.Series(index=out.index, dtype="float64")) >= 0.25
    return out.sort_values(["symbol", "accepted_at", "fiscal_period_end"])
