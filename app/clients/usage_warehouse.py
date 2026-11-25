"""Usage warehouse — the read-only BI fed by a daily ETL from every city database.

The hub only ever does ``GET path?params`` against the same JSON routes the
BI's own screens use, so the interface is a single ``get``:

- ``HttpUsageWarehouse`` logs in with a service account, keeps the session
  cookie and re-logs once on 401. Parallel requests that all get 401 at the
  same time must not trigger N logins: a lock plus a session counter makes
  only the first one log in and the rest reuse the new session.
- ``SqliteUsageWarehouse`` computes the same JSON from synthetic events.
"""

import asyncio
import re
import sqlite3
from datetime import date, timedelta
from typing import Any

import httpx

from app.db import one, rows

DOMAINS = ("access", "mobile", "edits", "certificates")
# Per domain: the "item" dimension and which dimension the variation ranks.
ITEM_KEY = {"access": None, "mobile": "theme", "edits": "theme", "certificates": "certificate"}
PUBLIC_USER = ""  # certificates issued from the public portal have no user


class UsageWarehouseUnavailableError(Exception):
    """Warehouse down, login refused or non-200 answer."""


class HttpUsageWarehouse:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._credentials = (username, password)
        self._transport = transport
        self._timeout = timeout
        self._cookies = httpx.Cookies()
        self._login_lock = asyncio.Lock()
        self._session = 0  # bumps on every successful login
        self.logins = 0

    async def _login(self, client: httpx.AsyncClient) -> None:
        user, password = self._credentials
        r = await client.post("/auth/login", json={"username": user, "password": password})
        if r.status_code != 200:
            # Do not echo the body: it may carry directory details.
            raise UsageWarehouseUnavailableError(f"login refused (HTTP {r.status_code})")
        self._cookies = httpx.Cookies(client.cookies)
        self._session += 1
        self.logins += 1

    async def get(self, path: str, params: Any = None) -> Any:
        session_used = self._session
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=self._timeout, cookies=self._cookies, transport=self._transport
        ) as client:
            try:
                r = await client.get(path, params=params)
                if r.status_code == 401:
                    async with self._login_lock:
                        if self._session == session_used:
                            await self._login(client)
                    client.cookies = httpx.Cookies(self._cookies)
                    r = await client.get(path, params=params)
            except httpx.HTTPError as err:
                raise UsageWarehouseUnavailableError(f"unreachable: {type(err).__name__}") from err
            if r.status_code != 200:
                raise UsageWarehouseUnavailableError(f"HTTP {r.status_code} on {path}")
            return r.json()


def month_minus(ym: str, k: int = 1) -> str:
    """'2026-01' - 1 -> '2025-12' (negative k moves forward)."""
    idx = int(ym[:4]) * 12 + int(ym[5:7]) - 1 - k
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def last_complete_month(last_day: date) -> str:
    """Last fully covered month up to ``last_day`` (the BI's reference month rule)."""
    d = last_day + timedelta(days=1)
    return month_minus(f"{d.year:04d}-{d.month:02d}", 1)


