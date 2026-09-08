from __future__ import annotations

import pandas as pd

from .normalize import accession_index, fact_rows

NET_INCOME_TAGS = [
    "NetIncomeLossAvailableToCommonStockholdersBasic",
    "NetIncomeLoss",
    "ProfitLoss",
]

DILUTED_SHARES_TAGS = [
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
    "WeightedAverageNumberOfSharesOutstandingBasic",
]


def _annual_candidates(companyfacts: dict, filing_rows: list[dict], tags: list[str], metric: str) -> pd.DataFrame:
    idx = accession_index(filing_rows)
    df = fact_rows(companyfacts, tags, metric, idx)
    if df.empty:
        return df
    df = df[
        df["form"].isin(["10-K", "10-K/A"])
        & df["duration_days"].between(300, 430, inclusive="both")
    ].copy()
    return df


def _pick(df: pd.DataFrame):
    if df.empty:
        return None
    return df.sort_values(["tag_priority", "is_amendment"], ascending=[True, True]).iloc[0]


def _derived_state(companyfacts: dict, filing_rows: list[dict]) -> pd.DataFrame:
    ni = _annual_candidates(companyfacts, filing_rows, NET_INCOME_TAGS, "net_income")
    sh = _annual_candidates(companyfacts, filing_rows, DILUTED_SHARES_TAGS, "diluted_shares")
    if ni.empty or sh.empty:
        return pd.DataFrame()

    rows = []
    accessions = sorted(set(ni["accession"].dropna()) & set(sh["accession"].dropna()))
    for accn in accessions:
        n = ni[ni["accession"] == accn].copy()
        s = sh[sh["accession"] == accn].copy()
        if n.empty or s.empty:
            continue

        report_date = n["report_date"].dropna()
        if report_date.empty:
            report_date = s["report_date"].dropna()
        if report_date.empty:
            continue
        report_date = pd.to_datetime(report_date.iloc[0])

        cur_n = _pick(n[n["end"] == report_date])
        cur_s = _pick(s[s["end"] == report_date])
        if cur_n is None or cur_s is None or not cur_s["value"]:
            continue

        current_eps = cur_n["value"] / cur_s["value"]
        target_lo = report_date - pd.Timedelta(days=400)
        target_hi = report_date - pd.Timedelta(days=330)

        prev_n = _pick(n[(n["end"] >= target_lo) & (n["end"] <= target_hi)])
        prev_s = _pick(s[(s["end"] >= target_lo) & (s["end"] <= target_hi)])

        growth = pd.NA
        if prev_n is not None and prev_s is not None and prev_s["value"]:
            prior_eps = prev_n["value"] / prev_s["value"]
            if prior_eps > 0:
                growth = current_eps / prior_eps - 1

        rows.append({
            "accepted_at": cur_n["accepted_at"],
            "filed_at": cur_n["filed_at"],
            "accession": accn,
            "annual_eps": current_eps,
            "annual_eps_growth": growth,
            "annual_growth_source": "DERIVED_NET_INCOME_OVER_SHARES",
        })

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("accepted_at")


def fill_missing_annual_eps(normalized: pd.DataFrame, companyfacts: dict, filing_rows: list[dict]) -> pd.DataFrame:
    """Fill only missing annual EPS state using transparent SEC-derived fallback.

    Direct standardized EPS remains preferred. This fallback is used only when the SEC
    companyfacts standardized EPS concepts are unavailable for an issuer (for example,
    filings that present EPS through issuer-specific extensions).
    """
    if normalized.empty:
        return normalized

    state = _derived_state(companyfacts, filing_rows)
    if state.empty:
        return normalized

    out = normalized.copy()
    for i, row in out.iterrows():
        if pd.notna(row.get("annual_eps")):
            continue
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
