from ussy_fundamentals.quarterly_eps_stale_diagnose import classify_stale


def test_stale_eps_with_fallback_inputs_continuing():
    assert classify_stale(
        recent_10q_after_standard=4,
        fallback_pairs_after_standard=3,
        nonstandard_direct_after_standard=0,
    ) == "STANDARD_EPS_STOPPED_FALLBACK_INPUTS_CONTINUE"


def test_stale_eps_with_nonstandard_replacement_candidate():
    assert classify_stale(
        recent_10q_after_standard=4,
        fallback_pairs_after_standard=0,
        nonstandard_direct_after_standard=2,
    ) == "TAG_TRANSITION_CANDIDATE"


def test_stale_eps_without_replacement_evidence():
    assert classify_stale(
        recent_10q_after_standard=4,
        fallback_pairs_after_standard=0,
        nonstandard_direct_after_standard=0,
    ) == "STANDARD_EPS_STOPPED_NO_USABLE_REPLACEMENT"


def test_stale_eps_without_later_quarterly_filings():
    assert classify_stale(
        recent_10q_after_standard=0,
        fallback_pairs_after_standard=0,
        nonstandard_direct_after_standard=0,
    ) == "NO_10Q_AFTER_STANDARD_EPS"