class SqliteUsageWarehouse:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.requests: list[str] = []

    async def get(self, path: str, params: Any = None) -> Any:
        self.requests.append(path)
        p = dict(params or {})
        if path == "/api/meta":
            return self._meta()
        if path == "/api/etl":
            return {"watermarks": rows(self.conn, "SELECT database, routine, last_day AS lastDay FROM etl_watermark")}
        if path == "/api/options":
            r = one(
                self.conn,
                "SELECT min(day) AS min, max(day) AS max FROM usage_event WHERE database = ?",
                (p.get("city"),),
            )
            return {"period": r}
        m = re.fullmatch(r"/api/tables/(\w+)", path)
        if m and m.group(1) in DOMAINS:
            return self._monthly_table(m.group(1), p)
        m = re.fullmatch(r"/api/(\w+)", path)
        if m and m.group(1) in DOMAINS:
            return self._domain_page(m.group(1), p)
        raise UsageWarehouseUnavailableError(f"HTTP 404 on {path}")

    def _meta(self) -> dict:
        last = one(self.conn, "SELECT max(last_day) AS d FROM etl_watermark")["d"]
        ref = last_complete_month(date.fromisoformat(last)) if last else None
        run = one(
            self.conn,
            "SELECT started_at AS startedAt, finished_at AS finishedAt, status "
            "FROM etl_run ORDER BY finished_at DESC LIMIT 1",
        )
        cities = rows(self.conn, "SELECT database, name, has_etl AS hasEtl FROM usage_city ORDER BY name")
        return {
            "dataUntil": last,
            "refMonth": ref,
            "prevMonth": month_minus(ref) if ref else None,
            "lastEtlRun": run,
            "cities": [{**c, "hasEtl": bool(c["hasEtl"])} for c in cities],
        }

    def _where(self, domain: str, p: dict) -> tuple[str, list]:
        sql, args = "database = ? AND domain = ?", [p.get("city"), domain]
        if p.get("from"):
            sql += " AND day >= ?"
            args.append(p["from"])
        if p.get("to"):
            sql += " AND day <= ?"
            args.append(p["to"])
        return sql, args

    def _ranking(self, column: str, where: str, args: list) -> dict:
        items = rows(
            self.conn,
            f"SELECT {column} AS k, sum(n) AS value FROM usage_event WHERE {where} "
            f"GROUP BY {column} ORDER BY value DESC, k",
            tuple(args),
        )
        return {"items": items, "total": len(items)}

    def _variation(self, domain: str, column: str, city: str) -> dict:
        meta = self._meta()
        ref, prev = meta["refMonth"], meta["prevMonth"]
        if not ref:
            return {"items": [], "total": 0}
        items = rows(
            self.conn,
            f"SELECT {column} AS k, "
            "sum(CASE WHEN substr(day,1,7) = ? THEN n ELSE 0 END) - "
            "sum(CASE WHEN substr(day,1,7) = ? THEN n ELSE 0 END) AS variation "
            "FROM usage_event WHERE database = ? AND domain = ? AND substr(day,1,7) IN (?, ?) "
            f"GROUP BY {column} ORDER BY variation, k",
            (ref, prev, city, domain, ref, prev),
        )
        return {"items": items, "total": len(items)}

    def _domain_page(self, domain: str, p: dict) -> dict:
        where, args = self._where(domain, p)
        city = p.get("city")
        total = one(
            self.conn,
            f"SELECT coalesce(sum(n), 0) AS t, count(DISTINCT user) AS u, "
            f"count(DISTINCT profile) AS pr, count(DISTINCT item) AS it "
            f"FROM usage_event WHERE {where}",
            tuple(args),
        )
        page: dict = {
            "byMonth": rows(
                self.conn,
                f"SELECT substr(day,1,7) AS month, sum(n) AS value FROM usage_event "
                f"WHERE {where} GROUP BY 1 ORDER BY 1",
                tuple(args),
            ),
            "byUser": self._ranking("user", where, args),
            "byProfile": self._ranking("profile", where, args),
        }
        item = ITEM_KEY[domain]
        if domain == "access":
            page["cards"] = {"total": total["t"], "activeUsers": total["u"], "profiles": total["pr"]}
            page["variation"] = self._variation(domain, "user", city)
            return page
        configured = one(
            self.conn,
            "SELECT count(DISTINCT item) AS c FROM usage_catalog " "WHERE database = ? AND domain = ?",
            (city, domain),
        )["c"]
        used = total["it"]
        page["cards"] = {
            "total": total["t"],
            "used": used,
            "configured": configured or None,
            "rate": round(used / configured, 4) if configured else None,
        }
        page["byItem"] = self._ranking("item", where, args)
        page["variation"] = self._variation(domain, "item", city)
        page["itemKey"] = item
        return page

    def _monthly_table(self, domain: str, p: dict) -> dict:
        sql, args = "database = ? AND domain = ?", [p.get("city"), domain]
        if p.get("to"):
            sql += " AND day <= ?"
            args.append(p["to"])
        return {
            "rows": rows(
                self.conn,
                f"SELECT substr(day,1,7) AS date, sum(n) AS n FROM usage_event " f"WHERE {sql} GROUP BY 1 ORDER BY 1",
                tuple(args),
            )
        }
