"""SQLite storage. Stands in for every external system of the real deployment.

Each block of tables below is owned by exactly one adapter, so the rest of the
app never knows it is talking to SQLite:

- contracts spreadsheet        -> ``city``, ``contract``
- ticketing API (helpdesk)     -> ``helpdesk_*``      via ``SeedTicketingClient``
- historical ticket load       -> ``snapshot_*``      via ``SnapshotStore``
- collaboration list (journeys)-> ``journey_item``    via ``SeedJourneyListClient``
- usage warehouse (daily ETL)  -> ``usage_*``/``etl_*`` via ``SqliteUsageWarehouse``
- directory (LDAP-like)        -> ``directory_entry`` via ``FakeDirectory``
- CSM manual input & governance-> ``csm_input``, ``stakeholder``, ``meeting`` ...
"""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS city (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contract (
  code TEXT PRIMARY KEY, city_id TEXT NOT NULL REFERENCES city(id),
  plan TEXT, csm TEXT, negotiator TEXT, start_date TEXT, end_date TEXT,
  principal INTEGER NOT NULL DEFAULT 0, renewal_in_progress INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS helpdesk_company (
  id INTEGER PRIMARY KEY, business_name TEXT NOT NULL, person_type INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS helpdesk_ticket (
  id INTEGER PRIMARY KEY, subject TEXT, status TEXT, urgency TEXT, category TEXT,
  created TEXT, owner_team TEXT, service TEXT,
  company_id INTEGER, org_name TEXT, requester TEXT
);

CREATE TABLE IF NOT EXISTS snapshot_ticket (
  city_id TEXT, id TEXT, subject TEXT, status TEXT, priority TEXT, category TEXT,
  opened TEXT, sla TEXT, requester TEXT, team TEXT, PRIMARY KEY (city_id, id)
);
CREATE TABLE IF NOT EXISTS snapshot_report (
  city_id TEXT, ticket_id TEXT, title TEXT, opened TEXT, kind TEXT, status TEXT,
  PRIMARY KEY (city_id, ticket_id)
);

CREATE TABLE IF NOT EXISTS journey_item (
  id INTEGER PRIMARY KEY, city_value TEXT, department TEXT, quarter TEXT, owner TEXT,
  sector TEXT, objective TEXT, plan TEXT, audience TEXT, initial_indicator TEXT,
  success_indicator TEXT, layers TEXT, result TEXT, percent_complete TEXT,
  status TEXT, modified TEXT, category TEXT
);

CREATE TABLE IF NOT EXISTS usage_city (
  database TEXT PRIMARY KEY, name TEXT NOT NULL, has_etl INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS usage_event (
  database TEXT, domain TEXT, day TEXT, user TEXT, profile TEXT, item TEXT, n INTEGER
);
CREATE INDEX IF NOT EXISTS ix_usage ON usage_event(database, domain, day);
CREATE TABLE IF NOT EXISTS usage_catalog (
  database TEXT, domain TEXT, item TEXT
);
CREATE TABLE IF NOT EXISTS etl_run (
  started_at TEXT, finished_at TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS etl_watermark (
  database TEXT, routine TEXT, last_day TEXT
);

CREATE TABLE IF NOT EXISTS directory_entry (
  dn TEXT PRIMARY KEY, uid TEXT, cn TEXT, password_hash TEXT
);

CREATE TABLE IF NOT EXISTS csm_input (
  city_id TEXT PRIMARY KEY, sentiment INTEGER, utilization INTEGER, satisfaction INTEGER,
  updated_by TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS stakeholder (
  id INTEGER PRIMARY KEY, city_id TEXT, name TEXT, role TEXT, profile TEXT
);
CREATE TABLE IF NOT EXISTS interaction (
  stakeholder_id INTEGER, day TEXT
);
CREATE TABLE IF NOT EXISTS meeting (
  city_id TEXT, kind TEXT, day TEXT, status TEXT, open_items INTEGER, title TEXT
);
CREATE TABLE IF NOT EXISTS expansion_target (
  city_id TEXT, department TEXT, potential TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS interest_signal (
  city_id TEXT, description TEXT
);

CREATE TABLE IF NOT EXISTS hours_balance (
  city_id TEXT PRIMARY KEY, contracted REAL, used REAL
);
CREATE TABLE IF NOT EXISTS hours_request (
  ticket_id TEXT PRIMARY KEY, city_id TEXT, stage TEXT, department TEXT,
  opened TEXT, due TEXT
);

CREATE TABLE IF NOT EXISTS holiday (day TEXT PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS resident (
  id INTEGER PRIMARY KEY, name TEXT, city_id TEXT, team_coverage INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS resident_report (
  resident_id INTEGER, week_start TEXT, ticket_id TEXT, resolved TEXT,
  PRIMARY KEY (resident_id, week_start)
);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    """One connection per process; FastAPI runs our handlers on one event loop."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def rows(conn: sqlite3.Connection, sql: str, params: tuple | dict = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(conn: sqlite3.Connection, sql: str, params: tuple | dict = ()) -> dict | None:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None
