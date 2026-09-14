from ussy_fundamental.sec_13f_identity_mapping import (
    VERSION,
    IdentifierCandidate,
    MappingState,
    ProjectSecurity,
    map_cusip_to_project_security,
)


def candidate(**kwargs):
    base = dict(
        source_cusip="037833100",
        figi="BBG000B9XRY4",
        ticker="AAPL",
        market_sector="Equity",
        security_type="Common Stock",
        exchange_code="US",
        provider="OPENFIGI",
        provider_observed_at="2026-09-15T00:00:00Z",
    )
    base.update(kwargs)
    return IdentifierCandidate(**base)


def universe(*rows):
    return [ProjectSecurity(security_id=sid, ticker=ticker) for sid, ticker in rows]


def test_version_is_frozen():
    assert VERSION == "sec-13f-identity-mapping-v1"


def test_exact_unique_mapping_passes():
    r = map_cusip_to_project_security(
        cusip="037833100",
        candidates=[candidate()],
        project_securities=universe(("sec-aapl", "AAPL")),
    )
    assert r.state == MappingState.MAPPED
    assert r.security_id == "sec-aapl"
    assert r.ticker == "AAPL"
    assert r.figi == "BBG000B9XRY4"


def test_missing_cusip_is_not_evaluable():
    r = map_cusip_to_project_security(
        cusip=None,
        candidates=[candidate()],
        project_securities=universe(("sec-aapl", "AAPL")),
    )
    assert r.state == MappingState.NOT_EVALUABLE
    assert r.reason == "CUSIP_MISSING"


def test_non_exact_cusip_is_not_used():
    r = map_cusip_to_project_security(
        cusip="999999999",
        candidates=[candidate()],
        project_securities=universe(("sec-aapl", "AAPL")),
    )
    assert r.state == MappingState.NOT_EVALUABLE
    assert r.reason == "NO_EXACT_IDENTIFIER_RESULT"


def test_non_equity_result_is_not_evaluable():
    r = map_cusip_to_project_security(
        cusip="037833100",
        candidates=[candidate(market_sector="Corporate")],
        project_securities=universe(("sec-aapl", "AAPL")),
    )
    assert r.state == MappingState.NOT_EVALUABLE
    assert r.reason == "NO_ELIGIBLE_EQUITY_IDENTIFIER_RESULT"


def test_missing_figi_is_not_evaluable():
    r = map_cusip_to_project_security(
        cusip="037833100",
        candidates=[candidate(figi=None)],
        project_securities=universe(("sec-aapl", "AAPL")),
    )
    assert r.state == MappingState.NOT_EVALUABLE


def test_multiple_eligible_results_are_not_silently_resolved():
    r = map_cusip_to_project_security(
        cusip="037833100",
        candidates=[candidate(), candidate(figi="BBG000B9XRY5")],
        project_securities=universe(("sec-aapl", "AAPL")),
    )
    assert r.state == MappingState.NOT_EVALUABLE
    assert r.reason == "AMBIGUOUS_IDENTIFIER_RESULT"


def test_ticker_outside_project_universe_is_not_evaluable():
    r = map_cusip_to_project_security(
        cusip="037833100",
        candidates=[candidate()],
        project_securities=universe(("sec-msft", "MSFT")),
    )
    assert r.state == MappingState.NOT_EVALUABLE
    assert r.reason == "MAPPED_TICKER_OUTSIDE_PROJECT_UNIVERSE"


def test_duplicate_project_ticker_is_not_evaluable():
    r = map_cusip_to_project_security(
        cusip="037833100",
        candidates=[candidate()],
        project_securities=universe(("a", "AAPL"), ("b", "AAPL")),
    )
    assert r.state == MappingState.NOT_EVALUABLE
    assert r.reason == "PROJECT_TICKER_IDENTITY_AMBIGUOUS"
