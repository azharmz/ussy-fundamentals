"""Exact identity gate for SEC 13F CUSIP -> project security mapping.

This module is intentionally provider-agnostic. A caller may use OpenFIGI or
another audited identifier service upstream, but fuzzy issuer/ticker matching is
never performed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

VERSION = "sec-13f-identity-mapping-v1"


class MappingState(str, Enum):
    MAPPED = "MAPPED"
    NOT_EVALUABLE = "NOT_EVALUABLE"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class IdentifierCandidate:
    source_cusip: str
    figi: str | None
    ticker: str | None
    market_sector: str | None
    security_type: str | None
    exchange_code: str | None
    provider: str
    provider_observed_at: str


@dataclass(frozen=True)
class ProjectSecurity:
    security_id: str
    ticker: str


@dataclass(frozen=True)
class MappingResult:
    state: MappingState
    reason: str
    security_id: str | None = None
    ticker: str | None = None
    figi: str | None = None
    provider: str | None = None
    version: str = VERSION


def map_cusip_to_project_security(
    *,
    cusip: str | None,
    candidates: Sequence[IdentifierCandidate],
    project_securities: Sequence[ProjectSecurity],
) -> MappingResult:
    """Resolve only an exact, unique identifier-service result into project identity.

    Rules:
    - CUSIP must be present and exactly match the upstream candidate's source CUSIP.
    - Candidate must identify an equity instrument and carry a ticker + FIGI.
    - Exactly one eligible candidate is required; ambiguity is NOT_EVALUABLE.
    - Candidate ticker must exactly match exactly one project security ticker.
    - No issuer-name, prefix, share-class, or approximate ticker fallback is allowed.
    """
    if not cusip:
        return MappingResult(MappingState.NOT_EVALUABLE, "CUSIP_MISSING")

    exact = [c for c in candidates if c.source_cusip == cusip]
    if not exact:
        return MappingResult(MappingState.NOT_EVALUABLE, "NO_EXACT_IDENTIFIER_RESULT")

    eligible = [
        c
        for c in exact
        if c.figi
        and c.ticker
        and (c.market_sector or "").casefold() == "equity"
    ]
    if not eligible:
        return MappingResult(MappingState.NOT_EVALUABLE, "NO_ELIGIBLE_EQUITY_IDENTIFIER_RESULT")
    if len(eligible) != 1:
        return MappingResult(MappingState.NOT_EVALUABLE, "AMBIGUOUS_IDENTIFIER_RESULT")

    chosen = eligible[0]
    matches = [s for s in project_securities if s.ticker == chosen.ticker]
    if not matches:
        return MappingResult(
            MappingState.NOT_EVALUABLE,
            "MAPPED_TICKER_OUTSIDE_PROJECT_UNIVERSE",
            ticker=chosen.ticker,
            figi=chosen.figi,
            provider=chosen.provider,
        )
    if len(matches) != 1:
        return MappingResult(
            MappingState.NOT_EVALUABLE,
            "PROJECT_TICKER_IDENTITY_AMBIGUOUS",
            ticker=chosen.ticker,
            figi=chosen.figi,
            provider=chosen.provider,
        )

    security = matches[0]
    return MappingResult(
        MappingState.MAPPED,
        "EXACT_CUSIP_PROVIDER_RESULT_AND_PROJECT_TICKER_MATCH",
        security_id=security.security_id,
        ticker=security.ticker,
        figi=chosen.figi,
        provider=chosen.provider,
    )
