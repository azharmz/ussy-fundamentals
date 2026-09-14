"""Exact identity gate for SEC 13F CUSIP -> project security mapping.

This module is intentionally provider-agnostic. A caller may use OpenFIGI or
another audited identifier service upstream, but fuzzy issuer/ticker matching is
never performed here.

Identifier services may legitimately return several venue-level FIGIs for the
same share class/security. Those rows are not treated as ambiguity when their
stable security identity converges.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

VERSION = "sec-13f-identity-mapping-v1.1"


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
    composite_figi: str | None = None
    share_class_figi: str | None = None


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
    composite_figi: str | None = None
    share_class_figi: str | None = None
    provider: str | None = None
    version: str = VERSION


def _norm(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value.upper() if value else None


def map_cusip_to_project_security(
    *,
    cusip: str | None,
    candidates: Sequence[IdentifierCandidate],
    project_securities: Sequence[ProjectSecurity],
) -> MappingResult:
    """Resolve exact CUSIP results after collapsing venue-level duplicates.

    Rules:
    - CUSIP must exactly match the upstream candidate's source CUSIP.
    - Candidate must be Equity, Common Stock, and carry ticker + FIGI.
    - Multiple venue rows are acceptable only when they converge on the same
      ticker and, when present, the same Share Class FIGI / Composite FIGI.
    - Conflicting stable security identities remain NOT_EVALUABLE.
    - Candidate ticker must exactly match exactly one project security ticker.
    - No issuer-name, prefix, share-class guess, or approximate ticker fallback.
    """
    cusip_norm = _norm(cusip)
    if not cusip_norm:
        return MappingResult(MappingState.NOT_EVALUABLE, "CUSIP_MISSING")

    exact = [c for c in candidates if _norm(c.source_cusip) == cusip_norm]
    if not exact:
        return MappingResult(MappingState.NOT_EVALUABLE, "NO_EXACT_IDENTIFIER_RESULT")

    eligible = [
        c
        for c in exact
        if c.figi
        and _norm(c.ticker)
        and _norm(c.market_sector) == "EQUITY"
        and _norm(c.security_type) == "COMMON STOCK"
    ]
    if not eligible:
        return MappingResult(MappingState.NOT_EVALUABLE, "NO_ELIGIBLE_COMMON_EQUITY_IDENTIFIER_RESULT")

    tickers = {_norm(c.ticker) for c in eligible}
    share_classes = {_norm(c.share_class_figi) for c in eligible if _norm(c.share_class_figi)}
    composites = {_norm(c.composite_figi) for c in eligible if _norm(c.composite_figi)}
    providers = {_norm(c.provider) for c in eligible}

    if len(tickers) != 1:
        return MappingResult(MappingState.NOT_EVALUABLE, "CONFLICTING_TICKER_IDENTITY")
    if len(share_classes) > 1:
        return MappingResult(MappingState.NOT_EVALUABLE, "CONFLICTING_SHARE_CLASS_IDENTITY")
    if len(composites) > 1:
        return MappingResult(MappingState.NOT_EVALUABLE, "CONFLICTING_COMPOSITE_IDENTITY")
    if len(providers) != 1:
        return MappingResult(MappingState.NOT_EVALUABLE, "MIXED_IDENTIFIER_PROVIDERS")

    # When several venue rows exist, require at least one stable non-venue FIGI
    # dimension so venue-specific FIGIs alone cannot be silently collapsed.
    if len(eligible) > 1 and not share_classes and not composites:
        return MappingResult(MappingState.NOT_EVALUABLE, "VENUE_ROWS_LACK_STABLE_SECURITY_IDENTITY")

    chosen = eligible[0]
    ticker = next(iter(tickers))
    share_class = next(iter(share_classes)) if share_classes else None
    composite = next(iter(composites)) if composites else None

    matches = [s for s in project_securities if _norm(s.ticker) == ticker]
    if not matches:
        return MappingResult(
            MappingState.NOT_EVALUABLE,
            "MAPPED_TICKER_OUTSIDE_PROJECT_UNIVERSE",
            ticker=ticker,
            figi=chosen.figi,
            composite_figi=composite,
            share_class_figi=share_class,
            provider=chosen.provider,
        )
    if len(matches) != 1:
        return MappingResult(
            MappingState.NOT_EVALUABLE,
            "PROJECT_TICKER_IDENTITY_AMBIGUOUS",
            ticker=ticker,
            figi=chosen.figi,
            composite_figi=composite,
            share_class_figi=share_class,
            provider=chosen.provider,
        )

    security = matches[0]
    return MappingResult(
        MappingState.MAPPED,
        "EXACT_CUSIP_CONVERGED_SECURITY_IDENTITY_AND_PROJECT_TICKER_MATCH",
        security_id=security.security_id,
        ticker=security.ticker,
        figi=chosen.figi,
        composite_figi=composite,
        share_class_figi=share_class,
        provider=chosen.provider,
    )
