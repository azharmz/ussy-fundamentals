import pandas as pd

from ussy_fundamentals.annual_gap_diagnose import classify_gap


def _fact(tag, form="10-K", years=(2022, 2023, 2024)):
    return {
        "facts": {
            "us-gaap": {
                tag: {
                    "units": {
                        "USD/shares": [
                            {"form": form, "start": f"{y}-01-01", "end": f"{y}-12-31", "val": 1.0}
                            for y in years
                        ]
                    }
                }
            }
        }
    }


def test_classifies_standard_eps_history_available():
    payload = _fact("EarningsPerShareDiluted")
    cls, stats = classify_gap(2, payload)
    assert cls == "STANDARD_EPS_HISTORY_AVAILABLE"
    assert stats["standard_eps_annual_periods"] == 3


def test_classifies_fallback_input_history_available():
    payload = {
        "facts": {
            "us-gaap": {
                "NetIncomeLoss": {"units": {"USD": [
                    {"form": "10-K", "start": f"{y}-01-01", "end": f"{y}-12-31", "val": 100}
                    for y in (2022, 2023, 2024)
                ]}},
                "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
                    {"form": "10-K", "start": f"{y}-01-01", "end": f"{y}-12-31", "val": 10}
                    for y in (2022, 2023, 2024)
                ]}},
            }
        }
    }
    cls, stats = classify_gap(1, payload)
    assert cls == "FALLBACK_INPUT_HISTORY_AVAILABLE"
    assert stats["fallback_pair_annual_periods"] == 3


def test_classifies_nonstandard_entity_semantics():
    payload = {"facts": {"us-gaap": {"LimitedPartnersCapital": {"units": {}}}}}
    cls, stats = classify_gap(1, payload)
    assert cls == "NONSTANDARD_ENTITY_EPS_SEMANTICS"
    assert "LimitedPartnersCapital" in stats["semantic_hint_tags"]
