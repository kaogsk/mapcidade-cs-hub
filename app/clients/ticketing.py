"""Ticketing API (helpdesk SaaS) — one interface, two implementations.

``HttpTicketingClient`` speaks the real API's dialect (token in the query
string, OData ``$filter``). ``SeedTicketingClient`` answers the same two calls
from SQLite, with the same quirk that shaped the whole design: only the most
recent N tickets come back per query.
"""

import sqlite3
from typing import Protocol

import httpx

from app.db import rows


class TicketingUnavailableError(Exception):
    """The ticketing API failed (network, auth, 5xx)."""


class TicketingClient(Protocol):
    async def search_company(self, city_name: str) -> dict | None: ...

    async def search_tickets(
        self,
        client_org: str | None = None,
        client_id: int | str | None = None,
        owner_team: str | None = None,
        limit: int = 50,
    ) -> list[dict]: ...


def odata_str(value: object) -> str:
    """Value ready for a single-quoted OData literal: ``'`` becomes ``''``.

    Without this, "O'Connell Falls" closes the literal mid-way, the API
    answers 400 and the hub's endpoint answered 500.
    """
    return str(value).replace("'", "''")


def prefer_city_hall(results: list[dict]) -> dict | None:
    """Several companies can contain the city name; the city hall wins."""
    if not results:
        return None
    preferred = [r for r in results if "city hall" in (r.get("businessName") or "").lower()]
    return (preferred or results)[0]


class HttpTicketingClient:
    def __init__(self, base_url: str, token: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._transport = transport

    async def _get(self, path: str, params: dict) -> list | dict:
        try:
            async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
                resp = await client.get(f"{self.base_url}{path}", params={**params, "token": self.token})
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as err:
            raise TicketingUnavailableError(type(err).__name__) from err

    async def search_company(self, city_name: str) -> dict | None:
        params = {
            "$select": "id,businessName,personType",
            "$filter": f"personType eq 2 and contains(tolower(businessName), '{odata_str(city_name.lower())}')",
        }
        return prefer_city_hall(await self._get("/persons", params) or [])

    async def search_tickets(self, client_org=None, client_id=None, owner_team=None, limit=50) -> list[dict]:
        filters = []
        if client_org:
            filters.append(f"clients/any(c: c/businessName eq '{odata_str(client_org)}')")
        if client_id:
            # Looks numeric, but ``c/id`` is a string in this schema: unquoted
            # it fails with "incompatible types". Checked against the live API.
            filters.append(f"clients/any(c: c/id eq '{odata_str(client_id)}')")
        if owner_team:
            filters.append(f"ownerTeam eq '{odata_str(owner_team)}'")
        params = {
            "$select": "id,subject,status,urgency,category,createdDate,ownerTeam,service,clients",
            "$top": limit,
            "$orderby": "createdDate desc",
        }
        if filters:
            params["$filter"] = " and ".join(filters)
        result = await self._get("/tickets", params)
        return result if isinstance(result, list) else []


class SeedTicketingClient:
    """Answers from the ``helpdesk_*`` tables, same shapes as the HTTP client."""

    def __init__(self, conn: sqlite3.Connection, recent_limit: int = 50) -> None:
        self.conn = conn
        self.recent_limit = recent_limit
        self.calls: list[tuple] = []

    async def search_company(self, city_name: str) -> dict | None:
        self.calls.append(("company", city_name))
        found = rows(
            self.conn,
            "SELECT id, business_name AS businessName, person_type AS personType "
            "FROM helpdesk_company WHERE person_type = 2 AND instr(lower(business_name), lower(?)) > 0 "
            "ORDER BY id",
            (city_name,),
        )
        return prefer_city_hall(found)

    async def search_tickets(self, client_org=None, client_id=None, owner_team=None, limit=50) -> list[dict]:
        self.calls.append(("tickets", client_org, client_id, owner_team))
        where, params = [], []
        if client_org:
            where.append("t.org_name = ?")
            params.append(client_org)
        if client_id:
            where.append("CAST(t.company_id AS TEXT) = ?")
            params.append(str(client_id))
        if owner_team:
            where.append("t.owner_team = ?")
            params.append(owner_team)
        sql = (
            "SELECT t.* FROM helpdesk_ticket t"
            + (" WHERE " + " AND ".join(where) if where else "")
            + " ORDER BY t.created DESC LIMIT ?"
        )
        params.append(min(limit, self.recent_limit))
        return [_as_api_ticket(r) for r in rows(self.conn, sql, tuple(params))]


def _as_api_ticket(r: dict) -> dict:
    return {
        "id": r["id"],
        "subject": r["subject"],
        "status": r["status"],
        "urgency": r["urgency"],
        "category": r["category"],
        "createdDate": r["created"],
        "ownerTeam": r["owner_team"],
        "service": r["service"],
        "requester": r["requester"],
        "clients": [
            {
                "id": str(r["company_id"]) if r["company_id"] else None,
                "organization": {"businessName": r["org_name"]} if r["org_name"] else None,
            }
        ],
    }
