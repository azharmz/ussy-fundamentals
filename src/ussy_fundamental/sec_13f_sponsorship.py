"""PIT semantic contract for SEC Form 13F institutional-sponsorship evidence.

This module deliberately does not fetch filings and does not infer IBD-style
accumulation/distribution.  It validates whether a filed 13F holding is usable
as-of a decision timestamp and preserves accession/amendment lineage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

CONTRACT_VERSION = "sec-13f-institutional-sponsorship-v1"
SUPPORTED_FORMS = frozenset({"13F-HR", "13F-HR/A"})


class EvidenceState(str, Enum):
    USABLE = "USABLE"
    NOT_YET_AVAILABLE = "NOT_YET_AVAILABLE"
    NOT_EVALUABLE = "NOT_EVALUABLE"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class HoldingLineage:
    manager_cik: str
    accession_number: str
    form_type: str
    report_period: str
    accepted_at: Optional[datetime]
    cusip: Optional[str]
    issuer_name: Optional[str] = None
    title_of_class: Optional[str] = None
    value: Optional[int] = None
    shares_or_principal: Optional[float] = None
    share_principal_type: Optional[str] = None
    put_call: Optional[str] = None
    source_id: Optional[str] = None


@dataclass(frozen=True)
class Assessment:
    state: EvidenceState
    reason: str
    contract_version: str = CONTRACT_VERSION


def assess_holding_asof(holding: HoldingLineage, asof: datetime) -> Assessment:
    """Assess whether one filed holding may be used at *asof*.

    Reporting-period end is never used as the availability clock.  The filing
    must have an explicit accepted-at timestamp and it must be <= asof.
    """
    if holding.form_type not in SUPPORTED_FORMS:
        return Assessment(EvidenceState.REJECTED, "UNSUPPORTED_FORM")
    if not holding.manager_cik or not holding.accession_number:
        return Assessment(EvidenceState.NOT_EVALUABLE, "MISSING_ACCESSION_LINEAGE")
    if holding.accepted_at is None:
        return Assessment(EvidenceState.NOT_EVALUABLE, "MISSING_AVAILABILITY_TIMESTAMP")
    if holding.accepted_at > asof:
        return Assessment(EvidenceState.NOT_YET_AVAILABLE, "FILING_NOT_AVAILABLE_ASOF")
    if not holding.cusip:
        return Assessment(EvidenceState.NOT_EVALUABLE, "MISSING_SECURITY_IDENTITY")
    return Assessment(EvidenceState.USABLE, "FILED_HOLDING_AVAILABLE_ASOF")


def identity_mapping_authorized(*, exact_audited_mapping: bool) -> Assessment:
    """Gate ticker/security-id aggregation until an exact audited map exists."""
    if not exact_audited_mapping:
        return Assessment(EvidenceState.NOT_EVALUABLE, "SECURITY_IDENTITY_MAPPING_NOT_AUDITED")
    return Assessment(EvidenceState.USABLE, "SECURITY_IDENTITY_MAPPING_AUDITED")
