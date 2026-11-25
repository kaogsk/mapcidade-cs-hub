"""Account health: Health Score, its dimensions, the "temperature" indicators,
churn risk, overall risk class, next actions and a plain-language summary.

All pure functions over already-loaded data. In the original these lived in the
browser (inside a 7k-line HTML file); porting them to Python made them
testable and let the API return the same numbers to every client.

Design rule kept from the original: **an indicator without a measurement is
"not informed", never 0.** A zero that nobody measured drags the score down
and reads as a bad result. So the Health Score is a weighted mean over the
dimensions that *have* a value, with the weights renormalised, and the API
says how many of the four dimensions it came from. That last part matters:
in production, with only two dimensions measurable, most cities scored
96-100 and the number stopped discriminating. The ``partial`` flag is what
keeps that honest on screen.
"""

from dataclasses import dataclass, field
from datetime import date

DIMENSION_WEIGHTS = {"sentiment": 0.30, "engagement": 0.25, "utilization": 0.25, "meetings": 0.20}
EXPANSION_POTENTIAL = {"High": 20, "Medium": 12, "Low": 6}


def clamp(v: float) -> int:
    return max(0, min(100, round(v)))


def _date(s: str | None) -> date | None:
    """Accepts ISO (2026-10-02) and BR (02/10/2026) dates."""
    if not s:
        return None
    try:
        if "/" in s:
            d, m, y = s.split("/")
            return date(int(y), int(m), int(d))
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def recency_score(days: int | None) -> float | None:
    """Days since the last contact -> 0..1."""
    if days is None:
        return None
    return 1 if days <= 30 else 0.85 if days <= 60 else 0.7 if days <= 90 else 0.45 if days <= 180 else 0.2


def _weighted(parts: list[tuple[float | None, float]]) -> int | None:
    present = [(v, w) for v, w in parts if v is not None]
    if not present:
        return None
    total = sum(w for _, w in present)
    return clamp(sum(v * w for v, w in present) / total * 100)


@dataclass
class HealthInputs:
    today: date
    journeys: list[dict] = field(default_factory=list)
    stakeholders: list[dict] = field(default_factory=list)
    meetings: list[dict] = field(default_factory=list)
    reports: list[dict] = field(default_factory=list)
    csm: dict = field(default_factory=dict)
    expansion: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    contract: dict | None = None
    usage: dict | None = None  # {"access": {"last", "previous"}, "edits": {...}} (reference month pair)
    hours_pct: float | None = None


def _past(items: list[dict], key: str, today: date) -> list[dict]:
    return [x for x in items if (d := _date(x.get(key))) and d <= today]


def meetings_dimension(i: HealthInputs) -> int | None:
    """Governance discipline: of what was already due, how much happened.
    60% meetings/visits held, 40% reports approved."""
    enc = _past(i.meetings, "day", i.today)
    reps = _past(i.reports, "opened", i.today)
    a = len([m for m in enc if m["status"] == "Held"]) / len(enc) if enc else None
    b = len([r for r in reps if r["status"] == "Approved"]) / len(reps) if reps else None
    return _weighted([(a, 0.6), (b, 0.4)])


def engagement_dimension(i: HealthInputs) -> int | None:
    """How much the customer moves the process: 45% average progress of active
    journeys, 35% recency of contact with stakeholders, 20% past meetings that
    closed without open items."""
    active = [j for j in i.journeys if j.get("status") != "Planned"]
    a = sum(j.get("percent") or 0 for j in active) / len(active) / 100 if active else None
    notes = []
    for s in i.stakeholders:
        days = [(i.today - d).days for d in map(_date, s.get("interactions") or []) if d]
        if days:
            notes.append(recency_score(min(days)))
    b = sum(notes) / len(notes) if notes else None
    held = [m for m in _past(i.meetings, "day", i.today) if m["kind"] == "meeting"]
    c = len([m for m in held if not m.get("openItems")]) / len(held) if held else None
    return _weighted([(a, 0.45), (b, 0.35), (c, 0.20)])


