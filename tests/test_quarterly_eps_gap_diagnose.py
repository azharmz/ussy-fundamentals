import pandas as pd

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
    return {
        "facts": {
            "us-gaap": {
                tag: {
                    "units": {unit: entries}
                }
            }
        }
    }


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


def test_classifies_direct_eps_with_comparator_gap():
    payload = _fact("EarningsPerShareDiluted", [
        {"form": "10-Q", "start": "2025-01-01", "end": "2025-03-31", "val": 0.5, "accn": "A1"},
        {"form": "10-Q", "start": "2025-04-01", "end": "2025-06-30", "val": 0.6, "accn": "A2"},
    ])
    filings = [_filing("A1", "2025-03-31"), _filing("A2", "2025-06-30")]
    cls, stats = classify_symbol("TEST", "0000000001", payload, filings)
    assert cls == "DIRECT_EPS_PRESENT_YOY_COMPARATOR_GAP"
    assert stats["direct_current_quarter_periods"] == 2
    assert stats["normalized_recent_eps_yoy_usable"] == 0


def test_classifies_direct_net_income_shares_fallback_inputs():
    payload = {
        "facts": {
            "us-gaap": {
                "NetIncomeLoss": {"units": {"USD": [
                    {"form": "10-Q", "start": "2025-01-01", "end": "2025-03-31", "val": 100, "accn": "A1"},
                    {"form": "10-Q", "start": "2025-04-01", "end": "2025-06-30", "val": 120, "accn": "A2"},
                ]}},
                "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
                    {"form": "10-Q", "start": "2025-01-01", "end": "2025-03-31", "val": 10, "accn": "A1"},
                    {"form": "10-Q", "start": "2025-04-01", "end": "2025-06-30", "val": 10, "accn": "A2"},
                ]}},
            }
        }
    }
    filings = [_filing("A1", "2025-03-31"), _filing("A2", "2025-06-30")]
    cls, stats = classify_symbol("TEST", "0000000001", payload, filings)
    assert cls == "DIRECT_NET_INCOME_SHARES_FALLBACK_AVAILABLE"
    assert stats["fallback_direct_net_income_shares_periods"] == 2
