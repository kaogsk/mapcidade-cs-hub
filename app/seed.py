"""Synthetic data for every source. Deterministic (fixed RNG seed), with all
dates relative to an anchor day so the demo never goes stale.

    python -m app.seed            # (re)create data/cshub.db anchored on today
    python -m app.seed 2026-11-25 # anchored on a given day

Every name below is fictional. Each city exercises at least one edge case of
the real system (see the comments on ``CITIES``).
"""

import random
import sqlite3
import sys
from datetime import date, datetime, timedelta

from app import db
from app.clients.directory import hash_password
from app.clock import Clock
from app.config import Settings
from app.services.calendar import BusinessCalendar
from app.services.residents import recent_weeks, week_window

DEMO_PASSWORD = "demo-password"
SERVICE_DN = "uid=svc-cshub,ou=services,dc=mapcidade,dc=example"

CITIES = [
    # id, name, region, plan, csm
    (
        "rivermeadow",
        "Rivermeadow",
        "NR",
        "Diamond",
        "Ana Lima",
    ),  # > recent-limit tickets; two list spellings; lagging ETL routine
    ("lakeside", "Lakeside", "CT", "Gold", "Bruno Reis"),  # contract ends in 45 days, usage dropping, hours almost gone
    ("oakwood", "Oakwood", "VL", "Platinum", "Carla Nunes"),  # list value with region suffix; a late journey
    ("pinecrest", "Pinecrest", "NR", "Silver", "Ana Lima"),  # principal expired, addendum still valid
    (
        "stonebridge",
        "Stonebridge",
        "CT",
        "Bronze",
        "Bruno Reis",
    ),  # listed as "STONE BRIDGE"; not in the usage warehouse
    (
        "willowbrook",
        "Willowbrook",
        "VL",
        "Gold",
        "Carla Nunes",
    ),  # listed as "WILLOWBROOKE" (alias); warehouse history only
    ("oconnellfalls", "O'Connell Falls", "NR", "Silver", "Ana Lima"),  # apostrophe; missing from the journey list
    ("harborview", "Harborview", "CT", "Bronze", "Bruno Reis"),  # no company in the ticketing API; contract expired
]
NEGOTIATORS = ["Diego Moura", "Elisa Faria"]
DEPARTMENTS = ["Planning", "Finance", "Health", "Civil Defense", "Public Works", "Environment"]
PROFILES = ["Planning", "Finance", "Field team", "Admin", "Public"]
THEMES = ["Parcels", "Buildings", "Street lights", "Trees", "Permits", "Drainage"]
CERTIFICATES = ["Zoning certificate", "Address certificate", "Land-use certificate", "Setback certificate"]
USERS = [
    "j.carter",
    "m.ellis",
    "r.hughes",
    "s.patel",
    "t.nguyen",
    "k.olsen",
    "l.moreau",
    "p.grant",
    "d.wolfe",
    "e.vance",
    "h.lowe",
    "n.quinn",
]
SUBJECTS = [
    "Layer does not load",
    "Search by address returns nothing",
    "Permit form validation error",
    "Slow map rendering",
    "Mobile sync failed",
    "Certificate PDF layout broken",
    "Export to spreadsheet fails",
    "New user access",
    "Theme popup is empty",
    "Printing scale is wrong",
]


def _iso(d: date) -> str:
    return d.isoformat()


def _helpdesk_ts(rng: random.Random, d: date) -> str:
    """Naive local timestamp with 3 or 7 fractional digits (the API mixes both)."""
    t = datetime.combine(d, datetime.min.time()) + timedelta(
        hours=rng.randint(8, 17), minutes=rng.randint(0, 59), seconds=rng.randint(0, 59)
    )
    frac = f"{rng.randint(0, 9_999_999):07d}" if rng.random() < 0.5 else f"{rng.randint(0, 999):03d}"
    return t.strftime("%Y-%m-%dT%H:%M:%S") + "." + frac