def relationship(i: HealthInputs) -> int | None:
    """30% a decision maker or manager is mapped, 70% recency of the last contact.
    Deliberately ignores how many departments are covered: the stakeholder map
    is filled by hand, and penalising its gaps would measure the form, not the
    relationship."""
    if not i.stakeholders:
        return None
    a = 1 if any(s.get("profile") in ("Decision maker", "Manager") for s in i.stakeholders) else 0
    days = [(i.today - d).days for s in i.stakeholders for d in map(_date, s.get("interactions") or []) if d]
    b = recency_score(min(days)) if days else None
    return clamp(a * 100) if b is None else clamp((a * 0.3 + b * 0.7) * 100)


def expansion_potential(i: HealthInputs) -> int | None:
    """Unsold room: target departments by potential (High 20, Medium 12, Low 6;
    'Low adoption' counts half), capped at 80, plus 10 per interest signal
    (capped at 20). An empty matrix means "not mapped", not "no potential"."""
    targets = (i.expansion or {}).get("targets") or []
    if not targets:
        return None
    pts = 0.0
    for t in targets:
        base = EXPANSION_POTENTIAL.get(t.get("potential"), 0)
        if t.get("status") == "Not using":
            pts += base
        elif t.get("status") == "Low adoption":
            pts += base * 0.5
    signals = min(20, len((i.expansion or {}).get("signals") or []) * 10)
    return clamp(min(80, pts) + signals)


def churn_risk(i: HealthInputs, rel: int | None) -> int | None:
    """Probability-of-non-renewal points. Factors whose input is missing are
    dropped and their weight redistributed. Technical-hours consumption is
    *not* a factor: a low balance means heavy use of the service, not risk."""
    factors: list[tuple[float, float]] = []
    usage = i.usage or {}
    drops = []
    for dom in ("access", "edits"):
        d = usage.get(dom) or {}
        last, prev = d.get("last"), d.get("previous")
        if isinstance(last, int | float) and prev:
            # Month-over-month drop; -25% or worse saturates at 1. The browser
            # version divided the monthly variation by the *period total*
            # (whole history by default): with a year of data a 25% drop
            # counted as ~2%, and the score moved with the date filter.
            drops.append(0 if last >= prev else min(1, (prev - last) / prev / 0.25))
    if drops:
        factors.append((sum(drops) / len(drops), 30))
    c = i.contract
    if c and c.get("daysLeft") is not None:
        d = c["daysLeft"]
        near = 1 if d < 60 else 0.5 if d < 120 else 0.3 if d < 180 else 0.15 if d < 365 else 0
        factors.append((near * (0.6 if c.get("status") == "Renewal" else 1), 25))
    st = i.stats or {}
    if st:
        sla = st.get("slaPct", 100)
        factors.append(
            (min(1, (0.5 if st.get("critical") else 0) + (0.5 if sla < 80 else 0.25 if sla < 90 else 0)), 20)
        )
    # The browser version computed (100 - null) / 100 here, i.e. a city with no
    # stakeholders mapped got the *maximum* fragility. Unknown is skipped now.
    if rel is not None:
        factors.append(((100 - rel) / 100, 15))
    factors.append((1 if any(j.get("status") == "Late" for j in i.journeys) else 0, 10))
    total = sum(w for _, w in factors)
    return clamp(sum(v * w for v, w in factors) / total * 100) if total else None


def health(i: HealthInputs) -> dict:
    csm = i.csm or {}
    dims = {
        "sentiment": csm.get("sentiment"),
        "engagement": engagement_dimension(i),
        "utilization": csm.get("utilization"),
        "meetings": meetings_dimension(i),
    }
    measured = {k: v for k, v in dims.items() if isinstance(v, int | float)}
    if measured:
        weight = sum(DIMENSION_WEIGHTS[k] for k in measured)
        score = clamp(sum(v * DIMENSION_WEIGHTS[k] for k, v in measured.items()) / weight)
        status = "Healthy" if score >= 75 else "Attention" if score >= 60 else "Critical"
    else:
        score, status = None, "No data"
    rel = relationship(i)
    return {
        "score": score,
        "status": status,
        "dimensions": dims,
        "measured": sorted(measured),
        "partial": len(measured) < len(DIMENSION_WEIGHTS),
        "temperature": {
            "satisfaction": csm.get("satisfaction"),
            "churn": churn_risk(i, rel),
            "expansion": expansion_potential(i),
            "relationship": rel,
        },
    }


