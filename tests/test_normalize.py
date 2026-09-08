import pandas as pd

from ussy_fundamentals.normalize import classify_period


def test_classify_period():
    assert classify_period(90) == "quarterly"
    assert classify_period(180) == "half_year"
    assert classify_period(270) == "nine_month"
    assert classify_period(365) == "annual"
    assert classify_period(None) == "instant"


def test_classify_period_nan():
    assert classify_period(pd.NA) == "instant"
