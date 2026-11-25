"""Data lineage, computed instead of written by hand.

The original hub had a lineage document whose coverage numbers ("tickets:
71 of 92 cities") were counted manually on a given day and went stale. Here
the same table is computed on request from the data the hub actually holds.
"""

import asyncio

from app.clients.journeys import JourneySource
from app.clients.ticketing import TicketingClient
from app.repository import Repository
from app.services.indicators import IndicatorsService
from app.services.mapping import normalize_city

SOURCES = [
    {
        "key": "contracts",
        "name": "Contracts spreadsheet",
        "path": "seeded tables (city, contract)",
        "how": "loaded once; days left and status derived on every request",
        "feeds": "Contract card, churn, risk",
    },
    {
        "key": "tickets",
        "name": "Ticketing API",
        "path": "live sync, merged by id over the historical load",
        "how": "GET /api/sync/{city}, 5-min cache",
        "feeds": "Tickets, KPIs, churn, risk",
    },
    {
        "key": "reports",
        "name": "Ticketing API (CS team)",
        "path": "CS-team tickets mapped to reports",
        "how": "same sync, owner team = Customer Success",
        "feeds": "Reports, Meetings dimension",
    },
    {
        "key": "journeys",
        "name": "Customer-journey list",
        "path": "collaboration list, read only",
        "how": "matched by normalised city name; 1 h cache of the city values",
        "feeds": "Journeys, Engagement, churn",
    },
    {
        "key": "usage",
        "name": "Usage warehouse",
        "path": "daily ETL from every city database",
        "how": "GET /api/indicators/{city}; 15-min cache",
        "feeds": "Usage panel, churn",
    },
    {
        "key": "csm",
        "name": "CSM input",
        "path": "typed in the hub, stored with author and time",
        "how": "PUT /api/accounts/{id}/csm-input",
        "feeds": "Sentiment, Utilization, Satisfaction",
    },
    {
        "key": "governance",
        "name": "Stakeholders and meetings",
        "path": "hub tables",
        "how": "maintained by the CS team",
        "feeds": "Engagement, Meetings, Relationship",
    },
    {
        "key": "hours",
        "name": "Technical hours",
        "path": "balance + on-demand request tickets",
        "how": "GET /api/hours",
        "feeds": "Hours panel, risk, actions",
    },
    {
        "key": "residents",
        "name": "Resident weekly reports",
        "path": "report tickets per week",
        "how": "GET /api/residents",
        "feeds": "Resident compliance panel",
    },
]


async def lineage(
    repo: Repository, ticketing: TicketingClient, journeys: JourneySource, indicators: IndicatorsService
) -> dict:
    cities = repo.cities()
    total = len(cities)
    names = [c["name"] for c in cities]

    companies = await asyncio.gather(*(ticketing.search_company(n) for n in names))
    journey_values = await asyncio.gather(*(journeys.matching_values(n) for n in names))
    warehouse = {normalize_city(c["name"]) for c in await indicators.cities()}
    q = repo.conn.execute

    def ids(sql: str) -> set[str]:
        return {r[0] for r in q(sql).fetchall()}

    covered = {
        "contracts": ids("SELECT DISTINCT city_id FROM contract"),
        "tickets": {c["id"] for c, co in zip(cities, companies, strict=False) if co}
        | ids("SELECT DISTINCT city_id FROM snapshot_ticket"),
        "reports": ids("SELECT DISTINCT city_id FROM snapshot_report")
        | {c["id"] for c, co in zip(cities, companies, strict=False) if co},
        "journeys": {c["id"] for c, v in zip(cities, journey_values, strict=False) if v},
        "usage": {c["id"] for c in cities if normalize_city(c["name"]) in warehouse},
        "csm": ids("SELECT city_id FROM csm_input WHERE coalesce(sentiment, utilization, satisfaction) IS NOT NULL"),
        "governance": ids("SELECT city_id FROM stakeholder UNION SELECT city_id FROM meeting"),
        "hours": ids("SELECT city_id FROM hours_balance UNION SELECT city_id FROM hours_request"),
        "residents": ids("SELECT city_id FROM resident"),
    }
    by_id = {c["id"]: c["name"] for c in cities}
    out = []
    for s in SOURCES:
        have = covered[s["key"]] & set(by_id)
        out.append({**s, "covered": len(have), "total": total, "missing": sorted(by_id[i] for i in set(by_id) - have)})
    return {
        "cities": total,
        "sources": out,
        "derived": [
            {"name": "Health Score", "from": "sentiment, engagement, utilization, meetings"},
            {"name": "Churn risk", "from": "usage drop, contract end, tickets, relationship, late journeys"},
        ],
    }
