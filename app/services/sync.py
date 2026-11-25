"""Live sync of one city: ticketing API + journey list -> the hub payload.

This replaces the manual routine the CS team used to run by hand (look the
city up in the helpdesk, search its tickets twice, split support from CS
reports, fetch journeys, paste everything into the hub).

Failure policy, learned the hard way: when the journey source's login was
revoked (a password change on the account that owned the delegated token),
the whole endpoint answered 500 for a week and took the *tickets* down with
it. Now each secondary source is isolated: its section comes back empty, a
human-readable reason goes into ``warnings``, and a partial answer is never
cached, so the next request after the source recovers is complete again.
"""

import logging
import time
from dataclasses import dataclass, field

from app.clients.journeys import CityNotInListError, JourneySource
from app.clients.ticketing import TicketingClient
from app.clock import Clock
from app.services import mapping
from app.services.calendar import BusinessCalendar

log = logging.getLogger("cshub.sync")


class CityNotFoundError(Exception):
    """The ticketing API has no company for this city."""


class JourneyLoginExpiredError(Exception):
    """Delegated token for the journey list was revoked; someone must log in again."""


@dataclass
class SyncPayload:
    city: str
    tickets: list[dict]
    reports: list[dict]
    journeys: list[dict]
    stats: dict
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "city": self.city,
            "tickets": self.tickets,
            "reports": self.reports,
            "journeys": self.journeys,
            "stats": self.stats,
            "warnings": self.warnings,
        }


class SyncService:
    def __init__(
        self,
        ticketing: TicketingClient,
        journeys: JourneySource,
        clock: Clock,
        calendar: BusinessCalendar,
        cache_ttl_seconds: int = 300,
        monotonic=time.monotonic,
    ) -> None:
        self.ticketing = ticketing
        self.journeys = journeys
        self.clock = clock
        self.calendar = calendar
        self.ttl = cache_ttl_seconds
        self._monotonic = monotonic
        self._cache: dict[str, tuple[float, SyncPayload]] = {}

    def cached(self, city_name: str) -> SyncPayload | None:
        hit = self._cache.get(mapping.normalize_city(city_name))
        return hit[1] if hit and hit[0] > self._monotonic() else None

    async def sync(self, city_name: str) -> SyncPayload:
        key = mapping.normalize_city(city_name)
        if self.ttl > 0:
            hit = self.cached(city_name)
            if hit:
                return hit
        payload, source_failed = await self._fetch(city_name)
        # A city that is simply not in the journey list is a fact, not a
        # failure: that answer is cached like any other.
        if self.ttl > 0 and not source_failed:
            self._cache[key] = (self._monotonic() + self.ttl, payload)
        return payload

    async def _search_twice(self, company: dict, owner_team: str | None = None) -> list[dict]:
        # By organization name AND by company id: each search alone misses
        # tickets (requesters filed under a department's org name are only
        # linked by id; old tickets only by name). Dedupe by ticket id.
        by_org = await self.ticketing.search_tickets(client_org=company["businessName"], owner_team=owner_team)
        by_id = await self.ticketing.search_tickets(client_id=company["id"], owner_team=owner_team)
        return list({t["id"]: t for t in by_org + by_id}.values())

    async def _fetch(self, city_name: str) -> tuple[SyncPayload, bool]:
        company = await self.ticketing.search_company(city_name)
        if not company:
            raise CityNotFoundError(city_name)

        today = self.clock.today()
        support = [
            mapping.map_support_ticket(t, today, self.calendar)
            for t in await self._search_twice(company)
            if mapping.is_support_ticket(t)
        ]
        reports = [mapping.map_cs_report(t) for t in await self._search_twice(company, mapping.CS_TEAM)]

        warnings: list[str] = []
        source_failed = False
        try:
            journeys = [mapping.map_journey(j) for j in await self.journeys.journeys(city_name)]
        except CityNotInListError:
            journeys = []
            warnings.append(f"Journeys: '{city_name}' matches no city in the customer-journey list.")
        except Exception as err:
            log.warning("journey source unavailable for %r: %s: %s", city_name, type(err).__name__, err)
            journeys = []
            source_failed = True
            warnings.append(_journey_warning(err))

        return SyncPayload(
            city=mapping.normalize_city(city_name),
            tickets=support,
            reports=reports,
            journeys=journeys,
            stats=mapping.ticket_stats(support),
            warnings=warnings,
        ), source_failed


def _journey_warning(err: Exception) -> str:
    """Short message for the UI; full detail stays in the log."""
    if isinstance(err, JourneyLoginExpiredError):
        return (
            "Journeys not refreshed: the server's login to the journey list expired or was revoked. "
            "Someone has to log in again on the server."
        )
    return f"Journeys not refreshed: {type(err).__name__}."
