from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd

from .annual_fallback import fill_missing_annual_eps
from . import normalize as normalize_mod
from .normalize import normalize_company, wide_table
from .sec_client import SecClient, companyfacts, submissions, ticker_mapping


CIK_HISTORY_FILE = Path("data/cik_history_overrides.csv")


def _load_cik_history(path: Path = CIK_HISTORY_FILE) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["symbol", "cik", "role", "valid_to", "reason"])
    df = pd.read_csv(path, dtype={"symbol": str, "cik": str})
    df["symbol"] = df["symbol"].str.upper().str.strip()
    df["cik"] = df["cik"].str.zfill(10)
    return df


def _symbol_ciks(symbol: str, current_cik: str, history: pd.DataFrame) -> list[str]:
    ciks = [str(current_cik).zfill(10)]
    if not history.empty:
        extra = history.loc[history["symbol"] == symbol, "cik"].dropna().astype(str).tolist()
        ciks.extend(extra)
    return list(dict.fromkeys(ciks))


def _drop_nonadditive_derived_eps(df: pd.DataFrame) -> pd.DataFrame:
    """Remove quarterly EPS values reconstructed by arithmetic subtraction.

    EPS is not additive across periods because weighted-average diluted shares can change.
    Revenue may still be reconstructed from cumulative YTD values, but derived EPS rows
    (Q2/Q3 and Q4) are intentionally excluded until a numerator/share-based method exists.
    """
    if df.empty or "period_type" not in df.columns:
        return df
    period_type = df["period_type"].astype(str)
    bad = (df["metric"] == "eps") & period_type.str.startswith("quarterly_derived")
    return df.loc[~bad].copy()


def _carry_annual_state_across_ciks(df: pd.DataFrame) -> pd.DataFrame:
    """Carry only annual state that was actually public by each row's accepted_at.

    This function enforces a hard PIT invariant across predecessor/successor CIKs:
    annual_eps_accepted_at must never be later than the observation accepted_at.
    Any pre-existing future state is cleared before recomputing the carry-forward.
    """
    if df.empty or "annual_eps" not in df.columns:
        return df

    state_cols = [
        "annual_eps",
        "annual_eps_growth",
        "annual_eps_accepted_at",
        "annual_eps_filed_at",
        "annual_eps_source_accession",
        "annual_growth_source",
    ]

    pieces = []
    for symbol, s in df.groupby("symbol", sort=False):
        s = s.sort_values(["accepted_at", "fiscal_period_end"]).copy()
        row_accepted = pd.to_datetime(s["accepted_at"], errors="coerce", utc=True)
        annual_accepted = pd.to_datetime(s["annual_eps_accepted_at"], errors="coerce", utc=True)

        future = annual_accepted.notna() & row_accepted.notna() & annual_accepted.gt(row_accepted)
        if future.any():
            s.loc[future, state_cols] = pd.NA

        state = s[s["annual_eps"].notna() & s["annual_eps_accepted_at"].notna()][state_cols].copy()
        if state.empty:
            pieces.append(s)
            continue

        state["state_accepted_at"] = pd.to_datetime(state["annual_eps_accepted_at"], errors="coerce", utc=True)
        state = state.dropna(subset=["state_accepted_at"])
        state = (
            state.sort_values("state_accepted_at")
            .groupby("state_accepted_at", as_index=False)
            .last()
        )

        left = s.copy()
        left["_row_accepted_at"] = pd.to_datetime(left["accepted_at"], errors="coerce", utc=True)
        carry = pd.merge_asof(
            left[["_row_accepted_at"]].sort_values("_row_accepted_at"),
            state.sort_values("state_accepted_at"),
            left_on="_row_accepted_at",
            right_on="state_accepted_at",
            direction="backward",
            allow_exact_matches=True,
        ).set_index(left.sort_values("_row_accepted_at").index)

        for c in state_cols:
            left[c] = carry.reindex(left.index)[c]

        final_annual_accepted = pd.to_datetime(left["annual_eps_accepted_at"], errors="coerce", utc=True)
        final_row_accepted = pd.to_datetime(left["accepted_at"], errors="coerce", utc=True)
        bad = final_annual_accepted.notna() & final_row_accepted.notna() & final_annual_accepted.gt(final_row_accepted)
        if bad.any():
            left.loc[bad, state_cols] = pd.NA

        left = left.drop(columns=["_row_accepted_at"], errors="ignore")
        pieces.append(left)

    return pd.concat(pieces, ignore_index=True)


