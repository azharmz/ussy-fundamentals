from ussy_fundamentals.residual_revenue_fixable_probe import classify_comparator_evidence


def test_short_history_is_not_called_comparator_bug():
    assert classify_comparator_evidence(
        recent_periods=2,
        missing_yoy_periods=2,
        raw_same_tag_comparators=0,
        raw_same_unit_comparators=0,
        pit_same_tag_comparators=0,
        pit_same_unit_comparators=0,
    ) == "SHORT_PRIOR_YEAR_HISTORY"


def test_same_tag_comparator_presence_is_actionable():
    assert classify_comparator_evidence(
        recent_periods=8,
        missing_yoy_periods=4,
        raw_same_tag_comparators=2,
        raw_same_unit_comparators=2,
        pit_same_tag_comparators=0,
        pit_same_unit_comparators=0,
    ) == "COMPARATOR_PRESENT_NOT_SELECTED"


def test_cross_tag_comparator_is_separate_from_missing_evidence():
    assert classify_comparator_evidence(
        recent_periods=8,
        missing_yoy_periods=3,
        raw_same_tag_comparators=0,
        raw_same_unit_comparators=2,
        pit_same_tag_comparators=0,
        pit_same_unit_comparators=2,
    ) == "CROSS_TAG_COMPARATOR_PRESENT"


def test_no_comparator_evidence_stays_structural():
    assert classify_comparator_evidence(
        recent_periods=8,
        missing_yoy_periods=5,
        raw_same_tag_comparators=0,
        raw_same_unit_comparators=0,
        pit_same_tag_comparators=0,
        pit_same_unit_comparators=0,
    ) == "COMPARATOR_EVIDENCE_ABSENT"
