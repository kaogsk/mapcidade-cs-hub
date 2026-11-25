"""Contract vigency, derived at request time.

Two bugs from the original single-file hub shaped this module:

- "days left" and "status" were computed once when the file was generated and
  then aged silently (up to 36 days stale). Here they are derived from the
  start/end dates against ``today`` on every request.
- only the principal contract was considered, so a city whose principal
  contract had expired but whose addendum ran for another year showed up as
  "Expired". Effective vigency = the contract that ends *last*.
"""

from datetime import date

PLANS = ("Diamond", "Platinum", "Gold", "Silver", "Bronze")


def _d(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def effective_contract(contracts: list[dict]) -> dict | None:
    best, best_end = None, None
    for c in contracts:
        end = _d(c.get("end"))
        if end and (best_end is None or end > best_end):
            best, best_end = c, end
    return best


def contract_view(contracts: list[dict], today: date) -> dict | None:
    if not contracts:
        return None
    principal = next((c for c in contracts if c.get("principal")), contracts[0])
    effective = effective_contract(contracts) or principal
    start, end = _d(principal.get("start")), _d(effective.get("end"))
    raw_days = (end - today).days if end else None
    renewal = any(c.get("renewalInProgress") for c in contracts)
    if raw_days is None:
        status = "—"
    elif raw_days <= 0:
        status = "Expired"
    else:
        status = "Renewal" if renewal else "Active"
    return {
        "code": principal.get("code"),
        "plan": principal.get("plan"),
        "csm": principal.get("csm"),
        "negotiator": principal.get("negotiator"),
        "start": principal.get("start"),
        "end": effective.get("end"),
        "effectiveCode": effective.get("code"),
        "daysLeft": max(0, raw_days) if raw_days is not None else None,
        "status": status,
        "renewalInProgress": renewal,
        "totalDays": (end - start).days if start and end else None,
        "contracts": [
            {
                **c,
                "role": "principal" if c is principal else "addendum",
                "daysLeft": ((_d(c.get("end")) - today).days if _d(c.get("end")) else None),
            }
            for c in contracts
        ],
    }
