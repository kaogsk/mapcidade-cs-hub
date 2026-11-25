"""Read/write access to the hub's own data (everything that is not a live source)."""

import sqlite3
from datetime import date, datetime

from app.db import one, rows


class Repository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # -- contracts spreadsheet ------------------------------------------------
    def cities(self) -> list[dict]:
        return rows(self.conn, "SELECT id, name, region FROM city ORDER BY name")

    def city(self, city_id: str) -> dict | None:
        return one(self.conn, "SELECT id, name, region FROM city WHERE id = ?", (city_id,))

    def contracts(self, city_id: str) -> list[dict]:
        return rows(
            self.conn,
            "SELECT code, plan, csm, negotiator, start_date AS start, end_date AS end, "
            "principal, renewal_in_progress AS renewalInProgress FROM contract "
            "WHERE city_id = ? ORDER BY principal DESC, code",
            (city_id,),
        )

    # -- historical load (what the hub knew before live sync) ------------------
    def snapshot_tickets(self, city_id: str) -> list[dict]:
        return rows(
            self.conn,
            "SELECT id, subject, status, priority, category, opened, sla, requester, team "
            "FROM snapshot_ticket WHERE city_id = ?",
            (city_id,),
        )

    def snapshot_reports(self, city_id: str) -> list[dict]:
        return rows(
            self.conn,
            "SELECT ticket_id AS ticketId, title, opened, kind, status " "FROM snapshot_report WHERE city_id = ?",
            (city_id,),
        )

    # -- CSM manual input and governance --------------------------------------
    def csm_input(self, city_id: str) -> dict:
        r = one(
            self.conn,
            "SELECT sentiment, utilization, satisfaction, updated_by AS updatedBy, "
            "updated_at AS updatedAt FROM csm_input WHERE city_id = ?",
            (city_id,),
        )
        return r or {"sentiment": None, "utilization": None, "satisfaction": None, "updatedBy": None, "updatedAt": None}

    def save_csm_input(self, city_id: str, values: dict, user: str) -> dict:
        current = self.csm_input(city_id)
        merged = {k: values.get(k, current.get(k)) for k in ("sentiment", "utilization", "satisfaction")}
        self.conn.execute(
            "INSERT INTO csm_input (city_id, sentiment, utilization, satisfaction, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(city_id) DO UPDATE SET sentiment = excluded.sentiment, "
            "utilization = excluded.utilization, satisfaction = excluded.satisfaction, "
            "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (
                city_id,
                merged["sentiment"],
                merged["utilization"],
                merged["satisfaction"],
                user,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self.conn.commit()
        return self.csm_input(city_id)

    def stakeholders(self, city_id: str) -> list[dict]:
        people = rows(
            self.conn, "SELECT id, name, role, profile FROM stakeholder WHERE city_id = ? ORDER BY id", (city_id,)
        )
        for p in people:
            p["interactions"] = [
                r["day"]
                for r in rows(
                    self.conn, "SELECT day FROM interaction WHERE stakeholder_id = ? ORDER BY day DESC", (p["id"],)
                )
            ]
        return people

    def meetings(self, city_id: str) -> list[dict]:
        return rows(
            self.conn,
            "SELECT kind, day, status, open_items AS openItems, title FROM meeting " "WHERE city_id = ? ORDER BY day",
            (city_id,),
        )

    def expansion(self, city_id: str) -> dict:
        return {
            "targets": rows(
                self.conn, "SELECT department, potential, status FROM expansion_target " "WHERE city_id = ?", (city_id,)
            ),
            "signals": [
                r["description"]
                for r in rows(self.conn, "SELECT description FROM interest_signal WHERE city_id = ?", (city_id,))
            ],
        }

    # -- technical hours --------------------------------------------------------
    def hours_balance(self, city_id: str) -> dict | None:
        return one(self.conn, "SELECT contracted, used FROM hours_balance WHERE city_id = ?", (city_id,))

    def hours_requests(self, city_id: str | None = None) -> list[dict]:
        sql = (
            "SELECT r.ticket_id AS ticketId, r.city_id AS cityId, c.name AS city, r.stage, "
            "r.department, r.opened, r.due FROM hours_request r JOIN city c ON c.id = r.city_id"
        )
        if city_id:
            return rows(self.conn, sql + " WHERE r.city_id = ? ORDER BY r.due", (city_id,))
        return rows(self.conn, sql + " ORDER BY r.due")

    # -- resident analysts --------------------------------------------------------
    def holidays(self) -> list[date]:
        return [date.fromisoformat(r["day"]) for r in rows(self.conn, "SELECT day FROM holiday")]

    def residents(self) -> list[dict]:
        people = rows(
            self.conn,
            "SELECT r.id, r.name, r.city_id AS cityId, c.name AS city, "
            "r.team_coverage AS teamCoverage FROM resident r "
            "JOIN city c ON c.id = r.city_id ORDER BY c.name, r.name",
        )
        for p in people:
            p["teamCoverage"] = bool(p["teamCoverage"])
            p["reports"] = rows(
                self.conn,
                "SELECT week_start AS weekStart, ticket_id AS ticketId, resolved "
                "FROM resident_report WHERE resident_id = ?",
                (p["id"],),
            )
        return people
