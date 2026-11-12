import base64

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api import routes
from app.security import IdentityStore
from app.persistence.memory_store import SQLiteMemoryStore
from app.persistence.rag_store import SQLiteRagStore


@pytest.fixture
def secured(tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_AGENT_AUTH_MODE", "required")
    monkeypatch.setenv("DEV_AGENT_AUTH_DB", str(tmp_path / "identities.db"))
    store = IdentityStore()
    tokens = {actor: store.create_user(actor, role) for actor, role in [("alice", "member"), ("bob", "member"), ("root", "admin")]}
    rag = SQLiteRagStore(tmp_path / "rag.db")
    memory = SQLiteMemoryStore(tmp_path / "memories.db")
    monkeypatch.setattr(routes, "rag_store", rag)
    monkeypatch.setattr(routes, "memory_store", memory)
    return TestClient(app), store, tokens, rag, memory


def headers(tokens, actor="alice"):
    return {"Authorization": "Bearer " + tokens[actor]}


def test_authentication_and_header_impersonation(secured):
    client, store, tokens, rag, _ = secured
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/me", headers={"X-DevAgent-Role": "admin"}).status_code == 401
    identity = client.get("/api/v1/auth/me", headers={**headers(tokens), "X-DevAgent-Actor": "root", "X-DevAgent-Role": "admin"})
    assert identity.json() == {"actor_id": "alice", "role": "member"}
    rag.add_note("private", "bob-note", "secretword", principals=["bob"])
    result = client.post("/api/v1/rag/query", json={"collection": "private", "question": "secretword", "actor_id": "bob"}, headers={**headers(tokens), "X-DevAgent-Actor": "bob"})
    assert result.status_code == 200 and result.json()["results"] == []
    assert client.post("/api/v1/tasks/run", json={}, headers=headers(tokens)).status_code == 403
    basic = base64.b64encode(("alice:" + tokens["alice"]).encode()).decode()
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Basic " + basic}).status_code == 200
    with store.connection() as conn:
        conn.execute("UPDATE identity SET enabled=0 WHERE actor_id='alice'")
    assert client.get("/api/v1/auth/me", headers=headers(tokens)).status_code == 401


def test_memory_owner_and_memberships_are_server_controlled(secured):
    client, store, tokens, rag, memory = secured
    payload = {"text": "我希望代码审查报告请用中文。", "scope": "user", "scope_id": "bob"}
    assert client.post("/api/v1/memories/extract", json=payload, headers={**headers(tokens), "X-DevAgent-Role": "admin"}).status_code == 403
    assert client.get("/api/v1/memories?scope=user&scope_id=bob", headers=headers(tokens)).status_code == 403
    team = {**payload, "scope": "team", "scope_id": "team-one"}
    assert client.post("/api/v1/memories/extract", json=team, headers=headers(tokens)).status_code == 403
    store.grant("alice", "team", "team-one", "editor")
    assert client.get("/api/v1/memories?scope=team&scope_id=team-one", headers=headers(tokens)).status_code == 200
    assert client.get("/api/v1/memories?scope=team&scope_id=another", headers=headers(tokens)).status_code == 403
    rag.add_note("team", "note", "sharedword", principals=["team:team-one"])
    assert rag.query("team", "sharedword", actor_id="alice")
    assert rag.query("team", "sharedword", actor_id="bob") == []


def test_confirming_private_memory_does_not_publish_it(secured):
    client, _, tokens, rag, memory = secured
    records = memory.extract_candidates("我希望代码审查报告请用中文。", scope="user", scope_id="alice")
    record = records[0]
    response = client.post(f"/api/v1/memories/{record['memory_id']}/confirm", json={"collection": "notes"}, headers=headers(tokens))
    assert response.status_code == 200
    assert rag.list_documents("notes", actor_id="alice")
    assert rag.list_documents("notes", actor_id="bob") == []
    assert client.delete(f"/api/v1/memories/{record['memory_id']}", headers=headers(tokens, "bob")).status_code == 403


def test_csrf_and_remote_personal_mode(secured, monkeypatch):
    client, _, tokens, *_ = secured
    assert client.post("/api/v1/tasks/run", headers={**headers(tokens, "root"), "Origin": "https://evil.example"}, json={}).status_code == 403
    monkeypatch.setenv("DEV_AGENT_AUTH_MODE", "local")
    remote = TestClient(app, client=("203.0.113.10", 1234))
    assert remote.get("/api/v1/auth/me").status_code == 403
