"""Account view: everything the hub shows for one city, assembled from every source.

Data lineage per block (see docs/data-lineage.md):

    contract      <- contracts spreadsheet (seeded tables)
    tickets       <- historical load  MERGED BY ID WITH  live ticketing sync
    reports       <- historical load  MERGED BY ID WITH  live CS-team tickets
    journeys      <- live journey list (matched by normalised city name)
    usage         <- usage warehouse (daily ETL), reference-month pair only
    csm input     <- typed by the CSM in the hub (sentiment/utilization/satisfaction)
    governance    <- stakeholders, meetings, expansion matrix (hub tables)
    hours         <- technical-hours balance and on-demand requests
    health/churn  <- DERIVED from all of the above, never collected
"""

import asyncio
import logging

from app.clients.ticketing import TicketingUnavailableError
from app.clients.usage_warehouse import UsageWarehouseUnavailableError
from app.clock import Clock
from app.repository import Repository
from app.services import health as hl
from app.services import mapping
from app.services.contracts import contract_view
from app.services.hours import balance
from app.services.indicators import CityNotInWarehouseError, IndicatorsService
from app.services.sync import CityNotFoundError, SyncService

log = logging.getLogger("cshub.accounts")


class UnknownCityError(Exception):
    pass


def _with_kind(r: dict) -> dict:
    # The legacy load has reports without a kind; without it, every weekly
    # report landed in the "service" bucket. Derive it from the title.
    return {**r, "kind": r.get("kind") or mapping.report_kind(r.get("title") or "")}


class AccountService:
    def __init__(self, repo: Repository, sync: SyncService, indicators: IndicatorsService, clock: Clock) -> None:
        self.repo = repo
        self.sync = sync
        self.indicators = indicators
        self.clock = clock

    async def _live(self, name: str) -> tuple[dict | None, list[str], str]:
        try:
            payload = await self.sync.sync(name)
            return payload.as_dict(), list(payload.warnings), "live"
        except CityNotFoundError:
            return (
                None,
                [f"Tickets: '{name}' not found in the ticketing API; showing the historical load only."],
                "snapshot",
            )
        except TicketingUnavailableError as err:
            log.warning("ticketing unavailable for %r: %s", name, err)
            return None, ["Tickets: ticketing API unavailable; showing the historical load only."], "snapshot"

    async def _usage(self, name: str) -> tuple[dict | None, list[str]]:
        try:
            ind = await self.indicators.indicators(name)
        except CityNotInWarehouseError:
            return None, []
        except UsageWarehouseUnavailableError as err:
            log.warning("usage warehouse unavailable for %r: %s", name, err)
            return None, ["Usage: warehouse unavailable; churn computed without the usage factor."]
        cards = (ind.get("panel") or {}).get("cards") or {}
        usage = {d: cards.get(d) or {} for d in ("access", "edits")}
        usage["refMonth"] = (ind.get("panel") or {}).get("refMonth")
        usage["refIncomplete"] = (ind.get("panel") or {}).get("refIncomplete")
        return usage, []

    async def build(self, city_id: str) -> dict:
        city = self.repo.city(city_id)
        if not city:
            raise UnknownCityError(city_id)
        today = self.clock.today()
        (live, warnings, ticket_source), (usage, usage_warn) = await asyncio.gather(
            self._live(city["name"]), self._usage(city["name"])
        )
        warnings += usage_warn

        tickets = mapping.merge_by_id(
            self.repo.snapshot_tickets(city_id),
            (live or {}).get("tickets", []),
            key=lambda t: mapping.ticket_number(t["id"]),
        )
        tickets.sort(key=lambda t: mapping.ticket_number(t["id"]), reverse=True)
        reports = mapping.merge_by_id(
            [_with_kind(r) for r in self.repo.snapshot_reports(city_id)],
            [_with_kind(r) for r in (live or {}).get("reports", [])],
            key=lambda r: r["ticketId"],
        )
        journeys = (live or {}).get("journeys", [])
        stats = mapping.ticket_stats(tickets)
        contract = contract_view(self.repo.contracts(city_id), today)
        hours = balance(self.repo.hours_balance(city_id))

        inputs = hl.HealthInputs(
            today=today,
            journeys=journeys,
            stakeholders=self.repo.stakeholders(city_id),
            meetings=self.repo.meetings(city_id),
            reports=reports,
            csm=self.repo.csm_input(city_id),
            expansion=self.repo.expansion(city_id),
            stats=stats,
            contract=contract,
            usage=usage,
            hours_pct=hours["pct"] if hours else None,
        )
        h = hl.health(inputs)
        risk = hl.risk_class(h, inputs)
        return {
            "city": city,
            "contract": contract,
            "health": h,
            "risk": risk,
            "actions": hl.next_actions(inputs, hours["available"] if hours else None),
            "summary": hl.summary(city["name"], h, risk, inputs),
            "stats": stats,
            "tickets": tickets,
            "reports": reports,
            "journeys": journeys,
            "usage": usage,
            "hours": hours,
            "csmInput": inputs.csm,
            "stakeholders": inputs.stakeholders,
            "meetings": inputs.meetings,
            "expansion": inputs.expansion,
            "sources": {"tickets": ticket_source, "usage": "warehouse" if usage else "none"},
            "warnings": warnings,
        }

    async def overview(self) -> list[dict]:
        """One row per city for the portfolio table.

        Builds every account (concurrently, through the same caches). Fine for
        a handful of cities; at the real fleet size this belongs in a
        background refresher writing a summary table.
        """
        views = await asyncio.gather(*(self.build(c["id"]) for c in self.repo.cities()))
        return [
            {
                "id": v["city"]["id"],
                "name": v["city"]["name"],
                "region": v["city"]["region"],
                "plan": (v["contract"] or {}).get("plan"),
                "csm": (v["contract"] or {}).get("csm"),
                "daysLeft": (v["contract"] or {}).get("daysLeft"),
                "contractStatus": (v["contract"] or {}).get("status"),
                "health": v["health"]["score"],
                "healthStatus": v["health"]["status"],
                "partial": v["health"]["partial"],
                "churn": v["health"]["temperature"]["churn"],
                "risk": v["risk"],
                "openTickets": v["stats"]["open"],
                "critical": v["stats"]["critical"],
                "warnings": len(v["warnings"]),
            }
            for v in views
        ]
