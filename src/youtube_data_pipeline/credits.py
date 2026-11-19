from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")

SEARCH_BUCKET_ENDPOINTS: frozenset[str] = frozenset({"search"})

YRP_SEARCH_CREDIT_LIMIT = 200_000
YRP_GENERAL_CREDIT_LIMIT = 50_000
YRP_DAILY_TOTAL_CREDIT_LIMIT = 250_000

YRP_DEFAULT_ENDPOINT_CALL_LIMITS: dict[str, int] = {
    "search": 2_000,
    "videos": 4_000,
    "channels": 1_000,
    "playlistItems": 2_000,
    "commentThreads": 20_000,
    "comments": 8_000,
    "videoCategories": 10,
}


def bucket_for_endpoint(endpoint: str) -> str:
    """Return the YRP quota bucket name for a given endpoint."""
    return "search_list" if endpoint in SEARCH_BUCKET_ENDPOINTS else "general_read"


def current_pacific_quota_day(now: datetime | None = None) -> str:
    """Return the active Google Cloud quota day (YYYY-MM-DD) in Pacific Time."""
    dt = now if now is not None else datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(PACIFIC_TZ).strftime("%Y-%m-%d")


def pacific_quota_day_window_utc(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return `(start_utc, reset_utc)` for the current Pacific Time quota day (00:00 PT reset)."""
    dt = now if now is not None else datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    pt_now = dt.astimezone(PACIFIC_TZ)
    pt_start = datetime(
        pt_now.year,
        pt_now.month,
        pt_now.day,
        0,
        0,
        0,
        tzinfo=PACIFIC_TZ,
    )
    next_date = pt_now.date() + timedelta(days=1)
    pt_reset = datetime(
        next_date.year,
        next_date.month,
        next_date.day,
        0,
        0,
        0,
        tzinfo=PACIFIC_TZ,
    )
    return pt_start.astimezone(timezone.utc), pt_reset.astimezone(timezone.utc)


def seconds_until_pacific_quota_reset(now: datetime | None = None) -> float:
    """Return seconds remaining until the 00:00 Pacific Time daily quota reset."""
    dt = now if now is not None else datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    _, reset_utc = pacific_quota_day_window_utc(dt)
    return max(0.0, (reset_utc - dt.astimezone(timezone.utc)).total_seconds())


@dataclass(slots=True)
class CreditMonitor:
    total_spent: int = 0
    per_endpoint: dict[str, int] = field(default_factory=dict)
    calls_per_endpoint: dict[str, int] = field(default_factory=dict)
    search_credit_limit: int | None = None
    general_credit_limit: int | None = None
    per_endpoint_call_limits: dict[str, int] = field(default_factory=dict)
    # register() pode ser chamado por várias threads (coleta paralela); sem lock o
    # `+=` perde incrementos (subcontagem) e o dict pode corromper sob concorrência.
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )

    @classmethod
    def for_yrp(
        cls,
        *,
        search_credit_limit: int | None = YRP_SEARCH_CREDIT_LIMIT,
        general_credit_limit: int | None = YRP_GENERAL_CREDIT_LIMIT,
        per_endpoint_call_limits: Mapping[str, int] | None = None,
    ) -> CreditMonitor:
        """Build a CreditMonitor configured with YRP dual-bucket and per-endpoint caps."""
        call_limits = (
            dict(per_endpoint_call_limits)
            if per_endpoint_call_limits is not None
            else dict(YRP_DEFAULT_ENDPOINT_CALL_LIMITS)
        )
        return cls(
            search_credit_limit=search_credit_limit,
            general_credit_limit=general_credit_limit,
            per_endpoint_call_limits=call_limits,
        )

    @property
    def search_spent(self) -> int:
        with self._lock:
            return self._search_spent_locked()

    @property
    def general_spent(self) -> int:
        with self._lock:
            return self._general_spent_locked()

    def _search_spent_locked(self) -> int:
        return sum(
            cost
            for endpoint, cost in self.per_endpoint.items()
            if endpoint in SEARCH_BUCKET_ENDPOINTS
        )

    def _general_spent_locked(self) -> int:
        return sum(
            cost
            for endpoint, cost in self.per_endpoint.items()
            if endpoint not in SEARCH_BUCKET_ENDPOINTS
        )

    def check_allowance(
        self,
        endpoint: str,
        cost: int,
        *,
        credit_limit: int | None = None,
        search_credit_limit: int | None = None,
        general_credit_limit: int | None = None,
        per_endpoint_call_limits: Mapping[str, int] | None = None,
    ) -> str | None:
        """Return None if the call fits all active limits, or the stop reason string."""
        with self._lock:
            if credit_limit is not None and (self.total_spent + cost > credit_limit):
                return "credit_limit"

            eff_search_limit = (
                search_credit_limit if search_credit_limit is not None else self.search_credit_limit
            )
            if (
                endpoint in SEARCH_BUCKET_ENDPOINTS
                and eff_search_limit is not None
                and (self._search_spent_locked() + cost > eff_search_limit)
            ):
                return "search_credit_limit"

            eff_general_limit = (
                general_credit_limit
                if general_credit_limit is not None
                else self.general_credit_limit
            )
            if (
                endpoint not in SEARCH_BUCKET_ENDPOINTS
                and eff_general_limit is not None
                and (self._general_spent_locked() + cost > eff_general_limit)
            ):
                return "general_credit_limit"

            eff_call_limits = (
                per_endpoint_call_limits
                if per_endpoint_call_limits is not None
                else self.per_endpoint_call_limits
            )
            if eff_call_limits and endpoint in eff_call_limits:
                current_calls = self.calls_per_endpoint.get(endpoint, 0)
                if current_calls + 1 > eff_call_limits[endpoint]:
                    return "endpoint_call_limit"

            return None

    def register(
        self,
        endpoint: str,
        cost: int,
        *,
        call_count: int = 1,
    ) -> dict[str, Any]:
        with self._lock:
            self.total_spent += cost
            self.per_endpoint[endpoint] = self.per_endpoint.get(endpoint, 0) + cost
            if call_count > 0:
                self.calls_per_endpoint[endpoint] = (
                    self.calls_per_endpoint.get(endpoint, 0) + call_count
                )
            return self._snapshot_locked()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict[str, Any]:
        return {
            "total_spent": self.total_spent,
            "per_endpoint": dict(self.per_endpoint),
            "per_bucket": {
                "search_list": self._search_spent_locked(),
                "general_read": self._general_spent_locked(),
            },
            "calls_per_endpoint": dict(self.calls_per_endpoint),
        }
