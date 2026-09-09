import pandas as pd

from ussy_fundamentals.quarterly_eps_fallback_validate import TARGET_CLASS, _targets, compare_symbol


def _filing(accn, report_date):
    return {
        "accessionNumber": accn,
        "filingDate": report_date,
        "acceptanceDateTime": f"{report_date}T20:00:00Z",
        "reportDate": report_date,
        "form": "10-Q",
    }


def test_direct_net_income_over_diluted_shares_matches_reported_eps():
    payload = {
        "facts": {"us-gaap": {
            "EarningsPerShareDiluted": {"units": {"USD/shares": [
                {"form":"10-Q","start":"2025-01-01","end":"2025-03-31","val":1.0,"accn":"A1"}
            ]}},
            "NetIncomeLossAvailableToCommonStockholdersBasic": {"units": {"USD": [
                {"form":"10-Q","start":"2025-01-01","end":"2025-03-31","val":100.0,"accn":"A1"}
            ]}},
            "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
                {"form":"10-Q","start":"2025-01-01","end":"2025-03-31","val":100.0,"accn":"A1"}
            ]}},
        }}
    }
    out = compare_symbol("TEST", "0000000001", payload, [_filing("A1", "2025-03-31")])
    assert len(out) == 1
    assert out.iloc[0]["derived_eps"] == 1.0
    assert bool(out.iloc[0]["within_005"])


def test_targets_only_stale_symbols_with_continuing_fallback_inputs():
    diagnosis = pd.DataFrame([
        {"symbol": "HSY", "diagnosis_class": TARGET_CLASS},
        {"symbol": "MGY", "diagnosis_class": TARGET_CLASS},
        {"symbol": "COKE", "diagnosis_class": "STANDARD_EPS_STOPPED_NO_USABLE_REPLACEMENT"},
    ])
    out = _targets(diagnosis)
    assert out["symbol"].tolist() == ["HSY", "MGY"]
