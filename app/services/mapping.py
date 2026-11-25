"""Raw source records -> the hub's payload shapes.

Every rule here used to be applied by hand when someone pasted a fresh export
into the hub. Writing them down as pure functions is what made the sync
automatable (and testable).
"""

import re
import unicodedata
from datetime import date, datetime

from app.services.calendar import BusinessCalendar

SUPPORT_STATUS = {
    "resolved": "Closed",
    "closed": "Closed",
    "in progress": "In progress",
    "new": "Open",
    "open": "Open",
    "waiting": "Waiting",
}
URGENCY = {"high": "High", "medium": "Medium"}
CS_REPORT_STATUS = {"resolved": "Approved", "closed": "Approved"}
JOURNEY_STATUS = {"in progress": "In progress", "finished": "Done", "late": "Late"}

CS_TEAM = "Customer Success"
SUPPORT_TEAM_KEYWORDS = ("support", "development", "l1", "l2", "l3")
OPEN_STATUSES = ("Open", "In progress", "Waiting")

# City names as typed into other systems, beyond what normalization can fix.
CITY_ALIASES = {"willowbrooke": "willowbrook"}


def normalize_city(name: str | None) -> str:
    """'OAKWOOD - VL' -> 'oakwood'; "O'Connell Falls" -> 'oconnellfalls'.

    Accents, case, spaces, punctuation and a trailing ' - XX' region suffix
    are dropped, then known spelling aliases applied. Used for every
    cross-system match (journey list, usage warehouse, city ids).
    """
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s*-\s*[A-Za-z]{2}\s*$", "", s.strip())
    s = re.sub(r"[^0-9a-z]", "", s.lower())
    return CITY_ALIASES.get(s, s)


def parse_helpdesk_datetime(value: str) -> datetime:
    """The ticketing API sends 1 to 7 fractional digits; ``fromisoformat`` wants 3 or 6."""
    value = value.replace("Z", "+00:00")
    value = re.sub(r"\.(\d+)", lambda m: "." + m.group(1)[:6].ljust(6, "0"), value)
    return datetime.fromisoformat(value)


def to_br_date(value: str | None) -> str:
    if not value:
        return ""
    return parse_helpdesk_datetime(value).strftime("%d/%m/%Y")


def sla_status(status: str, created: str, today: date, calendar: BusinessCalendar) -> str:
    """Open tickets: > 5 business days = Overdue, 3-5 = Due soon. Closed ones are Ok."""
    if status not in ("Open", "In progress"):
        return "Ok"
    days = calendar.business_days_between(parse_helpdesk_datetime(created).date(), today)
    if days > 5:
        return "Overdue"
    if days >= 3:
        return "Due soon"
    return "Ok"


def map_support_ticket(raw: dict, today: date, calendar: BusinessCalendar) -> dict:
    status = SUPPORT_STATUS.get((raw.get("status") or "").strip().lower(), "Open")
    created = raw.get("createdDate")
    return {
        "id": str(raw["id"]),
        "subject": raw.get("subject") or "",
        "status": status,
        "priority": URGENCY.get((raw.get("urgency") or "").strip().lower(), "Low"),
        "category": raw.get("category"),
        # No date: "" rather than a crash. One incomplete ticket must not take
        # down the whole city payload.
        "opened": to_br_date(created),
        "sla": sla_status(status, created, today, calendar) if created else "Ok",
        "requester": raw.get("requester") or _first_client_org(raw.get("clients")),
        "team": raw.get("ownerTeam"),
    }


def _first_client_org(clients: list | None) -> str | None:
    first = (clients or [None])[0]
    if not isinstance(first, dict):
        return None
    org = first.get("organization")
    return org.get("businessName") if isinstance(org, dict) else org


def is_discarded(raw: dict) -> bool:
    team = (raw.get("ownerTeam") or "").lower()
    service = (raw.get("service") or "").lower()
    return (
        CS_TEAM.lower() in team
        or ("customer success" in service and "attendance" in service)
        or (raw.get("status") or "").lower() == "canceled"
    )


