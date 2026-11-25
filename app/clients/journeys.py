"""Customer-journey list (a collaboration-suite list) — read only.

Two layers:

- ``JourneyListClient`` is the raw list API: distinct values of the City
  column, and the items for an exact set of values. ``HttpJourneyListClient``
  talks OData-verbose with ``__next`` paging; ``SeedJourneyListClient`` reads
  SQLite.
- ``JourneySource`` holds the matching rule, which is the part that matters.
  The list stores the city exactly as someone typed it ("OAKWOOD - VL",
  "WILLOWBROOKE", "STONE BRIDGE"), and the list API's ``eq`` only ignores
  case. Filtering by the hub's name silently returned 0 journeys for every
  city spelled differently. So we read the real column values first, match
  them with ``normalize_city`` and filter by the exact values found.
"""

import sqlite3
import time
from typing import ClassVar, Protocol
from urllib.parse import quote

import httpx

from app.clients.ticketing import odata_str
from app.db import rows
from app.services.mapping import normalize_city

CITY_FIELD = "City"
# City values change rarely (a new city joins the list); re-reading the
# ~2k rows on every hub page open is waste.
VALUES_TTL_SECONDS = 60 * 60


class CityNotInListError(Exception):
    """No value of the list's City column matches the requested city."""


class JourneyListClient(Protocol):
    async def city_values(self) -> list[str]: ...

    async def items(self, values: list[str], quarter: str | None = None) -> list[dict]: ...


class HttpJourneyListClient:
    def __init__(self, list_url: str, token_provider, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.list_url = list_url.rstrip("/")
        self.token_provider = token_provider
        self._transport = transport

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token_provider()}", "Accept": "application/json;odata=verbose"}

    async def _paged(self, url: str) -> list[dict]:
        """Follow ``d.__next`` to the end: ``$top`` alone truncated big cities."""
        out: list[dict] = []
        async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
            while url:
                resp = await client.get(url, headers=self._headers())
                resp.raise_for_status()
                d = resp.json().get("d", {})
                out.extend(d.get("results", []))
                url = d.get("__next")
        return out

    async def city_values(self) -> list[str]:
        items = await self._paged(f"{self.list_url}/items?$select={CITY_FIELD}&$top=1000&$orderby=ID asc")
        return sorted({i[CITY_FIELD] for i in items if i.get(CITY_FIELD)})

    async def items(self, values: list[str], quarter: str | None = None) -> list[dict]:
        by_city = " or ".join(f"{CITY_FIELD} eq '{odata_str(v)}'" for v in values)
        flt = f"({by_city})"
        if quarter:
            flt += f" and Quarter eq '{odata_str(quarter)}'"
        items = await self._paged(f"{self.list_url}/items?$filter={quote(flt)}&$top=500&$orderby=ID asc")
        return [_clean(i) for i in items]


def _clean(item: dict) -> dict:
    """Drop ``__metadata`` and deferred navigation properties."""
    return {
        k: v
        for k, v in item.items()
        if k != "__metadata" and not (isinstance(v, dict) and ("__deferred" in v or "__metadata" in v))
    }


class SeedJourneyListClient:
    COLUMNS: ClassVar[dict[str, str]] = {
        "ID": "id",
        "City": "city_value",
        "Department": "department",
        "Quarter": "quarter",
        "Owner": "owner",
        "Sector": "sector",
        "Objective": "objective",
        "Plan": "plan",
        "Audience": "audience",
        "InitialIndicator": "initial_indicator",
        "SuccessIndicator": "success_indicator",
        "Layers": "layers",
        "Result": "result",
        "PercentComplete": "percent_complete",
        "Status": "status",
        "Modified": "modified",
        "Category": "category",
    }

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.value_reads = 0

    async def city_values(self) -> list[str]:
        self.value_reads += 1
        return [r["city_value"] for r in rows(self.conn, "SELECT DISTINCT city_value FROM journey_item ORDER BY 1")]

    async def items(self, values: list[str], quarter: str | None = None) -> list[dict]:
        if not values:
            return []
        cols = ", ".join(f"{col} AS {api}" for api, col in self.COLUMNS.items())
        marks = ",".join("?" * len(values))
        sql = f"SELECT {cols} FROM journey_item WHERE city_value IN ({marks})"
        params: list = list(values)
        if quarter:
            sql += " AND quarter = ?"
            params.append(quarter)
        return rows(self.conn, sql + " ORDER BY id", tuple(params))


class JourneySource:
    def __init__(self, client: JourneyListClient, clock=time.monotonic) -> None:
        self.client = client
        self._clock = clock
        self._values: tuple[float, list[str]] | None = None

    async def _city_values(self) -> list[str]:
        if self._values and self._values[0] > self._clock():
            return self._values[1]
        values = await self.client.city_values()
        self._values = (self._clock() + VALUES_TTL_SECONDS, values)
        return values

    async def matching_values(self, city_name: str) -> list[str]:
        target = normalize_city(city_name)
        return [v for v in await self._city_values() if normalize_city(v) == target]

    async def journeys(self, city_name: str, quarter: str | None = None) -> list[dict]:
        values = await self.matching_values(city_name)
        if not values:
            raise CityNotInListError(city_name)
        return await self.client.items(values, quarter)
