from ussy_fundamentals.quarterly_revenue_gap_diagnose import classify_revenue_gap


def test_revenue_present_but_yoy_missing_is_comparator_gap():
    cls = classify_revenue_gap(
        recent_revenue_periods=6,
        recent_yoy_usable=1,
        stale_days=20,
        direct_periods=6,
        ytd_periods=4,
        annual_periods=5,
        nonconfigured_tags=0,
        history_span_days=900,
    )
    assert cls == "REVENUE_PRESENT_YOY_COMPARATOR_GAP"


def test_stale_revenue_with_nonstandard_candidate_is_tag_transition():
    cls = classify_revenue_gap(
        recent_revenue_periods=4,
        recent_yoy_usable=3,
        stale_days=500,
        direct_periods=4,
        ytd_periods=2,
        annual_periods=5,
        nonconfigured_tags=2,
        history_span_days=1500,
    )
    assert cls == "STANDARD_REVENUE_EVIDENCE_STALE_TAG_TRANSITION_CANDIDATE"


def test_ytd_inputs_are_separate_from_no_evidence():
    cls = classify_revenue_gap(
        recent_revenue_periods=0,
        recent_yoy_usable=0,
        stale_days=None,
        direct_periods=0,
        ytd_periods=4,
        annual_periods=0,
        nonconfigured_tags=0,
        history_span_days=None,
    )
    assert cls == "YTD_RECONSTRUCTION_INPUTS_PRESENT"
