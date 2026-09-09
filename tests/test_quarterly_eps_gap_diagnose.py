from ussy_fundamentals.quarterly_eps_gap_diagnose import classify_symbol


def _filing(accn, report_date, form="10-Q"):
    return {
        "accessionNumber": accn,
        "filingDate": report_date,
        "acceptanceDateTime": f"{report_date}T20:00:00Z",
        "reportDate": report_date,
        "form": form,
    }


def _fact(tag, entries, unit="USD/shares"):
    return {"facts": {"us-gaap": {tag: {"units": {unit: entries}}}}}


def test_classifies_ytd_only_eps():
    payload = _fact("EarningsPerShareDiluted", [
        {"form": "10-Q", "start": "2025-01-01", "end": "2025-06-30", "val": 1.0, "accn": "A1"},
        {"form": "10-Q", "start": "2025-01-01", "end": "2025-09-30", "val": 1.5, "accn": "A2"},
    ])
    filings = [_filing("A1", "2025-06-30"), _filing("A2", "2025-09-30")]
    cls, stats = classify_symbol("TEST", "0000000001", payload, filings)
    assert cls == "YTD_ONLY_OR_MOSTLY_YTD"
    assert stats["ytd_current_periods"] == 2


def test_classifies_nonstandard_eps_tags_only():
    payload = _fact("IncomeLossFromContinuingOperationsPerDilutedShare", [
        {"form": "10-Q", "start": "2025-01-01", "end": "2025-03-31", "val": 0.5, "accn": "A1"},
    ])
    filings = [_filing("A1", "2025-03-31")]
    cls, stats = classify_symbol("TEST", "0000000001", payload, filings)
    assert cls == "NONSTANDARD_EPS_TAGS_ONLY"
    assert "IncomeLossFromContinuingOperationsPerDilutedShare" in stats["nonconfigured_eps_like_tags"]


def test_classifies_short_quarterly_history_as_expected():
    payload = _fact("EarningsPerShareDiluted", [
        {"form": "10-Q", "start": "2025-01-01", "end": "2025-03-31", "val": 0.5, "accn": "A1"},
        {"form": "10-Q", "start": "2025-04-01", "end": "2025-06-30", "val": 0.6, "accn": "A2"},
    ])
    filings = [_filing("A1", "2025-03-31"), _filing("A2", "2025-06-30")]
    cls, stats = classify_symbol("TEST", "0000000001", payload, filings)
    assert cls == "SHORT_QUARTERLY_HISTORY_EXPECTED"
    assert stats["direct_current_quarter_periods"] == 2


def test_classifies_longer_history_without_positive_comparators():
    # Five genuine discrete quarters spanning >330 days, intentionally without a
    # prior period inside the normal 330-400 day YoY matching band.
    starts = ["2024-01-01", "2024-04-10", "2024-07-20", "2024-10-30", "2025-02-08"]
    ends = ["2024-03-31", "2024-07-09", "2024-10-18", "2025-01-28", "2025-05-09"]
    entries = []
    filings = []
    for i, (start, end) in enumerate(zip(starts, ends), 1):
        accn = f"A{i}"
        entries.append({"form":"10-Q","start":start,"end":end,"val":0.5+i/10,"accn":accn})
        filings.append(_filing(accn, end))
    payload = _fact("EarningsPerShareDiluted", entries)
    cls, stats = classify_symbol("TEST", "0000000001", payload, filings)
    assert cls == "POSITIVE_BASE_COMPARATORS_INSUFFICIENT"
    assert stats["direct_current_quarter_periods"] == 5
    assert stats["direct_history_span_days"] > 330
    assert stats["positive_base_comparator_periods"] == 0


def test_classifies_nonpositive_prior_base_as_data_constraint():
    entries = [
        {"form":"10-Q","start":"2024-01-01","end":"2024-03-31","val":-0.50,"accn":"A1"},
        {"form":"10-Q","start":"2024-04-01","end":"2024-06-30","val":-0.40,"accn":"A2"},
        {"form":"10-Q","start":"2024-07-01","end":"2024-09-30","val":-0.30,"accn":"A3"},
        {"form":"10-Q","start":"2025-01-01","end":"2025-03-31","val":0.10,"accn":"A4"},
        {"form":"10-Q","start":"2025-04-01","end":"2025-06-30","val":0.20,"accn":"A5"},
    ]
    filings = [_filing(f"A{i}", e["end"]) for i, e in enumerate(entries, 1)]
    payload = _fact("EarningsPerShareDiluted", entries)
    cls, stats = classify_symbol("TEST", "0000000001", payload, filings)
    assert cls == "POSITIVE_BASE_COMPARATORS_INSUFFICIENT"
    assert stats["positive_base_comparator_periods"] == 0
    assert stats["nonpositive_base_comparator_periods"] >= 2


def test_classifies_stale_standard_eps_evidence():
    payload = _fact("EarningsPerShareDiluted", [
        {"form":"10-Q","start":"2023-01-01","end":"2023-03-31","val":0.5,"accn":"A1"},
        {"form":"10-Q","start":"2023-04-01","end":"2023-06-30","val":0.6,"accn":"A2"},
    ])
    filings = [_filing("A1", "2023-03-31"), _filing("A2", "2023-06-30"), _filing("A3", "2025-06-30")]
    cls, stats = classify_symbol("TEST", "0000000001", payload, filings)
    assert cls == "STANDARD_EPS_EVIDENCE_STALE"
    assert stats["direct_evidence_stale_days"] > 180


def test_classifies_direct_net_income_shares_fallback_inputs():
    payload = {"facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": [
            {"form": "10-Q", "start": "2025-01-01", "end": "2025-03-31", "val": 100, "accn": "A1"},
            {"form": "10-Q", "start": "2025-04-01", "end": "2025-06-30", "val": 120, "accn": "A2"},
        ]}},
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
            {"form": "10-Q", "start": "2025-01-01", "end": "2025-03-31", "val": 10, "accn": "A1"},
            {"form": "10-Q", "start": "2025-04-01", "end": "2025-06-30", "val": 10, "accn": "A2"},
        ]}},
    }}}
    filings = [_filing("A1", "2025-03-31"), _filing("A2", "2025-06-30")]
    cls, stats = classify_symbol("TEST", "0000000001", payload, filings)
    assert cls == "DIRECT_NET_INCOME_SHARES_FALLBACK_AVAILABLE"
    assert stats["fallback_direct_net_income_shares_periods"] == 2
