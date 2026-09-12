import pytest

from ussy_fundamentals.normalize import REVENUE_TAGS, normalize_company


def test_including_assessed_tax_is_second_priority_revenue_tag():
    assert REVENUE_TAGS[:3] == [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
    ]


def test_normalize_uses_including_assessed_tax_revenue_and_same_filing_yoy():
    accn = "0000000000-25-000001"
    companyfacts = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerIncludingAssessedTax": {
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-03-31",
                                "val": 100.0,
                                "accn": accn,
                                "form": "10-Q",
                                "fy": 2025,
                                "fp": "Q1",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-03-31",
                                "val": 120.0,
                                "accn": accn,
                                "form": "10-Q",
                                "fy": 2025,
                                "fp": "Q1",
                            },
                        ]
                    }
                }
            }
        }
    }
    filings = [
        {
            "accessionNumber": accn,
            "filingDate": "2025-04-25",
            "acceptanceDateTime": "2025-04-25T20:00:00Z",
            "reportDate": "2025-03-31",
            "form": "10-Q",
        }
    ]

    out = normalize_company("TEST", "0000000000", companyfacts, filings)
    revenue = out[out["metric"].eq("revenue")]

    assert len(revenue) == 1
    row = revenue.iloc[0]
    assert row["tag"] == "RevenueFromContractWithCustomerIncludingAssessedTax"
    assert row["value"] == 120.0
    assert row["yoy"] == pytest.approx(0.20)
    assert row["yoy_source"] == "SAME_FILING_COMPARATIVE"
