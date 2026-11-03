"""Password hashing and database-backed login sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass, field

from footage.db import call, execute, query, query_one

# scrypt from the standard library: memory-hard, no extra dependency.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
SESSION_COOKIE = "archive_session"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return "$".join([
        "scrypt", str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P),
        base64.b64encode(salt).decode(), base64.b64encode(digest).decode(),
    ])


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Verifying against a throwaway hash when the username does not exist keeps
# the response time the same, so it does not reveal which usernames exist.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def validate_new_password(password: str) -> str | None:
    if len(password) < 8:
        return "Mật khẩu phải có ít nhất 8 ký tự."
    return None


@dataclass
class User:
    user_id: int
    username: str
    full_name: str
    permissions: set[str] = field(default_factory=set)

    def can(self, permission: str) -> bool:
        return permission in self.permissions


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _permissions(connection, user_id: int) -> set[str]:
    rows = query(connection,
                 "SELECT permission_code FROM dbo.v_user_permissions WHERE user_id = ?", user_id)
    return {row["permission_code"] for row in rows}


def authenticate(connection, username: str, password: str) -> User | None:
    row = query_one(
        connection,
        "SELECT user_id, username, full_name, password_hash, is_active "
        "FROM dbo.users WHERE username = ?",
        username.strip().lower(),
    )
    if row is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, row["password_hash"]) or not row["is_active"]:
        return None
    execute(connection, "UPDATE dbo.users SET last_login_at = SYSDATETIME() WHERE user_id = ?",
            row["user_id"])
    return User(row["user_id"], row["username"], row["full_name"],
                _permissions(connection, row["user_id"]))


def create_session(connection, user_id: int, hours: int) -> str:
    token = secrets.token_urlsafe(32)
    execute(connection, "DELETE FROM dbo.sessions WHERE expires_at < SYSDATETIME()")
    execute(
        connection,
        "INSERT INTO dbo.sessions (session_hash, user_id, expires_at) "
        "VALUES (?, ?, DATEADD(HOUR, ?, SYSDATETIME()))",
        _token_hash(token), user_id, hours,
    )
    return token


def session_user(connection, token: str | None) -> User | None:
    if not token:
        return None
    row = query_one(
        connection,
        "SELECT u.user_id, u.username, u.full_name "
        "FROM dbo.sessions s JOIN dbo.users u ON u.user_id = s.user_id "
        "WHERE s.session_hash = ? AND s.expires_at > SYSDATETIME() AND u.is_active = 1",
        _token_hash(token),
    )
    if row is None:
        return None
    return User(row["user_id"], row["username"], row["full_name"],
                _permissions(connection, row["user_id"]))


def end_session(connection, token: str | None) -> None:
    if token:
        execute(connection, "DELETE FROM dbo.sessions WHERE session_hash = ?", _token_hash(token))


def create_user(connection, username: str, password: str, roles: list[str],
                full_name: str = "", organization_id: int | None = None,
                user_type: str = "other") -> int:
    """Create a user and roles atomically through sp_create_user."""
    problem = validate_new_password(password)
    if problem:
        raise ValueError(problem)
    rows = call(connection, "sp_create_user", username=username, password_hash=hash_password(password),
                roles=",".join(roles), full_name=full_name, organization_id=organization_id,
                user_type=user_type)
    return int(rows[0]["user_id"])


def set_roles(connection, user_id: int, roles: list[str]) -> None:
    known = {row["role_name"]: row["role_id"]
             for row in query(connection, "SELECT role_id, role_name FROM dbo.roles")}
    unknown = sorted(set(roles) - set(known))
    if unknown:
        raise ValueError(f"Vai trò không tồn tại: {', '.join(unknown)}")
    current = {row["role_id"] for row in query(
        connection, "SELECT role_id FROM dbo.user_roles WHERE user_id = ?", user_id)}
    wanted = {known[name] for name in roles}
    for role_id in current - wanted:
        execute(connection, "DELETE FROM dbo.user_roles WHERE user_id = ? AND role_id = ?",
                user_id, role_id)
    for role_id in wanted - current:
        execute(connection, "INSERT INTO dbo.user_roles (user_id, role_id) VALUES (?, ?)",
                user_id, role_id)


def set_password(connection, user_id: int, password: str) -> None:
    problem = validate_new_password(password)
    if problem:
        raise ValueError(problem)
    execute(connection, "UPDATE dbo.users SET password_hash = ? WHERE user_id = ?",
            hash_password(password), user_id)
    # A reset password must also end every existing login.
    execute(connection, "DELETE FROM dbo.sessions WHERE user_id = ?", user_id)
