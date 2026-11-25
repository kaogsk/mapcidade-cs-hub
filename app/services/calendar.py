"""Business-day arithmetic shared by ticket SLA and resident-report deadlines."""

from collections.abc import Iterable
from datetime import date, timedelta


class BusinessCalendar:
    def __init__(self, holidays: Iterable[date] = ()) -> None:
        self.holidays = frozenset(holidays)

    def is_business_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def business_days_between(self, start: date, end: date) -> int:
        """Business days ``d`` with ``start < d <= end`` (0 when ``end <= start``)."""
        count, d = 0, start
        while d < end:
            d += timedelta(days=1)
            if self.is_business_day(d):
                count += 1
        return count

    def add_business_days(self, start: date, n: int) -> date:
        """The ``n``-th business day strictly after ``start``."""
        if n < 1:
            raise ValueError("n must be >= 1")
        d, found = start, 0
        while found < n:
            d += timedelta(days=1)
            if self.is_business_day(d):
                found += 1
        return d
