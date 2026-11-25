from datetime import date

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_seeded_account_and_session_lifecycle(tmp_path):
    settings = Settings(db_path=tmp_path / "demo.db", demo_today=date(2026, 10, 1))
    with TestClient(create_app(settings)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/accounts").status_code == 401
        session = client.post("/api/auth/login", json={"username": "ana.lima", "password": "demo-password"})
        assert session.status_code == 200
        headers = {"Authorization": f"Bearer {session.json()['token']}"}
        assert client.get("/api/accounts", headers=headers).status_code == 200
        assert client.get("/api/accounts/rivermeadow", headers=headers).status_code == 200
        assert client.post("/api/auth/logout", headers=headers).status_code == 200
        assert client.get("/api/accounts", headers=headers).status_code == 401
