"""Server-owned identities for shared deployments; loopback-only personal mode."""
from __future__ import annotations

import argparse
import base64
import contextvars
import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse


@dataclass(frozen=True)
class Principal:
    actor_id: str
    role: str


_principal = contextvars.ContextVar("authenticated_principal", default=None)


def current_principal() -> Principal:
    principal = _principal.get()
    if principal is not None:
        return principal
    if os.getenv("DEV_AGENT_AUTH_MODE", "local") == "required":
        raise HTTPException(401, "Authentication required")
    return Principal("local-user", "admin")


class IdentityStore:
    def __init__(self):
        self.path = Path(os.getenv("DEV_AGENT_AUTH_DB", "data/identities.db"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS identity(
                actor_id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
                role TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS membership(
                actor_id TEXT NOT NULL, scope TEXT NOT NULL, scope_id TEXT NOT NULL,
                role TEXT NOT NULL, PRIMARY KEY(actor_id,scope,scope_id))""")

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def create_user(self, actor_id: str, role: str = "member", token: str | None = None) -> str:
        if not actor_id.strip() or role not in {"admin", "editor", "member"}:
            raise ValueError("invalid actor or role")
        token = token or secrets.token_urlsafe(32)
        if len(token) < 32:
            raise ValueError("use a generated token of at least 32 characters")
        with self.connection() as conn:
            conn.execute("INSERT INTO identity(actor_id,token_hash,role) VALUES(?,?,?)",
                         (actor_id, hashlib.sha256(token.encode()).hexdigest(), role))
        return token

    def authenticate(self, token: str) -> Principal | None:
        with self.connection() as conn:
            row = conn.execute("SELECT actor_id,role FROM identity WHERE token_hash=? AND enabled=1",
                               (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return Principal(row["actor_id"], row["role"]) if row else None

    def grant(self, actor_id: str, scope: str, scope_id: str, role: str):
        if scope not in {"project", "team", "collection"} or role not in {"member", "editor", "admin"}:
            raise ValueError("invalid membership")
        with self.connection() as conn:
            if not conn.execute("SELECT 1 FROM identity WHERE actor_id=?", (actor_id,)).fetchone():
                raise ValueError("actor does not exist")
            conn.execute("""INSERT INTO membership VALUES(?,?,?,?) ON CONFLICT(actor_id,scope,scope_id)
                DO UPDATE SET role=excluded.role""", (actor_id, scope, scope_id, role))

    def membership(self, actor_id: str, scope: str, scope_id: str) -> str | None:
        with self.connection() as conn:
            row = conn.execute("SELECT role FROM membership WHERE actor_id=? AND scope=? AND scope_id=?",
                               (actor_id, scope, scope_id)).fetchone()
        return row["role"] if row else None


def authorize_scope(scope: str, scope_id: str, action: str):
    principal = current_principal()
    if scope not in {"user", "project", "team"}:
        raise HTTPException(422, "Unknown memory scope")
    if principal.role == "admin":
        return
    if scope == "user":
        if scope_id != principal.actor_id:
            raise HTTPException(403, "User memory belongs to a different identity")
        return
    role = IdentityStore().membership(principal.actor_id, scope, scope_id)
    if not role or (action != "list" and role not in {"editor", "admin"}):
        raise HTTPException(403, "No server-side membership permits this action")
    if scope == "team" and action in {"confirm", "reject", "delete"} and role != "admin":
        raise HTTPException(403, "Team governance requires a team administrator")


def resolved_principals(actor_id: str) -> list[str]:
    if os.getenv("DEV_AGENT_AUTH_MODE", "local") != "required":
        return [actor_id]
    with IdentityStore().connection() as conn:
        rows = conn.execute("SELECT scope,scope_id FROM membership WHERE actor_id=?", (actor_id,)).fetchall()
    return [actor_id, *[f"{row['scope']}:{row['scope_id']}" for row in rows]]


class IdentityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        if request.url.path == "/health":
            await self.app(scope, receive, send)
            return
        mode = os.getenv("DEV_AGENT_AUTH_MODE", "local")
        error = None
        principal = None
        if mode == "local":
            allowed = {"127.0.0.1", "::1", "localhost"}
            if os.getenv("APP_ENV") == "test":
                allowed |= {"testclient", "testserver"}
            if not request.client or request.client.host not in allowed or request.url.hostname not in allowed:
                error = (403, "Personal mode is loopback-only. Configure required authentication for shared access.")
            else:
                principal = Principal("local-user", "admin")
        elif mode == "required":
            header = request.headers.get("authorization", "")
            scheme, _, credential = header.partition(" ")
            username = None
            if scheme.lower() == "basic":
                try:
                    username, credential = base64.b64decode(credential, validate=True).decode().split(":", 1)
                except (ValueError, UnicodeError):
                    credential = ""
            elif scheme.lower() != "bearer":
                credential = ""
            principal = IdentityStore().authenticate(credential) if credential else None
            if not principal or (username is not None and username != principal.actor_id):
                error = (401, "Valid server-issued credentials are required")
        else:
            error = (503, "Invalid authentication mode")
        if not error and request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("origin"):
            allowed_origins = {str(request.base_url).rstrip("/")} | set(filter(None, os.getenv("DEV_AGENT_ALLOWED_ORIGINS", "").split(",")))
            if request.headers["origin"] not in allowed_origins:
                error = (403, "Cross-origin mutation rejected")
        # Executing tools, scanning local paths, editing global configuration and
        # running benchmarks remain admin-only until workspace isolation exists.
        path = request.url.path.removeprefix("/api/v1")
        safe = path in {"/auth/me", "/rag/query", "/rag/documents", "/rag/status"} or path.startswith("/memories")
        if not error and principal.role != "admin" and request.url.path.startswith("/api/") and not safe:
            error = (403, "This studio operation requires an administrator")
        if error:
            response = JSONResponse({"detail": error[1]}, status_code=error[0],
                headers={"WWW-Authenticate": 'Basic realm="DevAgent Studio", charset="UTF-8"'} if error[0] == 401 else {})
            await response(scope, receive, send)
            return
        token = _principal.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            _principal.reset(token)


def main():
    parser = argparse.ArgumentParser(description="Provision identities locally; never send client-chosen roles.")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-user")
    create.add_argument("actor_id")
    create.add_argument("--role", choices=["admin", "editor", "member"], default="member")
    grant = sub.add_parser("grant")
    for name in ("actor_id", "scope", "scope_id", "role"):
        grant.add_argument(name)
    revoke = sub.add_parser("revoke")
    revoke.add_argument("actor_id")
    args = parser.parse_args()
    store = IdentityStore()
    if args.command == "create-user":
        print(json.dumps({"actor_id": args.actor_id, "token": store.create_user(args.actor_id, args.role)}))
    elif args.command == "grant":
        store.grant(args.actor_id, args.scope, args.scope_id, args.role)
    else:
        with store.connection() as conn:
            conn.execute("UPDATE identity SET enabled=0 WHERE actor_id=?", (args.actor_id,))


if __name__ == "__main__":
    main()
