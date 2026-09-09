import pandas as pd

from ussy_fundamentals.final_production_report import build_final_report


def test_final_production_buckets_and_funnel():
    readiness = pd.DataFrame([
        {"symbol": "A", "production_status": "PASS_FULL", "failure_class": ""},
        {"symbol": "B", "production_status": "PASS_3Y_FALLBACK", "failure_class": ""},
        {"symbol": "C", "production_status": "CIK_NOT_FOUND", "failure_class": "CIK_NOT_FOUND"},
        {"symbol": "D", "production_status": "UNSUPPORTED_FPI", "failure_class": "UNSUPPORTED_FPI"},
        {"symbol": "E", "production_status": "FAIL_PRODUCTION_COVERAGE", "failure_class": "ANNUAL_LT_3Y"},
        {"symbol": "F", "production_status": "FAIL_PRODUCTION_COVERAGE", "failure_class": "QUARTERLY_REVENUE_YOY_INSUFFICIENT"},
        {"symbol": "G", "production_status": "NO_SEC_FACTS", "failure_class": "NO_SEC_FACTS"},
    ])
    taxonomy = pd.DataFrame([
        {"symbol": "E", "exception_family": "PRODUCTION_COVERAGE", "exception_class": "ANNUAL_LT_3Y_NEEDS_AGE_VERIFICATION", "needs_diagnosis": True},
        {"symbol": "F", "exception_family": "PRODUCTION_COVERAGE", "exception_class": "REVENUE_YOY_COVERAGE_GAP", "needs_diagnosis": True},
    ])

    detail, summary = build_final_report(readiness, taxonomy)

    buckets = dict(zip(detail["symbol"], detail["final_bucket"]))
    assert buckets["A"] == "READY"
    assert buckets["B"] == "READY"
    assert buckets["C"] == "UNRESOLVED_CIK"
    assert buckets["D"] == "UNSUPPORTED_FPI"
    assert buckets["E"] == "INSUFFICIENT_HISTORY"
    assert buckets["F"] == "RESIDUAL_PRODUCTION_COVERAGE"
    assert buckets["G"] == "STRUCTURALLY_UNAVAILABLE_OR_UNSUPPORTED"

    assert summary["requested_symbols"] == 7
    assert summary["ready_symbols"] == 2
    assert summary["funnel"]["requested_universe"] == 7
    assert summary["funnel"]["cik_resolved_or_not_required"] == 6
    assert summary["funnel"]["domestic_sec_supported"] == 5
    assert summary["funnel"]["normalized_with_sec_facts"] == 4
    assert summary["funnel"]["production_ready"] == 2