def risk_class(h: dict, i: HealthInputs) -> str:
    churn = h["temperature"]["churn"]
    s = 3 if churn is not None and churn > 40 else 2 if churn is not None and churn > 25 else 1
    st = i.stats or {}
    s += 2 if st.get("critical") else 0
    sla = st.get("slaPct", 100)
    s += 2 if sla < 80 else 1 if sla < 90 else 0
    days = (i.contract or {}).get("daysLeft")
    if days is not None:
        s += 3 if days < 60 else 1 if days < 180 else 0
    if i.hours_pct is not None:
        s += 2 if i.hours_pct > 90 else 1 if i.hours_pct > 80 else 0
    s += 2 if any(j.get("status") == "Late" for j in i.journeys) else 0
    return "Low" if s <= 3 else "Moderate" if s <= 6 else "High"


def next_actions(i: HealthInputs, hours_available: float | None = None) -> list[dict]:
    out: list[dict] = []
    days = (i.contract or {}).get("daysLeft")
    if days is not None and days < 180:
        out.append({"priority": "High", "area": "Contract", "text": f"Start contract renewal ({days} days left)"})
    if (i.stats or {}).get("critical"):
        out.append({"priority": "High", "area": "Support", "text": f"Resolve {i.stats['critical']} critical ticket(s)"})
    if i.hours_pct is not None and i.hours_pct >= 85:
        left = f" — only {hours_available:g}h left" if hours_available is not None else ""
        out.append({"priority": "High", "area": "Hours", "text": f"Renew technical-hours balance{left}"})
    for j in i.journeys:
        if j.get("status") == "Late":
            out.append(
                {
                    "priority": "High",
                    "area": "Journey",
                    "text": f"Resume late journey: {j.get('department')} — {j.get('sector')}",
                }
            )
    upcoming = sorted(
        (m for m in i.meetings if m["status"] == "Scheduled" and (_date(m["day"]) or i.today) >= i.today),
        key=lambda m: m["day"],
    )
    if upcoming:
        out.append(
            {
                "priority": "Medium",
                "area": "Meeting",
                "text": f"Prepare agenda: {upcoming[0]['title']} on {upcoming[0]['day']}",
            }
        )
    pending = [r for r in i.reports if r.get("status") == "Under review"]
    if pending:
        out.append({"priority": "Medium", "area": "Report", "text": f"{len(pending)} report(s) awaiting approval"})
    return out[:5]


def summary(name: str, h: dict, risk: str, i: HealthInputs) -> str:
    """Deterministic narrative. It only states what was measured: an indicator
    without a source never becomes a number in the text."""
    t = h["temperature"]
    if h["score"] is None:
        s = f"{name} has no Health Score yet: none of its dimensions has been measured. "
    else:
        extra = []
        if t["satisfaction"] is not None:
            extra.append(f"satisfaction at {t['satisfaction']}%")
        if t["churn"] is not None:
            extra.append(f"churn risk at {t['churn']}%")
        s = f"{name} has a Health Score of {h['score']}/100 ({h['status'].lower()})"
        s += (" with " + " and ".join(extra) if extra else "") + ". "
        if h["partial"]:
            s += f"Score based on {len(h['measured'])} of 4 dimensions. "
    s += f"Overall account risk: {risk}. "
    c = i.contract
    if c and c.get("daysLeft") is not None:
        if c["daysLeft"] < 60:
            s += f"Critical: contract ends in {c['daysLeft']} days. "
        else:
            s += f"Contract in force with {c['daysLeft']} days left ({c['status'].lower()}). "
    running = [j for j in i.journeys if j.get("status") in ("In progress", "Late")]
    late = [j for j in i.journeys if j.get("status") == "Late"]
    if running:
        s += f"{len(running)} journey(s) running" + (f", {len(late)} late" if late else "") + ". "
    if i.hours_pct is not None and i.hours_pct >= 85:
        s += f"Technical hours {i.hours_pct:.0f}% consumed. "
    if (i.stats or {}).get("critical"):
        s += f"{i.stats['critical']} critical ticket(s) open. "
    idle = [t["department"] for t in (i.expansion or {}).get("targets", []) if t.get("status") == "Not using"]
    if idle:
        s += f"Expansion room in {len(idle)} department(s): {', '.join(idle)}."
    return s.strip()
