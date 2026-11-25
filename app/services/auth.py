"""Opaque session tokens on top of the directory login.

``POST /api/auth/login`` validates against the directory and returns a random
token (never derived from the password); the front sends it as
``Authorization: Bearer <token>``. Sessions live in memory: a restart logs
everyone out, which is acceptable for an internal dashboard with 8 h sessions.
"""

import secrets
import time

from fastapi import HTTPException

from app.clients.directory import DirectoryAuthenticator


class SessionStore:
    def __init__(self, authenticator: DirectoryAuthenticator, ttl_seconds: int, clock=time.monotonic) -> None:
        self.authenticator = authenticator
        self.ttl = ttl_seconds
        self._clock = clock
        self._sessions: dict[str, tuple[str, float]] = {}

    def login(self, username: str, password: str) -> str:
        if not self.authenticator.authenticate(username, password):
            raise HTTPException(status_code=401, detail="Invalid username or password.")
        token = secrets.token_urlsafe(32)
        self._sessions[token] = (username, self._clock() + self.ttl)
        return token

    def logout(self, token: str) -> None:
        self._sessions.pop(token, None)

    def user_for(self, authorization: str | None) -> str:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Log in to continue.")
        token = authorization.split(" ", 1)[1].strip()
        session = self._sessions.get(token)
        if not session:
            raise HTTPException(status_code=401, detail="Invalid session. Log in again.")
        username, expires = session
        if expires <= self._clock():
            self._sessions.pop(token, None)
            raise HTTPException(status_code=401, detail="Session expired. Log in again.")
        return username
