"""Usage indicators for one city, from the usage warehouse.

Before this existed, the hub's usage block was scraped by hand from a BI
report, and stayed frozen at the scrape date ("updated on ..." meant "someone
copied it that day", not "data until"). Now it comes from the same daily ETL
the BI reads, with a period filter, the ETL's real finish time and a per-city
"data until" watermark.

Semantics that must survive into the UI:
- totals and rankings honour the period (``from``/``to``, inclusive);
- "variation" = reference month (last complete month) minus the month before.
  It ignores the chosen period, exactly like the BI does;
- the monthly panel (``build_panel``) never computes a variation for a partial
  month: half a month against a full one reads as a fake drop.
"""

import asyncio
import re
import time
from datetime import date, datetime, timedelta
from typing import Any

from app.clients.usage_warehouse import DOMAINS, last_complete_month, month_minus
from app.clock import BUSINESS_TZ
from app.services.mapping import normalize_city

TOP_N = 8
PUBLIC_PORTAL = "(Public portal)"
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


class CityNotInWarehouseError(Exception):
    """The city has no database read by the ETL and no historical load."""


def _num(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _int(v: Any) -> int | None:
    return None if v is None else int(round(_num(v)))


def _label(v: Any, empty: str = "—") -> str:
    s = "" if v is None else str(v).strip()
    return s or empty


def _iso(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


def _br(s: str | None) -> str | None:
    d = _iso(s)
    return d.strftime("%d/%m/%Y") if d else None


def _br_time(s: str | None) -> str | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(BUSINESS_TZ)
    except ValueError:
        return None
    return dt.strftime("%d/%m/%Y %H:%M")


def month_label(ym: str | None) -> str | None:
    if not ym or not re.match(r"^\d{4}-\d{2}$", ym):
        return None
    return f"{MONTHS[int(ym[5:7]) - 1]}/{ym[:4]}"


def end_of_month(ym: str) -> date:
    nxt = month_minus(ym, -1)
    return date(int(nxt[:4]), int(nxt[5:7]), 1) - timedelta(days=1)


def ranking(items: list[dict], empty: str = "—", n: int = TOP_N) -> list[dict]:
    return [{"name": _label(x.get("k"), empty), "value": _int(x.get("value"))} for x in items[:n]]


def drops(items: list[dict], n: int = TOP_N) -> list[dict]:
    neg = sorted((x for x in items if _num(x.get("variation")) < 0), key=lambda x: _num(x.get("variation")))
    return [{"name": _label(x.get("k")), "value": _int(x.get("variation"))} for x in neg[:n]]


def sum_variation(items: list[dict]) -> int:
    # Every event falls in exactly one category, so the sum of per-category
    # variations is the city's variation (the BI does not expose that total).
    return int(sum(_num(x.get("variation")) for x in items))


def _series(items: list[dict], month_key: str, value_key: str) -> dict[str, int]:
    return {str(x[month_key]): _int(x.get(value_key)) for x in items if x.get(month_key)}


def build_panel(
    history: dict[str, list[dict]],
    period_by_month: dict[str, list[dict]],
    from_: str | None,
    to: str | None,
    data_until_global: str | None,
    data_until_city: str | None,
    has_etl: bool,
) -> dict | None:
    """Month-over-month panel for one city, cut to the hub's period. Pure.

    ``history``: full monthly series per domain (no ``from``, up to ``to``),
    needed because the month before the period, and the pair of months on the
    cards, may fall outside it. ``period_by_month``: the period-filtered
    series, used only for a first month that the period starts mid-way.
    """
    hist = {d: _series(history.get(d) or [], "date", "n") for d in DOMAINS}
    cut = {d: _series(period_by_month.get(d) or [], "month", "value") for d in DOMAINS}
    first = {d: min(hist[d]) if hist[d] else None for d in DOMAINS}
    all_months = sorted({m for d in DOMAINS for m in hist[d]})
    if not all_months:
        return None

    d_from, d_to = _iso(from_), _iso(to)
    # Where this city's data ends: the ETL watermark; without ETL (historical
    # load only), the end of its last month.
    data_end = (_iso(data_until_city) if has_etl else None) or end_of_month(all_months[-1])
    end = min(x for x in (d_to, data_end) if x)

    start_month = f"{d_from.year:04d}-{d_from.month:02d}" if d_from else all_months[0]
    end_month = f"{end.year:04d}-{end.month:02d}"
    months: list[str] = []
    m = start_month
    while m <= end_month and len(months) < 600:
        months.append(m)
        m = month_minus(m, -1)

    def previous(dom: str, month: str) -> int | None:
        p = month_minus(month)
        if first[dom] is None or p < first[dom]:
            return None  # start of the series: nothing to compare with
        return hist[dom].get(p, 0)

    lines = []
    for month in months:
        partial_start = bool(d_from and month == start_month and d_from.day > 1)
        partial_end = month == end_month and end < end_of_month(month)
        partial = partial_start or partial_end
        values = {}
        for dom in DOMAINS:
            if first[dom] is None:
                values[dom] = {"value": None, "previous": None, "variation": None}
                continue
            value = cut[dom].get(month, 0) if partial_start else hist[dom].get(month, 0)
            prev = None if partial else previous(dom, month)
            values[dom] = {"value": value, "previous": prev, "variation": None if prev is None else value - prev}
        lines.append({"month": month, "label": month_label(month), "partial": partial, "values": values})

    # Cards: the BI's rule, with the period end in place of the global "data until".
    ref_end_candidates = [x for x in (d_to, _iso(data_until_global)) if x]
    ref = last_complete_month(min(ref_end_candidates) if ref_end_candidates else end)
    prev_m = month_minus(ref)
    cards = {}
    for dom in DOMAINS:
        last, before = hist[dom].get(ref), hist[dom].get(prev_m)
        cards[dom] = {
            "last": last,
            "previous": before,
            "variation": None if last is None and before is None else (last or 0) - (before or 0),
        }
    return {
        "refMonth": ref,
        "prevMonth": prev_m,
        "label": f"{month_label(ref)} × {month_label(prev_m)}",
        # City data ends before the reference month does: the card compares an
        # incomplete month (ETL stalled, or city only has a historical load).
        "refIncomplete": data_end < end_of_month(ref),
        "dataUntil": data_end.isoformat(),
        "cards": cards,
        "months": lines,
    }


def build_indicators(
    city: dict,
    meta: dict,
    watermarks: list[dict],
    pages: dict[str, dict],
    from_: str | None,
    to: str | None,
    available: dict | None,
    history: dict[str, list[dict]] | None = None,
) -> dict:
    """Pure: warehouse answers -> the hub's usage object."""
    marks = [w.get("lastDay") for w in watermarks if w.get("database") == city["database"] and w.get("lastDay")]
    # A city is only as fresh as its slowest ETL routine.
    data_until = min(marks) if marks else meta.get("dataUntil")
    run = meta.get("lastEtlRun") or {}
    panel = (
        build_panel(
            history,
            {d: pages[d].get("byMonth") or [] for d in DOMAINS},
            from_,
            to,
            meta.get("dataUntil"),
            data_until,
            bool(city.get("hasEtl")),
        )
        if history is not None
        else None
    )
    if not city.get("hasEtl") and panel:
        # No watermark for a history-only city: falling back to the global
        # "data until" claimed yesterday's data for a city frozen months ago.
        data_until = panel["dataUntil"]
    ac = pages["access"]
    ref, prev = meta.get("refMonth"), meta.get("prevMonth")
    return {
        "city": city["name"],
        "database": city["database"],
        "source": "usage-warehouse",
        "hasEtl": bool(city.get("hasEtl")),
        "updatedAt": _br_time(run.get("finishedAt")) or _br(meta.get("dataUntil")),
        "etlStatus": run.get("status"),
        "dataUntil": _br(data_until),
        "refMonth": ref,
        "prevMonth": prev,
        "variationLabel": f"{month_label(ref)} × {month_label(prev)}" if ref and prev else None,
        "period": {"from": from_, "to": to, "min": (available or {}).get("min"), "max": (available or {}).get("max")},
        "panel": panel,
        "access": {
            "total": _int(ac["cards"].get("total")),
            "users": _int(ac["cards"].get("activeUsers")),
            "profiles": _int(ac["cards"].get("profiles")),
            "variation": sum_variation(ac["variation"]["items"]),
            "byMonth": ac.get("byMonth", []),
            "topUsers": ranking(ac["byUser"]["items"]),
            "topProfiles": ranking(ac["byProfile"]["items"]),
            "dropUsers": drops(ac["variation"]["items"]),
        },
        "edits": _item_domain(pages["edits"]),
        "mobile": _item_domain(pages["mobile"]),
        "certificates": _item_domain(pages["certificates"], empty_user=PUBLIC_PORTAL),
    }


def _item_domain(page: dict, empty_user: str = "—") -> dict:
    cards = page["cards"]
    return {
        "total": _int(cards.get("total")),
        "users": _int(page["byUser"].get("total")),
        "itemsUsed": _int(cards.get("used")),
        "itemsConfigured": _int(cards.get("configured")),
        "rate": cards.get("rate"),
        "variation": sum_variation(page["variation"]["items"]),
        "byMonth": page.get("byMonth", []),
        "topItems": ranking(page["byItem"]["items"]),
        # An empty user on certificates = issued from the public portal.
        "topUsers": ranking(page["byUser"]["items"], empty=empty_user),
        "topProfiles": ranking(page["byProfile"]["items"]),
        "dropItems": drops(page["variation"]["items"]),
    }


class IndicatorsService:
    def __init__(self, warehouse, cache_ttl_seconds: int = 900, clock=time.monotonic) -> None:
        self.warehouse = warehouse
        self.ttl = cache_ttl_seconds
        self._clock = clock
        self._cache: dict[tuple, tuple[float, dict]] = {}
        self._meta: tuple[float, dict, list[dict]] | None = None

    async def _meta_and_watermarks(self) -> tuple[dict, list[dict]]:
        if self._meta and self._meta[0] > self._clock():
            return self._meta[1], self._meta[2]
        meta, etl = await asyncio.gather(self.warehouse.get("/api/meta"), self.warehouse.get("/api/etl"))
        marks = etl.get("watermarks", []) if isinstance(etl, dict) else []
        if self.ttl > 0:
            self._meta = (self._clock() + self.ttl, meta, marks)
        return meta, marks

    @staticmethod
    def resolve(meta: dict, name: str) -> dict:
        target = normalize_city(name)
        for c in meta.get("cities", []):
            if target in (normalize_city(c.get("name")), normalize_city(c.get("database"))):
                return c
        raise CityNotInWarehouseError(name)

    async def cities(self) -> list[dict]:
        meta, _ = await self._meta_and_watermarks()
        return meta.get("cities", [])

    async def indicators(self, name: str, from_: str | None = None, to: str | None = None) -> dict:
        meta, marks = await self._meta_and_watermarks()
        city = self.resolve(meta, name)
        key = (city["database"], from_, to)
        hit = self._cache.get(key)
        if self.ttl > 0 and hit and hit[0] > self._clock():
            return hit[1]

        params: dict[str, Any] = {"city": city["database"]}
        if from_:
            params["from"] = from_
        if to:
            params["to"] = to
        hist_params: dict[str, Any] = {"city": city["database"], "grain": "month"}
        if to:
            hist_params["to"] = to
        answers = await asyncio.gather(
            *(self.warehouse.get(f"/api/{d}", params) for d in DOMAINS),
            self.warehouse.get("/api/options", {"city": city["database"]}),
            *(self.warehouse.get(f"/api/tables/{d}", hist_params) for d in DOMAINS),
        )
        n = len(DOMAINS)
        pages = dict(zip(DOMAINS, answers[:n], strict=False))
        available = (answers[n] or {}).get("period")
        history = {d: (r or {}).get("rows") or [] for d, r in zip(DOMAINS, answers[n + 1 :], strict=False)}

        result = build_indicators(city, meta, marks, pages, from_, to, available, history)
        if self.ttl > 0:
            if len(self._cache) > 500:  # free-form periods: do not grow without bound
                self._cache.clear()
            self._cache[key] = (self._clock() + self.ttl, result)
        return result
