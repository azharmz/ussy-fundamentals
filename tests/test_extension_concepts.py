import pandas as pd

from ussy_fundamentals.normalize import REVENUE_TAGS, fact_rows


def _filings():
    return {
        "accn-1": {
            "filingDate": "2016-05-04",
            "acceptanceDateTime": "2016-05-04T16:16:40Z",
            "reportDate": "2016-03-31",
        }
    }


def _entry(value):
    return {
        "accn": "accn-1",
        "form": "10-Q",
        "start": "2016-01-01",
        "end": "2016-03-31",
        "filed": "2016-05-04",
        "fy": 2016,
        "fp": "Q1",
        "val": value,
    }


def test_revenue_tags_include_legacy_sales_and_other_operating_revenue():
    assert "SalesAndOtherOperatingRevenue" in REVENUE_TAGS


def test_fact_rows_reads_whitelisted_extension_concept():
    companyfacts = {
        "facts": {
            "xom": {
                "SalesAndOtherOperatingRevenue": {
                    "units": {"USD": [_entry(47_105_000_000)]}
                }
            }
        }
    }

    out = fact_rows(companyfacts, REVENUE_TAGS, "revenue", _filings())

    assert len(out) == 1
    assert out.iloc[0]["tag"] == "SalesAndOtherOperatingRevenue"
    assert out.iloc[0]["taxonomy"] == "xom"
    assert out.iloc[0]["value"] == 47_105_000_000


def test_us_gaap_wins_priority_over_same_named_extension_concept():
    companyfacts = {
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": [_entry(100)]}},
            },
            "issuer": {
                "Revenues": {"units": {"USD": [_entry(200)]}},
            },
        }
    }

    out = fact_rows(companyfacts, ["Revenues"], "revenue", _filings())
    ranked = out.sort_values("tag_priority")

    assert list(ranked["taxonomy"]) == ["us-gaap", "issuer"]
    assert list(ranked["value"]) == [100, 200]
