"""Business clock pinned to a fixed UTC-3 offset.

The ticketing API returns naive timestamps in local business time. Comparing
them with the server's local clock breaks as soon as the server runs in UTC
(counts shift around midnight), so every "today"/"now" goes through here.
"""

from datetime import date, datetime, timedelta, timezone

BUSINESS_TZ = timezone(timedelta(hours=-3))


class Clock:
    def __init__(self, fixed_today: date | None = None) -> None:
        self._fixed = fixed_today

    def today(self) -> date:
        return self._fixed or datetime.now(BUSINESS_TZ).date()

    def now(self) -> datetime:
        """Naive business-time ``now`` (same convention as the ticketing API)."""
        real = datetime.now(BUSINESS_TZ).replace(tzinfo=None)
        if self._fixed is None:
            return real
        return datetime.combine(self._fixed, real.time())