def _add_missing_reason_metadata(wide_df: pd.DataFrame) -> pd.DataFrame:
    """Make intentional vs unexplained NA values self-explaining in the final parquet.

    The vocabulary is intentionally conservative. A missing reason describes why the final
    dataset does not expose a value; it does not fabricate a replacement value.
    """
    out = wide_df.copy()
    fp = out["fp"].astype("string") if "fp" in out.columns else pd.Series(pd.NA, index=out.index, dtype="string")

    def reason_col(value_col: str) -> pd.Series:
        return pd.Series(pd.NA, index=out.index, dtype="string")

    if "quarterly_eps" in out.columns:
        r = reason_col("quarterly_eps")
        missing = out["quarterly_eps"].isna()
        r.loc[missing & fp.eq("Q4")] = "Q4_EXCLUDED_POLICY"
        r.loc[missing & r.isna()] = "NO_DIRECT_QUARTER"
        out["quarterly_eps_missing_reason"] = r

    if "quarterly_revenue" in out.columns:
        r = reason_col("quarterly_revenue")
        missing = out["quarterly_revenue"].isna()
        r.loc[missing] = "NO_DIRECT_QUARTER"
        out["quarterly_revenue_missing_reason"] = r

    if "quarterly_eps_yoy" in out.columns:
        r = reason_col("quarterly_eps_yoy")
        missing = out["quarterly_eps_yoy"].isna()
        if "quarterly_eps_missing_reason" in out.columns:
            inherited = missing & out["quarterly_eps"].isna()
            r.loc[inherited] = out.loc[inherited, "quarterly_eps_missing_reason"]
        r.loc[missing & r.isna()] = "NO_COMPARATIVE_AVAILABLE"
        out["quarterly_eps_yoy_missing_reason"] = r

    if "quarterly_revenue_yoy" in out.columns:
        r = reason_col("quarterly_revenue_yoy")
        missing = out["quarterly_revenue_yoy"].isna()
        if "quarterly_revenue_missing_reason" in out.columns:
            inherited = missing & out["quarterly_revenue"].isna()
            r.loc[inherited] = out.loc[inherited, "quarterly_revenue_missing_reason"]
        r.loc[missing & r.isna()] = "NO_COMPARATIVE_AVAILABLE"
        out["quarterly_revenue_yoy_missing_reason"] = r

    if "annual_eps" in out.columns:
        r = reason_col("annual_eps")
        r.loc[out["annual_eps"].isna()] = "TAG_NOT_FOUND"
        out["annual_eps_missing_reason"] = r

    if "annual_eps_growth" in out.columns:
        r = reason_col("annual_eps_growth")
        missing = out["annual_eps_growth"].isna()
        if "annual_eps_missing_reason" in out.columns:
            inherited = missing & out["annual_eps"].isna()
            r.loc[inherited] = out.loc[inherited, "annual_eps_missing_reason"]
        r.loc[missing & r.isna()] = "NO_COMPARATIVE_AVAILABLE"
        out["annual_eps_growth_missing_reason"] = r

    return out


def run(universe_path: Path, data_dir: Path) -> tuple[Path, Path]:
    raw = data_dir / "raw"
    processed = data_dir / "processed"
    mapping_dir = raw / "mapping"
    submissions_dir = raw / "submissions"
    companyfacts_dir = raw / "companyfacts"
    for p in [mapping_dir, submissions_dir, companyfacts_dir, processed]:
        p.mkdir(parents=True, exist_ok=True)

    for tag in ["IncomeLossFromContinuingOperationsPerDilutedShare"]:
        if tag not in normalize_mod.EPS_TAGS:
            normalize_mod.EPS_TAGS.append(tag)
    for tag in ["OperatingRevenues", "RegulatedAndUnregulatedOperatingRevenue"]:
        if tag not in normalize_mod.REVENUE_TAGS:
            normalize_mod.REVENUE_TAGS.append(tag)

    client = SecClient()
    universe = pd.read_csv(universe_path)
    universe["symbol"] = universe["symbol"].astype(str).str.upper().str.strip()
    universe = universe.merge(ticker_mapping(client, mapping_dir), on="symbol", how="left")
    history = _load_cik_history()

    outputs = []
    rows = list(universe.dropna(subset=["cik"]).itertuples())
    for n, row in enumerate(rows, start=1):
        ciks = _symbol_ciks(row.symbol, row.cik, history)
        for j, cik in enumerate(ciks, start=1):
            suffix = f".{j}" if len(ciks) > 1 else ""
            print(f"[{n}{suffix}] {row.symbol} CIK={cik}")
            filings = submissions(client, cik, submissions_dir)
            facts = companyfacts(client, cik, companyfacts_dir)
            normalized = normalize_company(row.symbol, cik, facts, filings)
            normalized = fill_missing_annual_eps(normalized, facts, filings)
            normalized = _drop_nonadditive_derived_eps(normalized)
            if not normalized.empty:
                outputs.append(normalized)

    if not outputs:
        raise RuntimeError("No fundamentals were normalized")

    long_df = pd.concat(outputs, ignore_index=True)
    dedupe_cols = [
        c for c in ["symbol", "metric", "accepted_at", "fiscal_period_end", "value", "accession"]
        if c in long_df.columns
    ]
    long_df = long_df.drop_duplicates(subset=dedupe_cols, keep="last")
    long_df = _carry_annual_state_across_ciks(long_df)

    wide_df = wide_table(long_df)

    for col, flag_col in [
        ("quarterly_eps_yoy", "quarterly_eps_yoy_extreme"),
        ("quarterly_revenue_yoy", "quarterly_revenue_yoy_extreme"),
        ("annual_eps_growth", "annual_eps_growth_extreme"),
    ]:
        values = pd.to_numeric(wide_df[col], errors="coerce")
        wide_df[col] = values
        wide_df[flag_col] = values.abs().ge(3.0).fillna(False)

    if "quarterly_eps_yoy" in wide_df.columns:
        wide_df["quarterly_eps_growth_sign_flip"] = False
        wide_df["quarterly_eps_growth_unstable_base"] = False

    wide_df = _add_missing_reason_metadata(wide_df)

    long_path = processed / "fundamentals_point_in_time_long.parquet"
    wide_path = processed / "fundamentals_point_in_time.parquet"
    long_df.to_parquet(long_path, index=False)
    wide_df.to_parquet(wide_path, index=False)
    return long_path, wide_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", default="data/universe.sample.csv")
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()
    long_path, wide_path = run(Path(args.universe), Path(args.data_dir))
    print(f"Wrote {long_path}")
    print(f"Wrote {wide_path}")


if __name__ == "__main__":
    main()
