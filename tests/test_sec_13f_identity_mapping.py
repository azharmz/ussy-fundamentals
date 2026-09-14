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
        composite_figi="BBG000B9XRY4",
        share_class_figi="BBG001S5N8V8",
    )
    base.update(kwargs)
    return IdentifierCandidate(**base)


def universe(*rows):
    return [ProjectSecurity(security_id=sid, ticker=ticker) for sid, ticker in rows]


def test_version_is_frozen():
    assert VERSION == "sec-13f-identity-mapping-v1.1"


def test_exact_unique_mapping_passes():
    r = map_cusip_to_project_security(
        cusip="037833100", candidates=[candidate()], project_securities=universe(("sec-aapl", "AAPL"))
    )
    assert r.state == MappingState.MAPPED
    assert r.security_id == "sec-aapl"


def test_multiple_venue_rows_converging_on_same_security_pass():
    rows = [
        candidate(figi="VENUE1", exchange_code="UA"),
        candidate(figi="VENUE2", exchange_code="UP"),
        candidate(figi="VENUE3", exchange_code="VG"),
    ]
    r = map_cusip_to_project_security(
        cusip="037833100", candidates=rows, project_securities=universe(("sec-aapl", "AAPL"))
    )
    assert r.state == MappingState.MAPPED
    assert r.share_class_figi == "BBG001S5N8V8"
    assert r.composite_figi == "BBG000B9XRY4"


def test_nvda_style_venue_rows_converge():
    rows = [
        candidate(source_cusip="67066G104", figi="BBG000BBJTN3", ticker="NVDA", exchange_code="UB", composite_figi="BBG000BBJQV0", share_class_figi="BBG001S5TZJ6"),
        candidate(source_cusip="67066G104", figi="BBG000BBJSH2", ticker="NVDA", exchange_code="UP", composite_figi="BBG000BBJQV0", share_class_figi="BBG001S5TZJ6"),
        candidate(source_cusip="67066G104", figi="BBG00X1L86Z9", ticker="NVDA", exchange_code="VG", composite_figi="BBG000BBJQV0", share_class_figi="BBG001S5TZJ6"),
    ]
    r = map_cusip_to_project_security(
        cusip="67066G104", candidates=rows, project_securities=universe(("sec-nvda", "NVDA"))
    )
    assert r.state == MappingState.MAPPED
    assert r.security_id == "sec-nvda"
    assert r.share_class_figi == "BBG001S5TZJ6"


def test_conflicting_share_classes_are_not_evaluable():
    rows = [candidate(figi="V1"), candidate(figi="V2", share_class_figi="OTHER")]
    r = map_cusip_to_project_security(
        cusip="037833100", candidates=rows, project_securities=universe(("sec-aapl", "AAPL"))
    )
    assert r.state == MappingState.NOT_EVALUABLE
    assert r.reason == "CONFLICTING_SHARE_CLASS_IDENTITY"


def test_conflicting_tickers_are_not_evaluable():
    rows = [candidate(figi="V1"), candidate(figi="V2", ticker="MSFT")]
    r = map_cusip_to_project_security(
        cusip="037833100", candidates=rows, project_securities=universe(("sec-aapl", "AAPL"))
    )
    assert r.state == MappingState.NOT_EVALUABLE
    assert r.reason == "CONFLICTING_TICKER_IDENTITY"


def test_multiple_venue_rows_without_stable_identity_are_not_evaluable():
    rows = [
        candidate(figi="V1", composite_figi=None, share_class_figi=None),
        candidate(figi="V2", composite_figi=None, share_class_figi=None),
    ]
    r = map_cusip_to_project_security(
        cusip="037833100", candidates=rows, project_securities=universe(("sec-aapl", "AAPL"))
    )
    assert r.state == MappingState.NOT_EVALUABLE
    assert r.reason == "VENUE_ROWS_LACK_STABLE_SECURITY_IDENTITY"


def test_missing_cusip_is_not_evaluable():
    r = map_cusip_to_project_security(cusip=None, candidates=[candidate()], project_securities=[])
    assert r.reason == "CUSIP_MISSING"


def test_non_exact_cusip_is_not_used():
    r = map_cusip_to_project_security(cusip="999999999", candidates=[candidate()], project_securities=[])
    assert r.reason == "NO_EXACT_IDENTIFIER_RESULT"


def test_non_equity_result_is_not_evaluable():
    r = map_cusip_to_project_security(cusip="037833100", candidates=[candidate(market_sector="Corporate")], project_securities=[])
    assert r.reason == "NO_ELIGIBLE_COMMON_EQUITY_IDENTIFIER_RESULT"


def test_non_common_stock_is_not_evaluable():
    r = map_cusip_to_project_security(cusip="037833100", candidates=[candidate(security_type="Depositary Receipt")], project_securities=[])
    assert r.reason == "NO_ELIGIBLE_COMMON_EQUITY_IDENTIFIER_RESULT"


def test_ticker_outside_project_universe_is_not_evaluable():
    r = map_cusip_to_project_security(cusip="037833100", candidates=[candidate()], project_securities=universe(("sec-msft", "MSFT")))
    assert r.reason == "MAPPED_TICKER_OUTSIDE_PROJECT_UNIVERSE"


def test_duplicate_project_ticker_is_not_evaluable():
    r = map_cusip_to_project_security(cusip="037833100", candidates=[candidate()], project_securities=universe(("a", "AAPL"), ("b", "AAPL")))
    assert r.reason == "PROJECT_TICKER_IDENTITY_AMBIGUOUS"
