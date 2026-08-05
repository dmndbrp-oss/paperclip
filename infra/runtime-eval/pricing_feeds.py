"""Typed read-adapter contract for the pricing staleness-detection feeds (SAG-6327 Phase 0).

Three upstream feeds back the staleness runner (SAG-6302 plan):
  1. Rate-record store   - active rate records w/ rate_card_version, imported_at, content_hash.
  2. Negotiated-rate change log - append-only manual rate changes (drives the SLA signal).
  3. Margin/SUT policy-change log - append-only policy changes (suppresses anomaly alerts).

None of the three physical feeds exist yet. Per SAG-6337 (Director of Pricing, resolved):
Pricing owns the data-semantics spec (filed as SAG-6341); Engineering owns the physical
store + these read entrypoints (SAG-6343, blocked on SAG-6341). Until SAG-6341 lands, the
Not-Implemented adapters below raise FeedUnavailableError rather than silently returning
empty/fake data, so the detection runner (SAG-6344) fails loudly instead of reporting a
false "no staleness detected."

The Fake* adapters exist so the runner and its tests can be built today against this
contract, ahead of the real feeds.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping, Optional


class FeedUnavailableError(RuntimeError):
    """Raised when a real feed is queried before its physical store exists.

    Callers must handle this by escalating, not by treating it as "zero results."
    """


@dataclass(frozen=True)
class RateRecord:
    """One Pricing Rev 2 rate observation at its declared record grain."""

    record_key: str
    product_estimate_group: str
    fee_bucket: str
    territory: str
    fee_per_sqft: Decimal
    cost_basis_per_sqft: Decimal
    install_adder_per_sqft: Decimal
    rate_card_version: int
    imported_at: datetime
    content_hash: str
    effective_at: Optional[datetime] = None
    source: Optional[str] = None
    last_verified_at: Optional[datetime] = None
    notes: Optional[str] = None

    @property
    def rate_bearing_fields(self) -> Mapping[str, Decimal]:
        """The exact ordered field set used by Pricing's content hash."""
        return MappingProxyType(
            {
                "fee_per_sqft": self.fee_per_sqft,
                "cost_basis_per_sqft": self.cost_basis_per_sqft,
                "install_adder_per_sqft": self.install_adder_per_sqft,
            }
        )

    @property
    def bucket_code(self) -> str:
        """Compatibility accessor for the pre-Rev-2 runner until its Task 4 update."""
        return self.fee_bucket


@dataclass(frozen=True)
class NegotiatedRateChange:
    """One append-only entry in the negotiated-rate change log."""

    record_key: str
    old_value: Decimal
    new_value: Decimal
    source: str
    reason: str
    entered_at: datetime
    committed_at: Optional[datetime] = None


@dataclass(frozen=True)
class PolicyChange:
    """One append-only entry in the margin/SUT policy-change log."""

    policy_key: str
    old: str
    new: str
    effective_at: datetime
    reason: str


class RateRecordFeed(ABC):
    @abstractmethod
    def get_active_rate_records(self, as_of: datetime) -> list[RateRecord]:
        """Return Pricing import observations available as of the given timestamp."""


class NegotiatedRateChangeFeed(ABC):
    @abstractmethod
    def get_changes_since(self, since: datetime) -> list[NegotiatedRateChange]:
        """Return negotiated-rate changes entered since the given timestamp."""


class MarginPolicyChangeFeed(ABC):
    @abstractmethod
    def get_changes_since(self, since: datetime) -> list[PolicyChange]:
        """Return margin/SUT policy changes effective since the given timestamp."""


class NotImplementedRateRecordFeed(RateRecordFeed):
    def get_active_rate_records(self, as_of: datetime) -> list[RateRecord]:
        raise FeedUnavailableError(
            "Rate-record store does not exist yet. Column spec pending SAG-6341 "
            "(Pricing feed-spec); physical store build tracked as SAG-6343."
        )


class NotImplementedNegotiatedRateChangeFeed(NegotiatedRateChangeFeed):
    def get_changes_since(self, since: datetime) -> list[NegotiatedRateChange]:
        raise FeedUnavailableError(
            "Negotiated-rate change log does not exist yet. Format spec pending SAG-6341; "
            "physical store build tracked as SAG-6343."
        )


class NotImplementedMarginPolicyChangeFeed(MarginPolicyChangeFeed):
    def get_changes_since(self, since: datetime) -> list[PolicyChange]:
        raise FeedUnavailableError(
            "Margin/SUT policy-change log does not exist yet. Format spec pending SAG-6341; "
            "physical store build tracked as SAG-6343."
        )


@dataclass
class FakeRateRecordFeed(RateRecordFeed):
    """In-memory feed for detection-runner development/tests (SAG-6344), ahead of the real store."""

    records: list[RateRecord] = field(default_factory=list)

    def get_active_rate_records(self, as_of: datetime) -> list[RateRecord]:
        return [r for r in self.records if r.imported_at <= as_of]


@dataclass
class FakeNegotiatedRateChangeFeed(NegotiatedRateChangeFeed):
    changes: list[NegotiatedRateChange] = field(default_factory=list)

    def get_changes_since(self, since: datetime) -> list[NegotiatedRateChange]:
        return [c for c in self.changes if c.entered_at >= since]


@dataclass
class FakeMarginPolicyChangeFeed(MarginPolicyChangeFeed):
    changes: list[PolicyChange] = field(default_factory=list)

    def get_changes_since(self, since: datetime) -> list[PolicyChange]:
        return [c for c in self.changes if c.effective_at >= since]
