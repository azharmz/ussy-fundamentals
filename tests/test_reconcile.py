from pathlib import Path

import pandas as pd

from ussy_fundamentals.reconcile import build_run_manifest, classify_dropped_symbol


def test_classify_fpi_separately_from_generic_missing():
    filings = [
        {"form": "20-F"},
        {"form": "6-K"},
    ]
    status, forms = classify_dropped_symbol(filings, {"facts": {"ifrs-full": {}}})

    assert status == "UNSUPPORTED_FPI"
    assert forms == "20-F,6-K"


def test_classify_supported_domestic_with_facts_as_normalization_empty():
    filings = [{"form": "10-K"}, {"form": "10-Q"}]
    status, forms = classify_dropped_symbol(filings, {"facts": {"us-gaap": {"Revenues": {}}}})

    assert status == "NORMALIZATION_EMPTY"
    assert forms == "10-K,10-Q"


def test_classify_supported_domestic_without_facts():
    status, _ = classify_dropped_symbol([{"form": "10-K"}], {"facts": {}})
    assert status == "NO_SEC_FACTS"


def test_build_manifest_accounts_for_requested_symbols(tmp_path: Path):
    data_dir = tmp_path / "data"
    (data_dir / "raw" / "mapping").mkdir(parents=True)
    (data_dir / "raw" / "submissions").mkdir(parents=True)
    (data_dir / "raw" / "companyfacts").mkdir(parents=True)

    universe = tmp_path / "universe.csv"
    pd.DataFrame({"symbol": ["OK", "FPI", "MISSING"]}).to_csv(universe, index=False)

    wide = tmp_path / "wide.parquet"
    pd.DataFrame(
        {
            "symbol": ["OK", "OK"],
            "fiscal_period_end": pd.to_datetime(["2025-03-31", "2025-06-30"]),
        }
    ).to_parquet(wide, index=False)

    (data_dir / "raw" / "mapping" / "company_tickers.json").write_text(
        '{"0":{"ticker":"OK","cik_str":1,"title":"OK"},'
        '"1":{"ticker":"FPI","cik_str":2,"title":"FPI"}}',
        encoding="utf-8",
    )
    (data_dir / "raw" / "submissions" / "CIK0000000002.json").write_text(
        '{"filings":{"recent":{"form":["20-F","6-K"]}}}',
        encoding="utf-8",
    )

    manifest = build_run_manifest(universe, wide, data_dir).set_index("symbol")

    assert len(manifest) == 3
    assert manifest.loc["OK", "status"] == "NORMALIZED"
    assert manifest.loc["OK", "normalized_rows"] == 2
    assert manifest.loc["FPI", "status"] == "UNSUPPORTED_FPI"
    assert manifest.loc["MISSING", "status"] == "CIK_NOT_FOUND"