def _br(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def seed(conn: sqlite3.Connection, today: date) -> None:
    rng = random.Random(42)
    t = today
    ex = conn.execute

    # -- cities and contracts -------------------------------------------------------
    ends = {
        "rivermeadow": 420,
        "lakeside": 45,
        "oakwood": 150,
        "pinecrest": -200,
        "stonebridge": 700,
        "willowbrook": 95,
        "oconnellfalls": 250,
        "harborview": -10,
    }
    for i, (cid, name, region, plan, csm) in enumerate(CITIES):
        ex("INSERT INTO city VALUES (?, ?, ?)", (cid, name, region))
        ex(
            "INSERT INTO contract VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (
                f"CT-{cid.upper()}-01",
                cid,
                plan,
                csm,
                NEGOTIATORS[i % 2],
                _iso(t - timedelta(days=365 * 3 + i * 40)),
                _iso(t + timedelta(days=ends[cid])),
                1 if cid == "lakeside" else 0,
            ),
        )
    ex(
        "INSERT INTO contract VALUES (?, 'pinecrest', NULL, 'Ana Lima', NULL, ?, ?, 0, 0)",
        ("ADD-PINECREST-01", _iso(t - timedelta(days=200)), _iso(t + timedelta(days=300))),
    )

    # -- ticketing API -------------------------------------------------------------
    companies = {cid: 1001 + i for i, (cid, *_rest) in enumerate(CITIES) if cid != "harborview"}
    for cid, name, *_ in CITIES:
        if cid in companies:
            ex("INSERT INTO helpdesk_company VALUES (?, ?, 2)", (companies[cid], f"City Hall of {name}"))
    ex("INSERT INTO helpdesk_company VALUES (2001, 'Rivermeadow Water Utility', 2)")
    ex("INSERT INTO helpdesk_company VALUES (2002, 'Lakeside Transit Co', 1)")

    next_id = [140000]
    snapshot_day = t - timedelta(days=60)

    def add_ticket(
        cid: str,
        name: str,
        created: date,
        team: str,
        subject: str,
        status: str,
        urgency: str,
        service: str = "Support",
        link: str = "both",
    ) -> int:
        next_id[0] += rng.randint(3, 40)
        tid = next_id[0]
        org = f"City Hall of {name}"
        company_id = companies[cid] if link in ("both", "id") else None
        org_name = org if link in ("both", "org") else f"{org} - Planning Dept"
        ex(
            "INSERT INTO helpdesk_ticket VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tid,
                subject,
                status,
                urgency,
                rng.choice(["Map", "Forms", "Mobile", "Certificates", "Access"]),
                _helpdesk_ts(rng, created),
                team,
                service,
                company_id,
                org_name,
                rng.choice(["Planning office", "Finance desk", "IT department", None]),
            ),
        )
        return tid

    volume = {
        "rivermeadow": 80,
        "lakeside": 45,
        "oakwood": 26,
        "pinecrest": 12,
        "stonebridge": 10,
        "willowbrook": 14,
        "oconnellfalls": 8,
    }
    events: list[tuple] = []
    for cid, name, *_ in CITIES:
        if cid not in companies:
            continue
        for _ in range(volume[cid]):
            created = t - timedelta(days=rng.randint(0, 240))
            events.append((created, cid, name, "support"))
        for k in range(0, 200, 14):  # CS team: a weekly report every other week, plus visits/meetings
            events.append((t - timedelta(days=k + rng.randint(0, 3)), cid, name, "cs"))
    events.sort()
    for created, cid, name, kind in events:
        age = (t - created).days
        link = rng.choices(["both", "id", "org"], [70, 15, 15])[0]
        if kind == "cs":
            subject = rng.choice(
                [
                    f"Weekly report - week of {_br(created - timedelta(days=created.weekday()))}",
                    "Site visit - Planning department",
                    "Online meeting - quarterly review",
                    "Training session - field team",
                ]
            )
            status = "Resolved" if age > 10 or rng.random() < 0.5 else "In progress"
            add_ticket(cid, name, created, "Customer Success", subject, status, "Low", "Customer success", link)
            continue
        r = rng.random()
        if age > 30:
            status = "Closed" if r < 0.55 else "Resolved" if r < 0.95 else "Canceled"
        else:
            status = rng.choice(["New", "In progress", "Waiting", "Resolved"])
        if cid == "lakeside" and age < 25 and r < 0.6:
            status = "In progress"
        urgency = rng.choices(["High", "Medium", "Low"], [25 if cid == "lakeside" else 10, 40, 50])[0]
        team = rng.choice(["Support L1", "Support L2", "Development"])
        service = "Customer success - attendance" if r > 0.97 else "Support"
        tid = add_ticket(cid, name, created, team, rng.choice(SUBJECTS), status, urgency, service, link)
        # The historical load: tickets known on snapshot day, frozen as they were then.
        if created <= snapshot_day and status != "Canceled" and service == "Support":
            frozen = status if age > 90 else rng.choice(["Open", "In progress"])
            ex(
                "INSERT INTO snapshot_ticket VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cid,
                    str(tid),
                    rng.choice(SUBJECTS),
                    {"Resolved": "Closed", "New": "Open"}.get(frozen, frozen),
                    urgency,
                    "Map",
                    _br(created),
                    "Ok",
                    None,
                    team,
                ),
            )
    # Legacy reports: same CS tickets, loaded before reports had a "kind" field.
    for cid, *_ in CITIES:
        for k in range(3):
            d = snapshot_day - timedelta(days=30 * k + 7)
            ex(
                "INSERT INTO snapshot_report VALUES (?, ?, ?, ?, NULL, 'Approved')",
                (cid, f"9{k}{companies.get(cid, 0)}", f"Weekly report - week of {_br(d)}", _br(d)),
            )

    # -- customer-journey list -------------------------------------------------------
    list_values = {
        "rivermeadow": ["RIVERMEADOW - NR", "Rivermeadow"],
        "lakeside": ["Lakeside"],
        "oakwood": ["OAKWOOD - VL"],
        "pinecrest": ["PINECREST"],
        "stonebridge": ["STONE BRIDGE"],
        "willowbrook": ["WILLOWBROOKE"],
        "riverdale": ["Riverdale"],
    }
    jid = 0
    for cid, values in list_values.items():
        for k in range(rng.randint(4, 9)):
            jid += 1
            q = rng.randint(1, 4)
            year = t.year - (1 if k % 3 == 0 else 0)
            quarter = f"Q{q} {year} - Jan/Feb/Mar" if year < t.year else f"{year} - Q{q} - Apr/May/Jun"
            status = rng.choices(
                ["In progress", "Finished", "Planned", "Late"], [40, 35, 15, 5 if cid != "oakwood" else 0]
            )[0]
            if cid == "oakwood" and k == 0:
                status = "Late"
            pct = {"Finished": 1, "Planned": 0}.get(status, round(rng.uniform(0.2, 0.9), 2))
            if k == 2 and status == "In progress":
                pct = round(pct * 100)  # typed as an integer percentage
            dept = rng.choice(DEPARTMENTS)
            ex(
                "INSERT INTO journey_item VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    jid,
                    values[k % len(values)],
                    dept,
                    quarter,
                    rng.choice(["Ana Lima", "Bruno Reis", "Carla Nunes"]),
                    f"{dept} office",
                    f"Adopt the map platform in the {dept.lower()} routine",
                    "Periodic visits until the team works without basic questions",
                    "Office staff;Field inspectors",
                    "Staff relies on another department for lookups",
                    "Staff using the platform daily",
                    rng.choice(THEMES),
                    None,
                    str(pct),
                    status,
                    _helpdesk_ts(rng, t - timedelta(days=rng.randint(1, 120))),
                    rng.choice(["Training", "Rollout"]),
                ),
            )

    # -- usage warehouse (daily ETL) ---------------------------------------------------
    warehouse = {
        "rivermeadow": ("rivermeadow", "Rivermeadow", 1, 1.0, 0.0),
        "lakeside": ("lakeside", "Lakeside", 1, 0.8, -0.35),
        "oakwood": ("oakwood", "Oakwood", 1, 0.6, 0.25),
        "pinecrest": ("pinecrest", "Pinecrest", 1, 0.3, 0.0),
        "willowbrook": ("willowbrook", "Willowbrook", 0, 0.5, 0.0),
        "oconnellfalls": ("oconnell_falls", "O'Connell Falls", 1, 0.25, 0.0),
        "harborview": ("harborview", "Harborview", 1, 0.2, -0.1),
    }
    rows_ = []
    for cid, (dbname, name, has_etl, scale, trend) in warehouse.items():
        ex("INSERT INTO usage_city VALUES (?, ?, ?)", (dbname, name, has_etl))
        last = t - timedelta(days=1) if has_etl else t - timedelta(days=130)
        users = USERS[: 5 + int(scale * 7)]
        start = t - timedelta(days=480)
        # The trend kicks in on the reference month (last complete month), so
        # the month-over-month cards show it.
        recent_start = (date(t.year, t.month, 1) - timedelta(days=1)).replace(day=1)
        day = start
        while day <= last:
            weekend = day.weekday() >= 5
            f = scale * (0.25 if weekend else 1.0) * (1 + (trend if day >= recent_start else 0))
            for dom, per_day, items in (
                ("access", 5, [None]),
                ("edits", 3, THEMES),
                ("mobile", 2, THEMES[:3]),
                ("certificates", 2, CERTIFICATES[:3]),
            ):
                for _ in range(per_day):
                    if rng.random() > f:
                        continue
                    user = rng.choice(users)
                    if dom == "certificates" and rng.random() < 0.4:
                        user = ""  # public portal
                    rows_.append(
                        (dbname, dom, _iso(day), user, rng.choice(PROFILES), rng.choice(items), rng.randint(1, 6))
                    )
            day += timedelta(days=1)
        for th in THEMES[:5]:
            ex("INSERT INTO usage_catalog VALUES (?, 'mobile', ?)", (dbname, th))
        for ce in CERTIFICATES:
            ex("INSERT INTO usage_catalog VALUES (?, 'certificates', ?)", (dbname, ce))
        if has_etl:
            for routine in ("access", "edits", "mobile", "certificates"):
                lag = 2 if (cid == "rivermeadow" and routine == "edits") else 0
                ex("INSERT INTO etl_watermark VALUES (?, ?, ?)", (dbname, routine, _iso(last - timedelta(days=lag))))
    conn.executemany("INSERT INTO usage_event VALUES (?, ?, ?, ?, ?, ?, ?)", rows_)
    ex("INSERT INTO etl_run VALUES (?, ?, 'ok')", (f"{_iso(t)}T09:30:02Z", f"{_iso(t)}T09:32:05Z"))

    # -- directory --------------------------------------------------------------------
    ex(
        "INSERT INTO directory_entry VALUES (?, 'svc-cshub', 'CS Hub service', ?)",
        (SERVICE_DN, hash_password(Settings().directory_service_password)),
    )
    for uid, cn in (("ana.lima", "Ana Lima"), ("bruno.reis", "Bruno Reis"), ("admin.ops", "Ops Admin")):
        ex(
            "INSERT INTO directory_entry VALUES (?, ?, ?, ?)",
            (f"uid={uid},ou=people,dc=mapcidade,dc=example", uid, cn, hash_password(DEMO_PASSWORD)),
        )

    # -- CSM input and governance -----------------------------------------------------------
    ex("INSERT INTO csm_input VALUES ('rivermeadow', 80, 72, 85, 'ana.lima', ?)", (f"{_iso(t)}T10:00:00",))
    ex("INSERT INTO csm_input VALUES ('oakwood', 70, NULL, NULL, 'carla.nunes', ?)", (f"{_iso(t)}T10:00:00",))
    sid = 0
    for cid, *_ in CITIES[:6]:
        for k, (role, profile) in enumerate(
            (("Planning secretary", "Decision maker"), ("GIS coordinator", "Manager"), ("Field lead", "Operational"))
        ):
            if cid == "lakeside" and profile != "Operational":
                continue  # nobody senior mapped
            sid += 1
            ex("INSERT INTO stakeholder VALUES (?, ?, ?, ?, ?)", (sid, cid, f"Contact {sid}", role, profile))
            gap = {"rivermeadow": 12, "oakwood": 40, "pinecrest": 100, "lakeside": 150}.get(cid, 70)
            for n in range(2):
                ex("INSERT INTO interaction VALUES (?, ?)", (sid, _iso(t - timedelta(days=gap + 30 * n + k))))
        for k in range(5):
            d = t + timedelta(days=-90 + 25 * k)
            past = d <= t
            status = (
                ("Held" if rng.random() < (0.9 if cid != "lakeside" else 0.4) else "Canceled") if past else "Scheduled"
            )
            ex(
                "INSERT INTO meeting VALUES (?, ?, ?, ?, ?, ?)",
                (
                    cid,
                    "visit" if k % 2 else "meeting",
                    _iso(d),
                    status,
                    rng.choice([0, 0, 1, 2]) if past else 0,
                    ["Quarterly review", "Rollout checkpoint", "Field visit", "Training follow-up", "Roadmap meeting"][
                        k
                    ],
                ),
            )
    for cid, targets in {
        "rivermeadow": [("Health", "High", "Not using"), ("Environment", "Medium", "Low adoption")],
        "oakwood": [("Finance", "High", "Not using"), ("Civil Defense", "Low", "Using")],
        "pinecrest": [("Public Works", "Medium", "Not using")],
    }.items():
        for dept, pot, st in targets:
            ex("INSERT INTO expansion_target VALUES (?, ?, ?, ?)", (cid, dept, pot, st))
    ex("INSERT INTO interest_signal VALUES ('rivermeadow', 'Health secretary asked for a demo')")

    # -- technical hours ---------------------------------------------------------------------
    for cid, contracted, used in (
        ("rivermeadow", 400, 260),
        ("lakeside", 200, 184),
        ("oakwood", 300, 250),
        ("pinecrest", 120, 30),
        ("willowbrook", 150, 140),
    ):
        ex("INSERT INTO hours_balance VALUES (?, ?, ?)", (cid, contracted, used))
    stages = ["Awaiting estimate", "Awaiting estimate", "Awaiting requester", "Estimate sent", "In execution"]
    for k in range(16):
        cid = rng.choice(["rivermeadow", "lakeside", "oakwood", "pinecrest", "willowbrook", "stonebridge"])
        opened = t - timedelta(days=rng.randint(2, 40))
        due = opened + timedelta(days=14)
        ex(
            "INSERT INTO hours_request VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(150000 + k * 17),
                cid,
                rng.choice(stages),
                rng.choice(["Support", "Development", "Data team"]),
                _iso(opened),
                _iso(due),
            ),
        )

    # -- resident analysts ------------------------------------------------------------------------
    mondays = recent_weeks(t, 6)
    civic_day = mondays[2] + timedelta(days=7)  # a Monday holiday inside a deadline window
    for d, label in (
        (civic_day, "Civic Day"),
        (date(t.year, 1, 1), "New Year"),
        (date(t.year, 12, 25), "Winter holiday"),
    ):
        ex("INSERT OR IGNORE INTO holiday VALUES (?, ?)", (_iso(d), label))
    cal = BusinessCalendar([civic_day, date(t.year, 1, 1), date(t.year, 12, 25)])
    patterns = {
        1: ("Ivy Turner", "rivermeadow", 0, ["ideal", "ideal", "on-time", "late", "ideal", "open"]),
        2: ("Noah Brooks", "oakwood", 0, ["ideal", "none", "ideal", "on-time", "none", "none"]),
        3: ("Lena Ortiz", "lakeside", 1, ["none", "ideal", "none", "none", "on-time", "none"]),
    }
    for rid, (name, cid, coverage, plan_) in patterns.items():
        ex("INSERT INTO resident VALUES (?, ?, ?, ?)", (rid, name, cid, coverage))
        for monday, what in zip(mondays, plan_, strict=False):
            if what == "none":
                continue
            w = week_window(monday, cal)
            resolved = {
                "ideal": w["weekEnd"],
                "on-time": w["deadline"],
                "late": cal.add_business_days(w["deadline"], 2),
                "open": None,
            }[what]
            if resolved and resolved > t:
                resolved = None
            ex(
                "INSERT INTO resident_report VALUES (?, ?, ?, ?)",
                (
                    rid,
                    _iso(monday),
                    str(160000 + rid * 100 + mondays.index(monday)),
                    _iso(resolved) if resolved else None,
                ),
            )
    conn.commit()


def main() -> None:
    settings = Settings()
    anchor = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else Clock(settings.demo_today).today()
    path = settings.db_path
    if path.exists():
        path.unlink()
    conn = db.connect(path)
    seed(conn, anchor)
    n = db.one(conn, "SELECT count(*) AS n FROM usage_event")["n"]
    print(f"seeded {path} anchored on {anchor} ({n} usage events)")


if __name__ == "__main__":
    main()
