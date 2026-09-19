from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


DEFAULT_WIDE = Path("data/processed/fundamentals_point_in_time.parquet")
DEFAULT_REPORT = Path("data/processed/fundamentals_final_production_report.csv")
DEFAULT_UNIVERSE = Path("data/processed/current_universe.csv")
DEFAULT_OUTPUT = Path("data/processed/fundamentals_serving_current.json")


def _iso(value) -> str | None:
    if pd.isna(value):
        return None
    ts = pd.Timestamp(value)
    return ts.isoformat()


def _number(value) -> float | None:
    if pd.isna(value):
        return None
    return float(value)


def _latest_two(rows: pd.DataFrame, column: str) -> tuple[float | None, float | None]:
    if column not in rows.columns:
        return None, None
    usable = rows.loc[rows[column].notna()].sort_values(["accepted_at", "fiscal_period_end"])
    if usable.empty:
        return None, None
    values = usable[column].tolist()
    latest = _number(values[-1])
    prior = _number(values[-2]) if len(values) > 1 else None
    return latest, prior


def build_serving_projection(
    wide: pd.DataFrame,
    report: pd.DataFrame,
    universe: pd.DataFrame,
    *,
    source_run_id: str | None = None,
    source_commit: str | None = None,
    universe_snapshot_date: str | None = None,
) -> dict:
    required = {"symbol", "accepted_at", "fiscal_period_end"}
    missing = required - set(wide.columns)
    if missing:
        raise ValueError(f"PIT dataset missing required columns: {sorted(missing)}")
    if "symbol" not in report.columns or "production_status" not in report.columns:
        raise ValueError("Final report must contain symbol and production_status")
    if "symbol" not in universe.columns or "security_id" not in universe.columns:
        raise ValueError("Universe must contain symbol and security_id")

    x = wide.copy()
    x["symbol"] = x["symbol"].astype(str).str.upper().str.strip()
    x["accepted_at"] = pd.to_datetime(x["accepted_at"], errors="coerce", utc=True)
    x["fiscal_period_end"] = pd.to_datetime(x["fiscal_period_end"], errors="coerce")
    x = x.dropna(subset=["symbol", "accepted_at", "fiscal_period_end"])

    statuses = (
        report.assign(symbol=report["symbol"].astype(str).str.upper().str.strip())
        .drop_duplicates("symbol", keep="last")
        .set_index("symbol")["production_status"]
        .to_dict()
    )
    security_ids = (
        universe.assign(symbol=universe["symbol"].astype(str).str.upper().str.strip())
        .drop_duplicates("symbol", keep="last")
        .set_index("symbol")["security_id"]
        .to_dict()
    )

    records = []
    for symbol, rows in x.groupby("symbol", sort=True):
        security_id = security_ids.get(symbol)
        if pd.isna(security_id) or security_id is None:
            continue
        rows = rows.sort_values(["accepted_at", "fiscal_period_end"])
        latest_row = rows.iloc[-1]
        eps_latest, eps_prior = _latest_two(rows, "quarterly_eps_yoy")
        rev_latest, rev_prior = _latest_two(rows, "quarterly_revenue_yoy")

        annual = rows.loc[rows.get("annual_eps_growth", pd.Series(index=rows.index, dtype=float)).notna()]
        annual_growth = _number(annual.iloc[-1]["annual_eps_growth"]) if not annual.empty else None

        records.append({
            "security_id": str(security_id),
            "ticker": symbol,
            "status": None if pd.isna(statuses.get(symbol)) else str(statuses.get(symbol)),
            "as_of_date": _iso(latest_row["accepted_at"]),
            "accepted_at": _iso(latest_row["accepted_at"]),
            "fiscal_period_end": _iso(latest_row["fiscal_period_end"]),
            "eps_yoy_latest": eps_latest,
            "eps_yoy_prior": eps_prior,
            "revenue_yoy_latest": rev_latest,
            "revenue_yoy_prior": rev_prior,
            "annual_eps_growth": annual_growth,
        })

    return {
        "schema_version": 1,
        "type": "fundamentals_serving_current",
        "source_run_id": source_run_id,
        "source_commit": source_commit,
        "universe_snapshot_date": universe_snapshot_date,
        "row_count": len(records),
        "records": records,
    }


def write_serving_projection(
    *,
    wide_path: Path = DEFAULT_WIDE,
    report_path: Path = DEFAULT_REPORT,
    universe_path: Path = DEFAULT_UNIVERSE,
    output_path: Path = DEFAULT_OUTPUT,
    source_run_id: str | None = None,
    source_commit: str | None = None,
    universe_snapshot_date: str | None = None,
) -> dict:
    payload = build_serving_projection(
        pd.read_parquet(wide_path),
        pd.read_csv(report_path),
        pd.read_csv(universe_path, dtype={"security_id": str}),
        source_run_id=source_run_id,
        source_commit=source_commit,
        universe_snapshot_date=universe_snapshot_date,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload
