import json

import pandas as pd
import pytest

from ussy_fundamentals.universe_source import eligible_membership, fetch_current_universe


class _Body:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FakeS3:
    def __init__(self, responses):
        self.responses = {key: list(values) for key, values in responses.items()}

    def get_object(self, Bucket, Key):
        values = self.responses[Key]
        payload = values[0] if len(values) == 1 else values.pop(0)
        return {"Body": _Body(payload)}


def test_eligible_membership_uses_compliance_not_halal_rating():
    membership = {
        "records": [
            {
                "security_id": "US1",
                "ticker": "AAA",
                "sharia_compliance": "COMPLIANT",
                "musaffaHalalRating": "NOT_HALAL",
            },
            {
                "security_id": "US2",
                "ticker": "BBB",
                "sharia_compliance": "NON_COMPLIANT",
                "musaffaHalalRating": "COMPLIANT",
            },
        ]
    }

    out = eligible_membership(membership)
    assert list(out["symbol"]) == ["AAA"]
    assert list(out["security_id"]) == ["US1"]


def test_fetch_current_universe_validates_pointer_and_count():
    pointer = {
        "snapshot_date": "2026-08-28",
        "membership_key": "universe/membership/2026-08-28.json",
        "security_master_key": "universe/security_master/2026-08-28.json",
        "change_key": "universe/changes/2026-08-28.json",
        "confirmed_compliant": 2,
    }
    membership = {
        "snapshot_date": "2026-08-28",
        "records": [
            {"security_id": "US1", "ticker": "AAA", "sharia_compliance": "COMPLIANT"},
            {"security_id": "US2", "ticker": "BBB", "sharia_compliance": "COMPLIANT"},
        ],
    }
    client = FakeS3(
        {
            "universe/current.json": [pointer, pointer],
            "universe/membership/2026-08-28.json": [membership],
        }
    )

    out, meta = fetch_current_universe(client=client, bucket="bucket")
    assert list(out["symbol"]) == ["AAA", "BBB"]
    assert meta["eligible_count"] == 2
    assert meta["snapshot_date"] == "2026-08-28"


def test_fetch_current_universe_retries_when_pointer_moves():
    pointer_a = {
        "snapshot_date": "2026-08-28",
        "membership_key": "universe/membership/2026-08-28.json",
        "security_master_key": "universe/security_master/2026-08-28.json",
        "confirmed_compliant": 1,
    }
    pointer_b = {
        "snapshot_date": "2026-09-01",
        "membership_key": "universe/membership/2026-09-01.json",
        "security_master_key": "universe/security_master/2026-09-01.json",
        "confirmed_compliant": 1,
    }
    membership_a = {
        "snapshot_date": "2026-08-28",
        "records": [{"security_id": "US1", "ticker": "AAA", "sharia_compliance": "COMPLIANT"}],
    }
    membership_b = {
        "snapshot_date": "2026-09-01",
        "records": [{"security_id": "US2", "ticker": "BBB", "sharia_compliance": "COMPLIANT"}],
    }
    client = FakeS3(
        {
            "universe/current.json": [pointer_a, pointer_b, pointer_b, pointer_b],
            "universe/membership/2026-08-28.json": [membership_a],
            "universe/membership/2026-09-01.json": [membership_b],
        }
    )

    out, meta = fetch_current_universe(client=client, bucket="bucket")
    assert list(out["symbol"]) == ["BBB"]
    assert meta["snapshot_date"] == "2026-09-01"


def test_fetch_current_universe_rejects_count_mismatch():
    pointer = {
        "snapshot_date": "2026-08-28",
        "membership_key": "universe/membership/2026-08-28.json",
        "security_master_key": "universe/security_master/2026-08-28.json",
        "confirmed_compliant": 2,
    }
    membership = {
        "snapshot_date": "2026-08-28",
        "records": [{"security_id": "US1", "ticker": "AAA", "sharia_compliance": "COMPLIANT"}],
    }
    client = FakeS3(
        {
            "universe/current.json": [pointer, pointer],
            "universe/membership/2026-08-28.json": [membership],
        }
    )

    with pytest.raises(ValueError, match="confirmed_compliant"):
        fetch_current_universe(client=client, bucket="bucket")
