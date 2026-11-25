"""Optional "advisor" seam — disabled, and no provider ships in this repo.

Context: the original single-file hub had an "AI Advisor" tab that called a
hosted LLM API *directly from the browser*. It never worked in production:
there was no API key on the client (and there must never be one there) and
the provider's CORS policy blocked the call anyway. The tab was eventually
hidden behind a feature flag.

The right place for such a call is the backend, behind a server-side key.
This module is that seam and nothing more: ``AdvisorAdapter`` is the
interface an implementation would satisfy, and the only implementation here
is ``DisabledAdvisor``. ``POST /api/accounts/{id}/advisor`` therefore answers
503 with ``{"enabled": false}``. The account page's narrative summary is the
deterministic, rule-based ``health.summary`` — it is not AI-generated.
"""

import logging
from typing import Protocol

log = logging.getLogger("cshub.advisor")


class AdvisorDisabledError(Exception):
    pass


class AdvisorAdapter(Protocol):
    enabled: bool

    async def advise(self, context: dict) -> str: ...


class DisabledAdvisor:
    enabled = False

    def __init__(self, reason: str) -> None:
        self.reason = reason

    async def advise(self, context: dict) -> str:
        raise AdvisorDisabledError(self.reason)


def build_advisor(provider: str, api_key: str) -> AdvisorAdapter:
    if not provider:
        return DisabledAdvisor("No advisor provider configured (CSHUB_ADVISOR_PROVIDER is empty).")
    if not api_key:
        return DisabledAdvisor(f"Advisor provider '{provider}' configured without a server-side key.")
    log.warning("advisor provider %r requested, but no adapter is implemented in this build", provider)
    return DisabledAdvisor(f"No adapter for provider '{provider}' in this build.")
