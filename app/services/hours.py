"""Technical hours: contracted balance and on-demand requests.

A city buys a bank of technical hours; extra work ("on-demand hours") is
requested through a form that opens a ticket, then a department sends an
estimate. The panel answers two questions: how much of the bank is gone, and
which requests are at risk of missing the estimate deadline.

Only requests whose estimate has *not* been sent yet can be late; once the
estimate is out, the deadline no longer applies to the CS side.
"""

from datetime import date

ESTIMATE_NOT_SENT = {"Awaiting estimate", "Awaiting requester"}
DUE_SOON_DAYS = 5


def balance(row: dict | None) -> dict | None:
    if not row or not row.get("contracted"):
        return None
    contracted, used = float(row["contracted"]), float(row["used"] or 0)
    return {
        "contracted": contracted,
        "used": used,
        "available": max(0.0, contracted - used),
        "pct": round(used / contracted * 100, 1),
    }


def classify(request: dict, today: date) -> dict:
    due = date.fromisoformat(request["due"])
    diff = (due - today).days
    if request["stage"] not in ESTIMATE_NOT_SENT:
        status = "estimate-sent"
    elif diff < 0:
        status = "overdue"
    elif diff <= DUE_SOON_DAYS:
        status = "due-soon"
    else:
        status = "on-time"
    return {**request, "daysToDue": diff, "dueStatus": status}


def hours_panel(requests: list[dict], today: date) -> dict:
    items = [classify(r, today) for r in requests]
    counts = {k: 0 for k in ("overdue", "due-soon", "on-time", "estimate-sent")}
    for it in items:
        counts[it["dueStatus"]] += 1
    by_department: dict[str, int] = {}
    for it in items:
        by_department[it["department"]] = by_department.get(it["department"], 0) + 1
    order = {"overdue": 0, "due-soon": 1, "on-time": 2, "estimate-sent": 3}
    items.sort(key=lambda x: (order[x["dueStatus"]], x["daysToDue"]))
    return {"total": len(items), "counts": counts, "byDepartment": by_department, "requests": items}
