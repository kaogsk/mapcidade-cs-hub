"""Resident-analyst compliance: did each on-site analyst file the weekly report on time?

Rules (from the CS coordination's written policy):

- a week runs Monday to Friday;
- the deadline is the **7th business day after the week ends**, counting from
  the first business day after it, and holidays do not count. A holiday inside
  that window pushes the deadline (a Monday holiday turns "the Tuesday two
  weeks later" into "the Wednesday");
- filed by the 2nd business day after the week (Mon/Tue) = ideal; by the
  deadline = on time; after it = late;
- no report: overdue once the deadline has passed, pending before that;
- residents whose city has team coverage (another analyst files for them)
  count as covered when they have no report of their own.

Only weeks whose deadline already passed are *assessable* for KPIs; judging a
week that can still be filed would inflate the "late" count.
"""

from datetime import date, timedelta

from app.services.calendar import BusinessCalendar

DEADLINE_BUSINESS_DAYS = 7
IDEAL_BUSINESS_DAYS = 2
# Worst first when summarising a city across weeks.
RANK = {"none": 0, "overdue": 1, "late": 2, "pending": 3, "on-time": 4, "ideal": 5, "covered": 6}


def week_window(week_start: date, cal: BusinessCalendar) -> dict:
    week_end = week_start + timedelta(days=4)
    return {
        "weekStart": week_start,
        "weekEnd": week_end,
        "idealBy": cal.add_business_days(week_end, IDEAL_BUSINESS_DAYS),
        "deadline": cal.add_business_days(week_end, DEADLINE_BUSINESS_DAYS),
    }


def week_status(report: dict | None, window: dict, today: date, team_coverage: bool) -> str:
    resolved = date.fromisoformat(report["resolved"]) if report and report.get("resolved") else None
    if resolved:
        if resolved <= window["idealBy"]:
            return "ideal"
        return "on-time" if resolved <= window["deadline"] else "late"
    if team_coverage and not (report and report.get("ticketId")):
        return "covered"
    return "overdue" if today > window["deadline"] else "pending"


def recent_weeks(today: date, count: int) -> list[date]:
    """Mondays of the last ``count`` weeks that already ended (newest last)."""
    this_monday = today - timedelta(days=today.weekday())
    first = this_monday - timedelta(weeks=count) if today.weekday() < 5 else this_monday - timedelta(weeks=count - 1)
    return [first + timedelta(weeks=k) for k in range(count)]


def compliance(residents: list[dict], today: date, cal: BusinessCalendar, weeks: int = 6) -> dict:
    mondays = recent_weeks(today, weeks)
    windows = [week_window(m, cal) for m in mondays]
    assessable = [w["deadline"] < today for w in windows]
    people = []
    alerts = []
    by_city: dict[str, dict] = {}
    for r in residents:
        reports = {x["weekStart"]: x for x in r.get("reports", [])}
        cells = []
        for w, can_assess in zip(windows, assessable, strict=False):
            rep = reports.get(w["weekStart"].isoformat())
            st = week_status(rep, w, today, r.get("teamCoverage", False))
            late_days = cal.business_days_between(w["deadline"], today) if st == "overdue" else 0
            cells.append(
                {
                    "weekStart": w["weekStart"].isoformat(),
                    "deadline": w["deadline"].isoformat(),
                    "ticketId": rep.get("ticketId") if rep else None,
                    "resolved": rep.get("resolved") if rep else None,
                    "status": st,
                    "assessable": can_assess,
                    "businessDaysLate": late_days,
                }
            )
            if st == "overdue":
                alerts.append(
                    {
                        "resident": r["name"],
                        "city": r["city"],
                        "weekStart": w["weekStart"].isoformat(),
                        "deadline": w["deadline"].isoformat(),
                        "businessDaysLate": late_days,
                    }
                )
            if can_assess:
                cur = by_city.get(r["city"])
                if cur is None or RANK[st] < RANK[cur["status"]]:
                    by_city[r["city"]] = {"status": st}
        people.append(
            {
                "name": r["name"],
                "city": r["city"],
                "cityId": r["cityId"],
                "teamCoverage": r.get("teamCoverage", False),
                "weeks": cells,
            }
        )

    assessed = [c for p in people for c in p["weeks"] if c["assessable"]]
    on_time = [c for c in assessed if c["status"] in ("ideal", "on-time", "covered")]
    counts: dict[str, int] = {}
    for c in assessed:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    return {
        "weeks": [
            {"weekStart": w["weekStart"].isoformat(), "deadline": w["deadline"].isoformat(), "assessable": a}
            for w, a in zip(windows, assessable, strict=False)
        ],
        "residents": people,
        "alerts": alerts,
        "kpis": {
            "assessed": len(assessed),
            "onTime": len(on_time),
            "pct": round(len(on_time) / len(assessed) * 100) if assessed else None,
            "counts": counts,
        },
        "worstByCity": by_city,
    }
