"""FastAPI entrypoint.

    uvicorn app.main:app --port 4701

``create_app`` takes every collaborator as an optional argument, so tests run
the real routes against in-memory SQLite and fake/broken sources.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import db
from app.clients.directory import DirectoryAuthenticator, FakeDirectory
from app.clients.journeys import JourneySource, SeedJourneyListClient
from app.clients.ticketing import SeedTicketingClient, TicketingClient, TicketingUnavailableError
from app.clients.usage_warehouse import SqliteUsageWarehouse, UsageWarehouseUnavailableError
from app.clock import Clock
from app.config import ROOT, Settings
from app.repository import Repository
from app.services.accounts import AccountService, UnknownCityError
from app.services.advisor import AdvisorAdapter, AdvisorDisabledError, build_advisor
from app.services.auth import SessionStore
from app.services.calendar import BusinessCalendar
from app.services.hours import hours_panel
from app.services.indicators import CityNotInWarehouseError, IndicatorsService
from app.services.lineage import lineage
from app.services.residents import compliance
from app.services.sync import CityNotFoundError, SyncService

log = logging.getLogger("cshub")
STATIC_DIR = ROOT / "static"


@dataclass
class Deps:
    settings: Settings
    repo: Repository
    clock: Clock
    calendar: BusinessCalendar
    ticketing: TicketingClient
    journeys: JourneySource
    sync: SyncService
    indicators: IndicatorsService
    accounts: AccountService
    sessions: SessionStore
    advisor: AdvisorAdapter


def build_deps(settings: Settings, conn=None, ticketing=None, journey_client=None, warehouse=None) -> Deps:
    conn = conn or db.connect(settings.db_path)
    if db.one(conn, "SELECT count(*) AS n FROM city")["n"] == 0:
        from app.seed import seed

        log.info("empty database: seeding synthetic data")
        seed(conn, settings.demo_today or Clock().today())
    repo = Repository(conn)
    clock = Clock(settings.demo_today)
    calendar = BusinessCalendar(repo.holidays())
    ticketing = ticketing or SeedTicketingClient(conn, settings.ticketing_recent_limit)
    journeys = JourneySource(journey_client or SeedJourneyListClient(conn))
    sync = SyncService(ticketing, journeys, clock, calendar, settings.cache_ttl_seconds)
    indicators = IndicatorsService(warehouse or SqliteUsageWarehouse(conn), settings.usage_cache_ttl_seconds)
    directory = FakeDirectory(conn)
    authenticator = DirectoryAuthenticator(
        directory,
        f"uid={settings.directory_service_user},ou=services,dc=mapcidade,dc=example",
        settings.directory_service_password,
        settings.directory_identifier_attribute,
    )
    return Deps(
        settings=settings,
        repo=repo,
        clock=clock,
        calendar=calendar,
        ticketing=ticketing,
        journeys=journeys,
        sync=sync,
        indicators=indicators,
        accounts=AccountService(repo, sync, indicators, clock),
        sessions=SessionStore(authenticator, settings.session_ttl_seconds),
        advisor=build_advisor(settings.advisor_provider, settings.advisor_api_key),
    )


class LoginRequest(BaseModel):
    username: str
    password: str


class CsmInput(BaseModel):
    sentiment: int | None = Field(default=None, ge=0, le=100)
    utilization: int | None = Field(default=None, ge=0, le=100)
    satisfaction: int | None = Field(default=None, ge=0, le=100)


def create_app(settings: Settings | None = None, deps: Deps | None = None) -> FastAPI:
    settings = settings or (deps.settings if deps else Settings())
    docs = settings.expose_docs
    app = FastAPI(
        title="MapCidade CS Hub",
        version="1.0.0",
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["*"],
    )
    d = deps or build_deps(settings)
    app.state.deps = d

    def user(request: Request) -> str:
        return d.sessions.user_for(request.headers.get("authorization"))

    @app.post("/api/auth/login")
    async def login(body: LoginRequest):
        # Password hashing / directory binds are blocking: keep them off the loop.
        token = await asyncio.to_thread(d.sessions.login, body.username, body.password)
        return {"token": token, "username": body.username}

    @app.post("/api/auth/logout")
    async def logout(request: Request, _user: str = Depends(user)):
        d.sessions.logout(request.headers["authorization"].split(" ", 1)[1].strip())
        return {"ok": True}

    @app.get("/api/me")
    async def me(u: str = Depends(user)):
        return {"username": u, "today": d.clock.today().isoformat(), "advisorEnabled": d.advisor.enabled}

    @app.get("/api/accounts")
    async def accounts(_user: str = Depends(user)):
        return await d.accounts.overview()

    @app.get("/api/accounts/{city_id}")
    async def account(city_id: str, _user: str = Depends(user)):
        try:
            return await d.accounts.build(city_id)
        except UnknownCityError:
            raise HTTPException(status_code=404, detail=f"Unknown city '{city_id}'.") from None

    @app.put("/api/accounts/{city_id}/csm-input")
    async def save_csm(city_id: str, body: CsmInput, u: str = Depends(user)):
        if not d.repo.city(city_id):
            raise HTTPException(status_code=404, detail=f"Unknown city '{city_id}'.")
        return d.repo.save_csm_input(city_id, body.model_dump(exclude_unset=True), u)

    @app.post("/api/accounts/{city_id}/advisor")
    async def advisor(city_id: str, _user: str = Depends(user)):
        try:
            return {"enabled": True, "text": await d.advisor.advise({"city": city_id})}
        except AdvisorDisabledError as err:
            return JSONResponse(status_code=503, content={"enabled": False, "detail": str(err)})

    @app.get("/api/sync/{city_name}")
    async def sync(city_name: str, _user: str = Depends(user)):
        """Raw live payload for one city (the backend's original contract)."""
        try:
            return (await d.sync.sync(city_name)).as_dict()
        except CityNotFoundError:
            raise HTTPException(status_code=404, detail=f"City '{city_name}' not found in the ticketing API.") from None
        except TicketingUnavailableError as err:
            raise HTTPException(status_code=502, detail=f"Ticketing API unavailable: {err}") from None

    @app.get("/api/indicators/{city_name}")
    async def indicators(
        city_name: str,
        from_: date | None = Query(default=None, alias="from"),
        to: date | None = Query(default=None),
        _user: str = Depends(user),
    ):
        if from_ and to and from_ > to:
            raise HTTPException(status_code=400, detail="Invalid period: 'from' is after 'to'.")
        try:
            return await d.indicators.indicators(
                city_name, from_.isoformat() if from_ else None, to.isoformat() if to else None
            )
        except CityNotInWarehouseError:
            raise HTTPException(status_code=404, detail=f"City '{city_name}' is not in the usage warehouse.") from None
        except UsageWarehouseUnavailableError as err:
            raise HTTPException(status_code=502, detail=f"Usage warehouse unavailable: {err}") from None

    @app.get("/api/hours")
    async def hours(city: str | None = None, _user: str = Depends(user)):
        return hours_panel(d.repo.hours_requests(city), d.clock.today())

    @app.get("/api/residents")
    async def residents(_user: str = Depends(user)):
        return compliance(d.repo.residents(), d.clock.today(), d.calendar)

    @app.get("/api/lineage")
    async def data_lineage(_user: str = Depends(user)):
        return await lineage(d.repo, d.ticketing, d.journeys, d.indicators)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    if Path(STATIC_DIR).is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def __getattr__(name: str):
    # ``uvicorn app.main:app`` builds the app lazily, so importing this module
    # in tests never opens the default database file.
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
