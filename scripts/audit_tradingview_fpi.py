from __future__ import annotations

import argparse
import json
import math
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

SCANNER_URL = "https://scanner.tradingview.com/america/scan"
DEFAULT_INPUT = Path("data/processed/unsupported_fpi.csv")
DEFAULT_OUTPUT = Path("data/processed/tradingview_fpi_coverage.csv")
DEFAULT_SUMMARY = Path("data/processed/tradingview_fpi_coverage_summary.json")

COLUMNS = [
    "name",
    "exchange",
    "last_report_frequency",
    "fiscal_period_end_fq",
    "fiscal_period_end_fh",
    "fiscal_period_fy",
    "fiscal_period_end_fy",
    "earnings_release_date",
    "earnings_release_trading_date_fq",
    "earnings_release_trading_date_fy",
    "earnings_per_share_fq",
    "revenue_fq",
    "earnings_per_share_diluted_fq_h",
    "total_revenue_fq_h",
    "earnings_per_share_diluted_fh_h",
    "total_revenue_fh_h",
    "earnings_per_share_diluted_fy_h",
    "total_revenue_fy_h",
]


def _values(v):
    return v if isinstance(v, list) else []


def _stats(values):
    xs = _values(values)
    nonnull = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    nonzero = [x for x in nonnull if x != 0]
    return len(xs), len(nonnull), len(nonzero)


def _paired(a, b):
    aa, bb = _values(a), _values(b)
    return sum(
        1
        for x, y in zip(aa, bb)
        if x is not None and y is not None
        and not (isinstance(x, float) and math.isnan(x))
        and not (isinstance(y, float) and math.isnan(y))
    )


def scan(tickers: list[str], retries: int = 3):
    payload = json.dumps({
        "symbols": {"tickers": tickers, "query": {"types": []}},
        "columns": COLUMNS,
    }).encode()
    req = urllib.request.Request(
        SCANNER_URL,
        data=payload,
        method="POST",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except (urllib.error.URLError, TimeoutError):
            if attempt + 1 == retries:
                raise
            time.sleep(2 ** attempt)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    p.add_argument("--batch-size", type=int, default=75)
    args = p.parse_args()

    src = pd.read_csv(args.input)
    symbols = src["symbol"].astype(str).str.upper().str.strip().drop_duplicates().tolist()

    # TradingView accepts exchange-qualified symbols. Query all US venues for each
    # ticker, then resolve returned rows by the scanner's own symbol name.
    venues = ["NASDAQ", "NYSE", "AMEX"]
    requested = [f"{venue}:{symbol}" for symbol in symbols for venue in venues]

    found = {}
    for i in range(0, len(requested), args.batch_size):
        batch = requested[i:i + args.batch_size]
        result = scan(batch)
        for row in result.get("data", []):
            d = dict(zip(COLUMNS, row.get("d", [])))
            symbol = str(d.get("name") or "").upper().strip()
            if symbol in symbols and symbol not in found:
                found[symbol] = d
        time.sleep(0.25)

    rows = []
    for symbol in symbols:
        d = found.get(symbol, {})
        out = {
            "symbol": symbol,
            "tv_found": bool(d),
            "exchange": d.get("exchange"),
            "last_report_frequency": d.get("last_report_frequency"),
            "fiscal_period_end_fq": d.get("fiscal_period_end_fq"),
            "fiscal_period_end_fh": d.get("fiscal_period_end_fh"),
            "fiscal_period_fy": d.get("fiscal_period_fy"),
            "fiscal_period_end_fy": d.get("fiscal_period_end_fy"),
            "earnings_release_date": d.get("earnings_release_date"),
            "earnings_release_trading_date_fq": d.get("earnings_release_trading_date_fq"),
            "earnings_release_trading_date_fy": d.get("earnings_release_trading_date_fy"),
            "earnings_per_share_fq": d.get("earnings_per_share_fq"),
            "revenue_fq": d.get("revenue_fq"),
        }
        for period in ("fq", "fh", "fy"):
            eps = d.get(f"earnings_per_share_diluted_{period}_h")
            rev = d.get(f"total_revenue_{period}_h")
            es, en, ez = _stats(eps)
            rs, rn, rz = _stats(rev)
            out.update({
                f"{period}_eps_slots": es,
                f"{period}_eps_nonnull": en,
                f"{period}_rev_slots": rs,
                f"{period}_rev_nonnull": rn,
                f"{period}_rev_nonzero": rz,
                f"{period}_paired_usable": _paired(eps, rev),
                f"{period}_suspicious_zero_revenue_series": bool(rn and rz == 0),
            })
        # CAN SLIM-oriented coverage gates only. These do NOT establish PIT safety.
        # C: need enough quarterly history to form at least two YoY comparisons
        # (current vs year-ago), conservatively requiring >= 8 paired quarters.
        # A: production policy uses 5 FY target with a 3 FY fallback.
        out["c_quarterly_8q_coverage"] = out["fq_paired_usable"] >= 8
        out["a_annual_5y_coverage"] = out["fy_paired_usable"] >= 5
        out["a_annual_3y_fallback_coverage"] = out["fy_paired_usable"] >= 3
        out["ca_full_coverage_candidate"] = (
            out["c_quarterly_8q_coverage"] and out["a_annual_5y_coverage"]
        )
        out["ca_3y_fallback_candidate"] = (
            out["c_quarterly_8q_coverage"]
            and not out["a_annual_5y_coverage"]
            and out["a_annual_3y_fallback_coverage"]
        )
        out["usable_any_period"] = any(
            out[f"{period}_paired_usable"] >= 2 for period in ("fq", "fh", "fy")
        )
        rows.append(out)

    df = pd.DataFrame(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False)

    summary = {
        "schema_version": 1,
        "source": str(args.input),
        "symbols": len(symbols),
        "tv_found": int(df["tv_found"].sum()),
        "tv_not_found": int((~df["tv_found"]).sum()),
        "fq_paired_ge2": int((df["fq_paired_usable"] >= 2).sum()),
        "fh_paired_ge2": int((df["fh_paired_usable"] >= 2).sum()),
        "fy_paired_ge2": int((df["fy_paired_usable"] >= 2).sum()),
        "usable_any_period": int(df["usable_any_period"].sum()),
        "c_quarterly_8q_coverage": int(df["c_quarterly_8q_coverage"].sum()),
        "a_annual_5y_coverage": int(df["a_annual_5y_coverage"].sum()),
        "a_annual_3y_fallback_coverage": int(df["a_annual_3y_fallback_coverage"].sum()),
        "ca_full_coverage_candidate": int(df["ca_full_coverage_candidate"].sum()),
        "ca_3y_fallback_candidate": int(df["ca_3y_fallback_candidate"].sum()),
        "suspicious_zero_revenue_any": int(
            (
                df["fq_suspicious_zero_revenue_series"]
                | df["fh_suspicious_zero_revenue_series"]
                | df["fy_suspicious_zero_revenue_series"]
            ).sum()
        ),
        "note": "Coverage audit only. TradingView historical arrays are current-revised and are not treated as PIT-safe.",
    }
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
