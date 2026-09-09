import pandas as pd

from ussy_fundamentals.exception_taxonomy import build_taxonomy


def _row(symbol, status, failure=None, forms=None):
    return {
        "symbol": symbol,
        "cik": "0000000001",
        "production_status": status,
        "failure_class": failure,
        "normalized_rows": 1,
        "forms_detected": forms,
        "status": "NORMALIZED" if status.startswith("PASS") or status == "FAIL_PRODUCTION_COVERAGE" else status,
        "run_status": "NORMALIZED" if status.startswith("PASS") or status == "FAIL_PRODUCTION_COVERAGE" else status,
    }


def test_taxonomy_classifies_fpi_and_production_failures():
    readiness = pd.DataFrame([
        _row("OK", "PASS_FULL"),
        _row("FPI", "UNSUPPORTED_FPI", forms="20-F,6-K"),
        _row("CAN", "UNSUPPORTED_FPI", forms="40-F,6-K"),
        _row("AGE", "FAIL_PRODUCTION_COVERAGE", "ANNUAL_LT_3Y"),
        _row(
            "MIX",
            "FAIL_PRODUCTION_COVERAGE",
            "QUARTERLY_EPS_YOY_INSUFFICIENT;ANNUAL_LT_3Y",
        ),
        _row("REV", "FAIL_PRODUCTION_COVERAGE", "QUARTERLY_REVENUE_YOY_INSUFFICIENT"),
        _row("STALE", "FAIL_PRODUCTION_COVERAGE", "QUARTERLY_REVENUE_YOY_STALE"),
        _row("CIK", "CIK_NOT_FOUND"),
    ])

    detail, summary = build_taxonomy(readiness)
    by_symbol = detail.set_index("symbol")

    assert by_symbol.loc["OK", "exception_class"] == "NONE"
    assert by_symbol.loc["FPI", "exception_class"] == "FPI_20F_6K"
    assert by_symbol.loc["CAN", "exception_class"] == "FPI_CANADIAN_40F"
    assert by_symbol.loc["AGE", "exception_class"] == "ANNUAL_LT_3Y_NEEDS_AGE_VERIFICATION"
    assert by_symbol.loc["MIX", "exception_class"] == "SHORT_HISTORY_PLUS_QUARTERLY_GAPS"
    assert by_symbol.loc["REV", "exception_class"] == "REVENUE_YOY_COVERAGE_GAP"
    assert by_symbol.loc["STALE", "exception_class"] == "REVENUE_YOY_STALE"
    assert by_symbol.loc["CIK", "exception_family"] == "IDENTITY_RESOLUTION"

    assert summary["requested_symbols"] == 8
    assert summary["ready_symbols"] == 1
    assert summary["non_ready_symbols"] == 7
    assert summary["fpi_subtype_counts"]["FPI_20F_6K"] == 1
