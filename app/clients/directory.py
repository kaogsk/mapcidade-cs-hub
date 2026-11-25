"""Directory login (LDAP-style), with an in-repo fake directory.

The real hub authenticates against the company directory with the classic
two-bind flow, kept here:

1. bind as a *service account* and search the user's DN by an identifier
   attribute (``uid`` here; the real one needed ``sAMAccountName`` because
   ``cn`` turned out to be the full display name, not the login);
2. bind again *as that DN* with the password the user typed. The service
   account never sees or checks anyone's password.

The search filter is built from user input, so it is escaped (RFC 4515).
Before that fix, ``adm*`` found the first account starting with "adm".
``FakeDirectory`` implements real filter semantics (wildcards included) so
the test for that fix is meaningful, not a tautology.
"""

import hashlib
import hmac
import re
import secrets
import sqlite3

from app.db import rows

_ESCAPES = {"\\": r"\5c", "*": r"\2a", "(": r"\28", ")": r"\29", "\x00": r"\00"}


def escape_filter_chars(value: str) -> str:
    return "".join(_ESCAPES.get(c, c) for c in value)


def _unescape(value: str) -> str:
    return re.sub(r"\\([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), value)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
    return f"pbkdf2${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, _digest = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), stored)


class DirectoryError(Exception):
    pass


class FakeDirectory:
    """Minimal directory: ``bind`` and equality/wildcard ``search``."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def bind(self, dn: str, password: str) -> bool:
        found = rows(self.conn, "SELECT password_hash FROM directory_entry WHERE dn = ?", (dn,))
        return bool(found) and bool(password) and verify_password(password, found[0]["password_hash"])

    def search(self, filter_: str) -> list[str]:
        m = re.fullmatch(r"\((\w+)=([^()]*)\)", filter_)
        if not m:
            raise DirectoryError(f"unsupported filter: {filter_!r}")
        attr, raw = m.group(1), m.group(2)
        if attr not in ("uid", "cn"):
            return []
        # Unescaped '*' is a wildcard; escaped '\2a' is a literal asterisk.
        parts = [re.escape(_unescape(p)) for p in raw.split("*")]
        pattern = re.compile("^" + ".*".join(parts) + "$", re.IGNORECASE)
        entries = rows(self.conn, f"SELECT dn, {attr} AS v FROM directory_entry ORDER BY dn")
        return [e["dn"] for e in entries if e["v"] is not None and pattern.match(e["v"])]


class DirectoryAuthenticator:
    def __init__(self, directory: FakeDirectory, service_dn: str, service_password: str, identifier: str) -> None:
        self.directory = directory
        self.service_dn = service_dn
        self.service_password = service_password
        self.identifier = identifier

    def authenticate(self, username: str, password: str) -> bool:
        if not username or not password:
            return False
        if not self.directory.bind(self.service_dn, self.service_password):
            raise DirectoryError("service account bind refused")
        found = self.directory.search(f"({self.identifier}={escape_filter_chars(username)})")
        if not found:
            return False
        return self.directory.bind(found[0], password)