def is_support_ticket(raw: dict) -> bool:
    words = re.findall(r"[a-z0-9]+", (raw.get("ownerTeam") or "").lower())
    return any(k in words for k in SUPPORT_TEAM_KEYWORDS) and not is_discarded(raw)


def report_kind(title: str) -> str:
    s = title.lower()
    if "weekly" in s:
        return "Weekly"
    if "visit" in s:
        return "Visit"
    if "meeting" in s or "online" in s:
        return "Meeting"
    return "Service"


def map_cs_report(raw: dict) -> dict:
    subject = raw.get("subject") or ""
    return {
        "ticketId": str(raw["id"]),
        "title": subject,
        "opened": to_br_date(raw.get("createdDate")),
        "kind": report_kind(subject),
        "status": CS_REPORT_STATUS.get((raw.get("status") or "").strip().lower(), "Under review"),
    }


def quarter_label_to_hub(value: str | None) -> str:
    """'2026 - Q3 - Jul/Aug/Sep' or 'Q3 2025 - Jul/Aug/Sep' -> 'Q3/2026'."""
    v = value or ""
    q, y = re.search(r"Q([1-4])", v), re.search(r"(\d{4})", v)
    return f"Q{q.group(1)}/{y.group(1)}" if q and y else v


def journey_percent(value) -> int:
    """The list stores a fraction (0.7 = 70%) but some rows were typed as 70.
    Same rule as the hub: n <= 1 is a fraction. (Unchecked, 70 became 7000%.)"""
    if value is None or value == "":
        return 0
    n = float(value)
    return round(n * 100) if n <= 1 else round(n)


def split_list(text: str | None) -> list[str]:
    return [p.strip() for p in re.split(r"[\n;]+", text or "") if p.strip()]


def map_journey(raw: dict) -> dict:
    return {
        "id": f"list_{raw['ID']}",
        "department": raw.get("Department"),
        "sector": raw.get("Sector"),
        "owner": raw.get("Owner"),
        "quarter": quarter_label_to_hub(raw.get("Quarter")),
        "status": JOURNEY_STATUS.get((raw.get("Status") or "").strip().lower(), "Planned"),
        "percent": journey_percent(raw.get("PercentComplete")),
        "objective": raw.get("Objective"),
        "plan": raw.get("Plan"),
        "audience": split_list(raw.get("Audience")),
        "initialIndicator": raw.get("InitialIndicator"),
        "successIndicator": raw.get("SuccessIndicator"),
        "result": raw.get("Result"),
        "modified": to_br_date(raw.get("Modified")),
        "category": raw.get("Category"),
    }


def ticket_stats(tickets: list[dict]) -> dict:
    """KPIs computed from the *merged* list, so the dashboard never disagrees
    with the ticket table. Waiting counts as open: the customer still has an
    unresolved request."""
    open_ = [t for t in tickets if t["status"] in OPEN_STATUSES]
    closed = [t for t in tickets if t["status"] == "Closed"]
    critical = [t for t in open_ if t["priority"] == "High" and t["status"] != "Waiting"]
    sla = round(len([t for t in open_ if t["sla"] == "Ok"]) / len(open_) * 100) if open_ else 100
    return {"open": len(open_), "closed": len(closed), "critical": len(critical), "slaPct": sla}


def ticket_number(ticket_id: str) -> int:
    digits = re.sub(r"\D", "", str(ticket_id or ""))
    return int(digits) if digits else 0


def merge_by_id(current: list[dict], incoming: list[dict], key) -> list[dict]:
    """Keep everything already known, overwrite items that come back.

    The ticketing API only returns recent tickets, so replacing the list would
    silently delete history (in production one city got 34 tickets back from
    the API while 92 were known).
    """
    out: list[dict] = []
    index: dict = {}
    for item in current or []:
        k = key(item)
        if k not in index:
            index[k] = len(out)
            out.append(item)
    for item in incoming or []:
        k = key(item)
        if k in index:
            out[index[k]] = {**out[index[k]], **item}
        else:
            index[k] = len(out)
            out.append(item)
    return out
