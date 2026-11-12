from __future__ import annotations

import re
import sqlite3
import hashlib
import json
import math
import os
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from app.harness.events import utc_now_iso
from app.security import resolved_principals


class SQLiteRagStore:
    def __init__(self, db_path: str | Path = "data/dev_agent_studio.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_document (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection TEXT NOT NULL,
                    path TEXT NOT NULL,
                    size INTEGER,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_chunk (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            document_columns = {row["name"] for row in conn.execute("PRAGMA table_info(rag_document)").fetchall()}
            for name, definition in {
                "content_hash": "TEXT",
                "version": "INTEGER NOT NULL DEFAULT 1",
                "is_current": "INTEGER NOT NULL DEFAULT 1",
                "valid_to": "TEXT",
                "acl_json": "TEXT NOT NULL DEFAULT '[\"*\"]'",
            }.items():
                if name not in document_columns:
                    conn.execute(f"ALTER TABLE rag_document ADD COLUMN {name} {definition}")
            chunk_columns = {row["name"] for row in conn.execute("PRAGMA table_info(rag_chunk)").fetchall()}
            if "document_version" not in chunk_columns:
                conn.execute("ALTER TABLE rag_chunk ADD COLUMN document_version INTEGER NOT NULL DEFAULT 1")
            if "metadata_json" not in chunk_columns:
                conn.execute("ALTER TABLE rag_chunk ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_document_current ON rag_document(collection, path, is_current)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_gold_case (
                    case_id TEXT PRIMARY KEY,
                    collection TEXT NOT NULL,
                    question TEXT NOT NULL,
                    expected_chunk_ids_json TEXT NOT NULL,
                    expected_paths_json TEXT NOT NULL,
                    expected_keywords_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def ingest(self, collection: str, documents: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> dict[str, int]:
        grouped = _group_chunks(chunks)
        changed_documents = 0
        with self._connection() as conn:
            for doc in documents:
                path = str(doc["path"])
                content_hash = _content_hash(grouped.get(path, []))
                existing = conn.execute(
                    "SELECT id, content_hash, version, acl_json FROM rag_document WHERE collection = ? AND path = ? AND is_current = 1 ORDER BY version DESC LIMIT 1",
                    (collection, path),
                ).fetchone()
                if existing and existing["content_hash"] == content_hash:
                    continue
                changed_documents += 1
                version = int(existing["version"] or 0) + 1 if existing else 1
                acl_json = existing["acl_json"] if existing else '["*"]'
                if existing:
                    conn.execute("UPDATE rag_document SET is_current = 0, valid_to = ? WHERE id = ?", (utc_now_iso(), existing["id"]))
                conn.execute(
                    "INSERT INTO rag_document(collection, path, size, created_at, content_hash, version, is_current, acl_json) VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                    (collection, path, doc.get("size"), utc_now_iso(), content_hash, version, acl_json),
                )
                conn.execute("DELETE FROM rag_chunk WHERE collection = ? AND path = ? AND document_version = ?", (collection, path, version))
                for chunk in grouped.get(path, []):
                    conn.execute(
                        "INSERT INTO rag_chunk(collection, chunk_id, path, content, metadata_json, created_at, document_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (collection, chunk["chunk_id"], path, chunk["content"], json.dumps(chunk.get("metadata") or {}, ensure_ascii=False), utc_now_iso(), version),
                    )
        return {"document_count": len(documents), "chunk_count": len(chunks), "changed_document_count": changed_documents}

    def query(self, collection: str, question: str, limit: int = 5, actor_id: str = "local-user") -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT c.chunk_id, c.path, c.content, c.metadata_json, d.acl_json
                FROM rag_chunk c JOIN rag_document d
                  ON c.collection = d.collection AND c.path = d.path AND c.document_version = d.version
                WHERE c.collection = ? AND d.is_current = 1
                """,
                (collection,),
            ).fetchall()
        principals = resolved_principals(actor_id)
        visible = [
            {"chunk_id": row["chunk_id"], "path": row["path"], "content": row["content"], "metadata": _json_dict(row["metadata_json"])}
            for row in rows
            if _acl_allows(row["acl_json"], principals)
        ]
        ranked = _rank_hybrid(question, visible)
        reranked = _rerank_candidates(question, ranked[: max(limit * 4, 20)])
        return [_public_rag_result(item) for item in reranked[:limit]]

    def list_documents(self, collection: str | None = None, actor_id: str = "local-user") -> list[dict[str, Any]]:
        with self._connection() as conn:
            if collection:
                rows = conn.execute(
                    "SELECT collection, path, size, created_at, version, is_current, valid_to, acl_json FROM rag_document WHERE collection = ? ORDER BY path, version DESC",
                    (collection,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT collection, path, size, created_at, version, is_current, valid_to, acl_json FROM rag_document ORDER BY collection, path, version DESC"
                ).fetchall()
        principals = resolved_principals(actor_id)
        return [{key: value for key, value in dict(row).items() if key != "acl_json"} for row in rows if _acl_allows(row["acl_json"], principals)]

    def set_document_acl(self, collection: str, path: str, principals: list[str]) -> bool:
        with self._connection() as conn:
            updated = conn.execute(
                "UPDATE rag_document SET acl_json = ? WHERE collection = ? AND path = ? AND is_current = 1",
                (json.dumps(sorted(set(principals))), collection, path),
            ).rowcount
        return bool(updated)

    def list_gold_cases(self, collection: str | None = None, include_disabled: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM rag_gold_case"
        params: list[Any] = []
        clauses: list[str] = []
        if collection:
            clauses.append("collection = ?")
            params.append(collection)
        if not include_disabled:
            clauses.append("enabled = 1")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC"
        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_gold_row_to_dict(dict(row)) for row in rows]

    def save_gold_case(self, case: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        case_id = str(case.get("case_id") or f"rag_gold_{hashlib.sha1((case.get('question') or now).encode('utf-8')).hexdigest()[:12]}")
        payload = _normalize_gold_case({**case, "case_id": case_id})
        with self._connection() as conn:
            existing = conn.execute("SELECT created_at FROM rag_gold_case WHERE case_id = ?", (case_id,)).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO rag_gold_case(
                    case_id, collection, question, expected_chunk_ids_json, expected_paths_json,
                    expected_keywords_json, metadata_json, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET
                    collection = excluded.collection,
                    question = excluded.question,
                    expected_chunk_ids_json = excluded.expected_chunk_ids_json,
                    expected_paths_json = excluded.expected_paths_json,
                    expected_keywords_json = excluded.expected_keywords_json,
                    metadata_json = excluded.metadata_json,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    payload["case_id"],
                    payload["collection"],
                    payload["question"],
                    json.dumps(payload["expected_chunk_ids"], ensure_ascii=False),
                    json.dumps(payload["expected_paths"], ensure_ascii=False),
                    json.dumps(payload["expected_keywords"], ensure_ascii=False),
                    json.dumps(payload["metadata"], ensure_ascii=False),
                    1 if payload["enabled"] else 0,
                    created_at,
                    now,
                ),
            )
        return {**payload, "created_at": created_at, "updated_at": now}

    def delete_gold_case(self, case_id: str) -> bool:
        with self._connection() as conn:
            removed = conn.execute("DELETE FROM rag_gold_case WHERE case_id = ?", (case_id,)).rowcount
        return bool(removed)

    def add_note(self, collection: str, path: str, content: str, principals: list[str] | None = None) -> dict[str, str]:
        safe_path = path.strip() or "manual-note"
        chunk_id = f"{safe_path}#note-{self._slug(content)[:24]}"
        now = utc_now_iso()
        content_hash = _content_hash([{"content": content}])
        with self._connection() as conn:
            existing = conn.execute(
                "SELECT id, version FROM rag_document WHERE collection = ? AND path = ? AND is_current = 1 ORDER BY version DESC LIMIT 1",
                (collection, safe_path),
            ).fetchone()
            version = int(existing["version"] or 1) if existing else 1
            if existing:
                conn.execute(
                    "UPDATE rag_document SET size = ?, created_at = ?, content_hash = ? WHERE id = ?",
                    (len(content), now, content_hash, existing["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO rag_document(collection, path, size, created_at, content_hash, version, is_current, acl_json) VALUES (?, ?, ?, ?, ?, 1, 1, ?)",
                    (collection, safe_path, len(content), now, content_hash, json.dumps(principals if principals is not None else ["*"])),
                )
            if principals is not None:
                conn.execute("UPDATE rag_document SET acl_json=? WHERE collection=? AND path=? AND is_current=1", (json.dumps(principals), collection, safe_path))
            conn.execute(
                "INSERT INTO rag_chunk(collection, chunk_id, path, content, created_at, document_version) VALUES (?, ?, ?, ?, ?, ?)",
                (collection, chunk_id, safe_path, content, now, version),
            )
        return {"collection": collection, "chunk_id": chunk_id, "path": safe_path}

    def delete_note(self, collection: str, path: str) -> bool:
        with self._connection() as conn:
            removed = conn.execute(
                "DELETE FROM rag_chunk WHERE collection = ? AND path = ?", (collection, path)
            ).rowcount
            conn.execute("DELETE FROM rag_document WHERE collection = ? AND path = ?", (collection, path))
        return bool(removed)

    def status(self) -> dict[str, Any]:
        return {
            "kind": "sqlite",
            "database_path": str(self.db_path),
            "retrieval": "hybrid_bm25_token_rrf",
            "embedding_source": None,
            "reranker": _config().get("DEV_AGENT_RAG_RERANKER", "off"),
        }

    def _slug(self, text: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", text.strip().lower()).strip("-")
        return slug or "note"


class EmbeddingProvider:
    def __init__(self, dimension: int = 1536):
        self.dimension = dimension
        self.model = _config().get("DEV_AGENT_EMBEDDING_MODEL", "text-embedding-3-small")
        self._last_source = "not_used"

    @property
    def source(self) -> str:
        return self._last_source

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        config = _config()
        api_key = config.get("OPENAI_API_KEY", "")
        if api_key:
            try:
                from langchain_openai import OpenAIEmbeddings

                kwargs: dict[str, Any] = {"model": self.model, "api_key": api_key}
                if config.get("OPENAI_BASE_URL"):
                    kwargs["base_url"] = config["OPENAI_BASE_URL"]
                embeddings = OpenAIEmbeddings(**kwargs)
                vectors = embeddings.embed_documents(texts)
                self._last_source = "openai"
                return [_fit_dimension(vector, self.dimension) for vector in vectors]
            except Exception:
                pass
        self._last_source = "hash_fallback"
        return [self._hash_embedding(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        config = _config()
        api_key = config.get("OPENAI_API_KEY", "")
        if api_key:
            try:
                from langchain_openai import OpenAIEmbeddings

                kwargs: dict[str, Any] = {"model": self.model, "api_key": api_key}
                if config.get("OPENAI_BASE_URL"):
                    kwargs["base_url"] = config["OPENAI_BASE_URL"]
                embeddings = OpenAIEmbeddings(**kwargs)
                self._last_source = "openai"
                return _fit_dimension(embeddings.embed_query(text), self.dimension)
            except Exception:
                pass
        self._last_source = "hash_fallback"
        return self._hash_embedding(text)

    def _hash_embedding(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}|[\u4e00-\u9fa5]{1,}", text.lower())
        if not tokens:
            tokens = [text[:64] or "empty"]
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8", errors="ignore")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class PgVectorRagStore:
    def __init__(self):
        config = _config()
        self.database_url = config.get("PGVECTOR_DATABASE_URL") or config.get("DATABASE_URL", "")
        self.dimension = int(config.get("DEV_AGENT_EMBEDDING_DIM", "1536") or 1536)
        self.embedding = EmbeddingProvider(self.dimension)
        if not self.database_url:
            raise RuntimeError("PGVECTOR_DATABASE_URL or DATABASE_URL is required for pgvector RAG store")
        self._init_schema()

    def _connect(self):
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("psycopg is required. Install with: pip install -e \".[vector]\"") from exc
        # Keep API startup responsive when Docker/Postgres is stopped. The active
        # store can still be selected explicitly once pgvector is available.
        return psycopg.connect(self.database_url, connect_timeout=3)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS rag_document (
                        id BIGSERIAL PRIMARY KEY,
                        collection TEXT NOT NULL,
                        path TEXT NOT NULL,
                        size INTEGER,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        UNIQUE(collection, path)
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS rag_chunk (
                        id BIGSERIAL PRIMARY KEY,
                        collection TEXT NOT NULL,
                        chunk_id TEXT NOT NULL,
                        path TEXT NOT NULL,
                        content TEXT NOT NULL,
                        metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        embedding vector({self.dimension}) NOT NULL,
                        embedding_source TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        UNIQUE(collection, chunk_id)
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_rag_document_collection ON rag_document(collection)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_rag_chunk_collection ON rag_chunk(collection)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_rag_chunk_embedding ON rag_chunk USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
                )
                cur.execute("ALTER TABLE rag_document ADD COLUMN IF NOT EXISTS content_hash TEXT")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_rag_lexical ON rag_chunk USING gin(to_tsvector('simple', content || ' ' || path))")
                cur.execute("ALTER TABLE rag_document ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1")
                cur.execute("ALTER TABLE rag_document ADD COLUMN IF NOT EXISTS is_current BOOLEAN NOT NULL DEFAULT TRUE")
                cur.execute("ALTER TABLE rag_document ADD COLUMN IF NOT EXISTS valid_to TIMESTAMPTZ")
                cur.execute("ALTER TABLE rag_document ADD COLUMN IF NOT EXISTS acl_json JSONB NOT NULL DEFAULT '[\"*\"]'::jsonb")
                cur.execute("ALTER TABLE rag_chunk ADD COLUMN IF NOT EXISTS document_version INTEGER NOT NULL DEFAULT 1")
                cur.execute("ALTER TABLE rag_chunk ADD COLUMN IF NOT EXISTS metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb")
                cur.execute("ALTER TABLE rag_document DROP CONSTRAINT IF EXISTS rag_document_collection_path_key")
                cur.execute("ALTER TABLE rag_chunk DROP CONSTRAINT IF EXISTS rag_chunk_collection_chunk_id_key")
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_rag_document_version ON rag_document(collection, path, version)")
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_rag_chunk_version ON rag_chunk(collection, chunk_id, document_version)")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS rag_gold_case (
                        case_id TEXT PRIMARY KEY,
                        collection TEXT NOT NULL,
                        question TEXT NOT NULL,
                        expected_chunk_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                        expected_paths_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                        expected_keywords_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                        metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                        enabled BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
            conn.commit()

    def ingest(self, collection: str, documents: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> dict[str, int]:
        grouped = _group_chunks(chunks)
        now = utc_now_iso()
        changed_chunks: list[tuple[dict[str, Any], int]] = []
        changed_documents = 0
        with self._connect() as conn:
            with conn.cursor() as cur:
                for doc in documents:
                    path = str(doc["path"])
                    content_hash = _content_hash(grouped.get(path, []))
                    cur.execute(
                        """
                        SELECT id, content_hash, version, acl_json
                        FROM rag_document
                        WHERE collection = %s AND path = %s AND is_current = TRUE
                        ORDER BY version DESC
                        LIMIT 1
                        """,
                        (collection, path),
                    )
                    existing = cur.fetchone()
                    if existing and existing[1] == content_hash:
                        continue
                    changed_documents += 1
                    version = int(existing[2] or 0) + 1 if existing else 1
                    acl_json = existing[3] if existing else ["*"]
                    if existing:
                        cur.execute("UPDATE rag_document SET is_current = FALSE, valid_to = %s WHERE id = %s", (now, existing[0]))
                    cur.execute(
                        """
                        INSERT INTO rag_document(collection, path, size, created_at, content_hash, version, is_current, acl_json)
                        VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s::jsonb)
                        """,
                        (collection, path, doc.get("size"), now, content_hash, version, _json_text(acl_json)),
                    )
                    cur.execute(
                        "DELETE FROM rag_chunk WHERE collection = %s AND path = %s AND document_version = %s",
                        (collection, path, version),
                    )
                    for chunk in grouped.get(path, []):
                        changed_chunks.append((chunk, version))
                vectors = self.embedding.embed_documents([chunk["content"] for chunk, _version in changed_chunks])
                for (chunk, version), vector in zip(changed_chunks, vectors, strict=False):
                    cur.execute(
                        """
                        INSERT INTO rag_chunk(collection, chunk_id, path, content, metadata_json, embedding, embedding_source, created_at, document_version)
                        VALUES (%s, %s, %s, %s, %s::jsonb, %s::vector, %s, %s, %s)
                        """,
                        (
                            collection,
                            chunk["chunk_id"],
                            chunk["path"],
                            chunk["content"],
                            _json_text(chunk.get("metadata") or {}),
                            _vector_literal(vector),
                            self.embedding.source,
                            now,
                            version,
                        ),
                    )
            conn.commit()
        return {"document_count": len(documents), "chunk_count": len(chunks), "changed_document_count": changed_documents, "changed_chunk_count": len(changed_chunks)}

    def query(self, collection: str, question: str, limit: int = 5, actor_id: str = "local-user") -> list[dict[str, Any]]:
        terms = _tokens(question)
        if not terms:
            return []
        vector = _vector_literal(self.embedding.embed_query(question))
        candidate_limit = max(limit * 8, 60)
        acl = resolved_principals(actor_id)
        base = """
            SELECT c.chunk_id,c.path,c.content,c.metadata_json,
                   1-(c.embedding <=> %s::vector) AS vector_score,d.acl_json
            FROM rag_chunk c JOIN rag_document d
              ON c.collection=d.collection AND c.path=d.path AND c.document_version=d.version
            WHERE c.collection=%s AND d.is_current=TRUE
              AND (d.acl_json ?| %s OR d.acl_json @> '["*"]'::jsonb)
        """
        # Independent recall: ACL is applied before either candidate LIMIT.
        lexical_query = " OR ".join('"' + term.replace('"', '') + '"' for term in terms)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(base + " ORDER BY c.embedding <=> %s::vector LIMIT %s",
                        (vector, collection, acl, vector, candidate_limit))
            vector_rows = cur.fetchall()
            cur.execute(base + """
                AND to_tsvector('simple', c.content || ' ' || c.path)
                    @@ websearch_to_tsquery('simple', %s)
                ORDER BY ts_rank_cd(to_tsvector('simple', c.content || ' ' || c.path),
                                    websearch_to_tsquery('simple', %s)) DESC LIMIT %s
                """, (vector, collection, acl, lexical_query, lexical_query, candidate_limit))
            lexical_rows = cur.fetchall()
        visible = {}
        for source, rows in (("vector", vector_rows), ("lexical", lexical_rows)):
            for row in rows:
                key = (row[1], row[0])
                item = visible.setdefault(key, {
                    "chunk_id": row[0], "path": row[1], "content": str(row[2]),
                    "metadata": _json_dict(row[3]), "vector_score": float(row[4] or 0),
                    "retrieval_sources": [], "embedding_source": self.embedding.source,
                })
                item["retrieval_sources"].append(source)
        ranked = _rank_hybrid(question, list(visible.values()), vector_score_key="vector_score")
        reranked = _rerank_candidates(question, ranked[:max(limit * 4, 20)])
        return [_public_rag_result(item) for item in reranked[:limit]]

    def list_documents(self, collection: str | None = None, actor_id: str = "local-user") -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if collection:
                    cur.execute(
                        """
                        SELECT collection, path, size, created_at, version, is_current, valid_to, acl_json
                        FROM rag_document
                        WHERE collection = %s
                        ORDER BY path, version DESC
                        """,
                        (collection,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT collection, path, size, created_at, version, is_current, valid_to, acl_json
                        FROM rag_document
                        ORDER BY collection, path, version DESC
                        """
                    )
                rows = cur.fetchall()
        return [
            {
                "collection": row[0],
                "path": row[1],
                "size": row[2],
                "created_at": _china_minute(row[3]),
                "version": row[4],
                "is_current": bool(row[5]),
                "valid_to": _china_minute(row[6]) if row[6] else None,
            }
            for row in rows
            if _acl_allows(_json_text(row[7]), resolved_principals(actor_id))
        ]

    def set_document_acl(self, collection: str, path: str, principals: list[str]) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE rag_document SET acl_json = %s::jsonb WHERE collection = %s AND path = %s AND is_current = TRUE",
                    (json.dumps(sorted(set(principals))), collection, path),
                )
                updated = cur.rowcount
            conn.commit()
        return bool(updated)

    def add_note(self, collection: str, path: str, content: str, principals: list[str] | None = None) -> dict[str, str]:
        safe_path = path.strip() or "manual-note"
        chunk_id = f"{safe_path}#note-{_slug(content)[:24]}"
        now = utc_now_iso()
        vector = self.embedding.embed_query(content)
        content_hash = _content_hash([{"content": content}])
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, version FROM rag_document
                    WHERE collection = %s AND path = %s AND is_current = TRUE
                    ORDER BY version DESC LIMIT 1
                    """,
                    (collection, safe_path),
                )
                existing = cur.fetchone()
                version = int(existing[1] or 1) if existing else 1
                if existing:
                    cur.execute(
                        "UPDATE rag_document SET size = %s, created_at = %s, content_hash = %s WHERE id = %s",
                        (len(content), now, content_hash, existing[0]),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO rag_document(collection, path, size, created_at, content_hash, version, is_current, acl_json)
                        VALUES (%s, %s, %s, %s, %s, 1, TRUE, %s::jsonb)
                        """,
                        (collection, safe_path, len(content), now, content_hash, json.dumps(principals if principals is not None else ["*"])),
                    )
                if principals is not None:
                    cur.execute("UPDATE rag_document SET acl_json=%s::jsonb WHERE collection=%s AND path=%s AND is_current=TRUE", (json.dumps(principals), collection, safe_path))
                cur.execute(
                    """
                    INSERT INTO rag_chunk(collection, chunk_id, path, content, embedding, embedding_source, created_at, document_version)
                    VALUES (%s, %s, %s, %s, %s::vector, %s, %s, %s)
                    ON CONFLICT(collection, chunk_id, document_version)
                    DO UPDATE SET content = EXCLUDED.content,
                                  embedding = EXCLUDED.embedding,
                                  embedding_source = EXCLUDED.embedding_source,
                                  created_at = EXCLUDED.created_at
                    """,
                    (
                        collection,
                        chunk_id,
                        safe_path,
                        content,
                        _vector_literal(vector),
                        self.embedding.source,
                        now,
                        version,
                    ),
                )
            conn.commit()
        return {"collection": collection, "chunk_id": chunk_id, "path": safe_path}

    def delete_note(self, collection: str, path: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM rag_chunk WHERE collection = %s AND path = %s", (collection, path))
                removed = cur.rowcount
                cur.execute("DELETE FROM rag_document WHERE collection = %s AND path = %s", (collection, path))
            conn.commit()
        return bool(removed)

    def list_gold_cases(self, collection: str | None = None, include_disabled: bool = False) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if collection:
            clauses.append("collection = %s")
            params.append(collection)
        if not include_disabled:
            clauses.append("enabled = TRUE")
        query = "SELECT case_id, collection, question, expected_chunk_ids_json, expected_paths_json, expected_keywords_json, metadata_json, enabled, created_at, updated_at FROM rag_gold_case"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
        return [
            _gold_row_to_dict(
                {
                    "case_id": row[0],
                    "collection": row[1],
                    "question": row[2],
                    "expected_chunk_ids_json": _json_text(row[3]),
                    "expected_paths_json": _json_text(row[4]),
                    "expected_keywords_json": _json_text(row[5]),
                    "metadata_json": _json_text(row[6]),
                    "enabled": row[7],
                    "created_at": str(row[8]),
                    "updated_at": str(row[9]),
                }
            )
            for row in rows
        ]

    def save_gold_case(self, case: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        case_id = str(case.get("case_id") or f"rag_gold_{hashlib.sha1((case.get('question') or now).encode('utf-8')).hexdigest()[:12]}")
        payload = _normalize_gold_case({**case, "case_id": case_id})
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT created_at FROM rag_gold_case WHERE case_id = %s", (case_id,))
                existing = cur.fetchone()
                created_at = str(existing[0]) if existing else now
                cur.execute(
                    """
                    INSERT INTO rag_gold_case(
                        case_id, collection, question, expected_chunk_ids_json, expected_paths_json,
                        expected_keywords_json, metadata_json, enabled, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s)
                    ON CONFLICT(case_id) DO UPDATE SET
                        collection = EXCLUDED.collection,
                        question = EXCLUDED.question,
                        expected_chunk_ids_json = EXCLUDED.expected_chunk_ids_json,
                        expected_paths_json = EXCLUDED.expected_paths_json,
                        expected_keywords_json = EXCLUDED.expected_keywords_json,
                        metadata_json = EXCLUDED.metadata_json,
                        enabled = EXCLUDED.enabled,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        payload["case_id"],
                        payload["collection"],
                        payload["question"],
                        json.dumps(payload["expected_chunk_ids"], ensure_ascii=False),
                        json.dumps(payload["expected_paths"], ensure_ascii=False),
                        json.dumps(payload["expected_keywords"], ensure_ascii=False),
                        json.dumps(payload["metadata"], ensure_ascii=False),
                        payload["enabled"],
                        created_at,
                        now,
                    ),
                )
            conn.commit()
        return {**payload, "created_at": created_at, "updated_at": now}

    def delete_gold_case(self, case_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM rag_gold_case WHERE case_id = %s", (case_id,))
                removed = cur.rowcount
            conn.commit()
        return bool(removed)

    def status(self) -> dict[str, Any]:
        return {
            "kind": "pgvector",
            "database_url_configured": bool(self.database_url),
            "embedding_model": self.embedding.model,
            "embedding_source": self.embedding.source,
            "embedding_dim": self.dimension,
            "retrieval": "vector_bm25_rrf",
            "reranker": _config().get("DEV_AGENT_RAG_RERANKER", "off"),
        }


def create_rag_store() -> SQLiteRagStore | PgVectorRagStore:
    kind = _config().get("DEV_AGENT_RAG_STORE", "sqlite").lower()
    if kind == "pgvector":
        try:
            return PgVectorRagStore()
        except Exception:
            if _config().get("DEV_AGENT_RAG_STRICT", "false").lower() == "true":
                raise
            return SQLiteRagStore()
    return SQLiteRagStore()


def _config() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    env_path = Path(os.getenv("APP_ENV_FILE", str(root / ".env")))
    values: dict[str, str] = {}
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key in [
        "DEV_AGENT_RAG_STORE",
        "PGVECTOR_DATABASE_URL",
        "DATABASE_URL",
        "DEV_AGENT_EMBEDDING_MODEL",
        "DEV_AGENT_EMBEDDING_DIM",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "DEV_AGENT_RAG_RERANKER",
        "DEV_AGENT_RAG_MIN_VECTOR_SCORE",
    ]:
        if os.getenv(key):
            values[key] = os.getenv(key, "")
    return values


def _fit_dimension(vector: list[float], dimension: int) -> list[float]:
    values = [float(value) for value in vector]
    if len(values) == dimension:
        return values
    if len(values) > dimension:
        return values[:dimension]
    return values + [0.0] * (dimension - len(values))


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"


def _slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", text.strip().lower()).strip("-")
    return slug or "note"


def _group_chunks(chunks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for chunk in chunks:
        grouped.setdefault(str(chunk["path"]), []).append(chunk)
    return grouped


def _content_hash(chunks: list[dict[str, Any]]) -> str:
    content = "\n".join(
        json.dumps(
            {"content": str(chunk.get("content") or ""), "metadata": chunk.get("metadata") or {}},
            ensure_ascii=False,
            sort_keys=True,
        )
        for chunk in chunks
    )
    return hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()


def _tokens(text: str) -> list[str]:
    stopwords = {"the", "a", "an", "is", "are", "to", "in", "of", "for", "and", "or", "how", "does", "do", "where", "what", "which", "with", "by", "from", "it", "this", "that"}
    expanded = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    terms = re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}|[\u4e00-\u9fa5]{2,}", text.lower() + " " + expanded.lower())
    return list(dict.fromkeys(term for term in terms if term not in stopwords))


def _rank_hybrid(question: str, rows: list[dict[str, Any]], vector_score_key: str | None = None) -> list[dict[str, Any]]:
    if not rows or not _tokens(question):
        return []
    query_terms = _tokens(question)
    corpus_tokens = [_tokens(str(row.get("content") or "")) for row in rows]
    bm25_scores = _bm25_scores(query_terms, corpus_tokens)
    semantic_scores = [
        float(row.get(vector_score_key) or 0.0) if vector_score_key else _token_overlap(query_terms, tokens)
        for row, tokens in zip(rows, corpus_tokens, strict=False)
    ]
    bm25_rank = _rank_positions(bm25_scores)
    semantic_rank = _rank_positions(semantic_scores)
    ranked: list[dict[str, Any]] = []
    threshold = float(_config().get("DEV_AGENT_RAG_MIN_VECTOR_SCORE", "0.35"))
    for index, row in enumerate(rows):
        score = (1 / (60 + bm25_rank[index])) + (1 / (60 + semantic_rank[index]))
        score += bm25_scores[index] * 0.001 + semantic_scores[index] * 0.05
        if bm25_scores[index] > 0 or (vector_score_key and semantic_scores[index] >= threshold):
            ranked.append(
                {
                    **row,
                    "score": score,
                    "bm25_score": bm25_scores[index],
                    "semantic_score": semantic_scores[index],
                    "retrieval_mode": "vector_bm25_rrf" if vector_score_key else "hybrid_bm25_token_rrf",
                }
            )
    ranked.sort(key=lambda item: float(item["score"]), reverse=True)
    return ranked


def _bm25_scores(query_terms: list[str], documents: list[list[str]]) -> list[float]:
    if not query_terms or not documents:
        return [0.0 for _ in documents]
    k1 = 1.5
    b = 0.75
    avgdl = sum(len(doc) for doc in documents) / max(1, len(documents))
    df = {term: sum(1 for doc in documents if term in set(doc)) for term in set(query_terms)}
    scores: list[float] = []
    for doc in documents:
        doc_len = len(doc) or 1
        freqs = {term: doc.count(term) for term in set(query_terms)}
        score = 0.0
        for term in query_terms:
            tf = freqs.get(term, 0)
            if not tf:
                continue
            idf = math.log(1 + (len(documents) - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
            score += idf * ((tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / max(avgdl, 1))))
        scores.append(score)
    return scores


def _token_overlap(query_terms: list[str], doc_terms: list[str]) -> float:
    if not query_terms:
        return 0.0
    return len(set(query_terms) & set(doc_terms)) / max(1, len(set(query_terms)))


def _rank_positions(scores: list[float]) -> list[int]:
    ordered = sorted(range(len(scores)), key=lambda index: scores[index], reverse=True)
    ranks = [len(scores) + 1] * len(scores)
    for rank, index in enumerate(ordered, start=1):
        ranks[index] = rank
    return ranks


def _rerank_candidates(question: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if _config().get("DEV_AGENT_RAG_RERANKER", "off").lower() != "llm" or len(candidates) <= 1:
        return candidates
    try:
        from app.providers.llm_provider import llm_provider

        candidate_lines = [
            {"chunk_id": item["chunk_id"], "path": item["path"], "preview": str(item["content"])[:420]}
            for item in candidates[:12]
        ]
        response = llm_provider.generate_with_status(
            "You are a RAG reranker. Return JSON only: {\"ordered_chunk_ids\":[...]}",
            json.dumps({"question": question, "candidates": candidate_lines}, ensure_ascii=False),
            json.dumps({"ordered_chunk_ids": [item["chunk_id"] for item in candidates]}, ensure_ascii=False),
            agent="rag_reranker",
            prompt_version="rag_reranker.v1",
            use_active_prompt=False,
        )
        parsed = json.loads(str(response.get("text") or "{}"))
        ordered = [str(item) for item in parsed.get("ordered_chunk_ids", [])]
        if not ordered:
            return candidates
        rank = {chunk_id: index for index, chunk_id in enumerate(ordered)}
        reranked = sorted(candidates, key=lambda item: (rank.get(str(item.get("chunk_id")), len(rank) + 100), -float(item.get("score") or 0)))
        for index, item in enumerate(reranked):
            item["rerank_score"] = max(0, len(reranked) - index)
            item["retrieval_mode"] = f"{item.get('retrieval_mode', 'hybrid')}_llm_rerank"
        return reranked
    except Exception:
        return candidates


def _public_rag_result(item: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(item.get("metadata") or {})
    parent_content = str(metadata.pop("parent_content", "") or "")
    return {
        "chunk_id": item.get("chunk_id"),
        "path": item.get("path"),
        "score": round(float(item.get("score") or 0), 6),
        "keyword_score": round(float(item.get("bm25_score") or 0), 4),
        "semantic_score": round(float(item.get("semantic_score") or 0), 4),
        "rerank_score": item.get("rerank_score"),
        "retrieval_mode": item.get("retrieval_mode", "hybrid_bm25_token_rrf"),
        "content": str(item.get("content") or "")[:800],
        "metadata": metadata,
        "context_content": parent_content or str(item.get("content") or "")[:800],
        "retrieval_sources": item.get("retrieval_sources", ["lexical"]),
        "embedding_source": item.get("embedding_source", "token_overlap"),
    }


def _normalize_gold_case(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": str(case.get("case_id") or "").strip(),
        "collection": str(case.get("collection") or "default").strip() or "default",
        "question": str(case.get("question") or "").strip(),
        "expected_chunk_ids": [str(item) for item in case.get("expected_chunk_ids", [])],
        "expected_paths": [str(item) for item in case.get("expected_paths", [])],
        "expected_keywords": [str(item) for item in case.get("expected_keywords", [])],
        "metadata": dict(case.get("metadata") or {}),
        "enabled": bool(case.get("enabled", True)),
    }


def _gold_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": row["case_id"],
        "collection": row["collection"],
        "question": row["question"],
        "expected_chunk_ids": _json_list(row.get("expected_chunk_ids_json")),
        "expected_paths": _json_list(row.get("expected_paths_json")),
        "expected_keywords": _json_list(row.get("expected_keywords_json")),
        "metadata": _json_dict(row.get("metadata_json")),
        "enabled": bool(row.get("enabled")),
        "created_at": _china_minute(row.get("created_at")),
        "updated_at": _china_minute(row.get("updated_at")),
    }


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        return [str(item) for item in json.loads(str(value or "[]"))]
    except (TypeError, json.JSONDecodeError):
        return []


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _china_minute(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if re.match(r"^\d{4}-\d{2}-\d{2}, \d{2}:\d{2}$", text):
        return text
    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            return dt.strftime("%Y-%m-%d, %H:%M")
        return dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d, %H:%M")
    except ValueError:
        return text[:16].replace("T", ", ")


def _acl_allows(raw_acl: Any, actor_id: str | list[str]) -> bool:
    actors = set(actor_id if isinstance(actor_id, list) else [actor_id])
    if isinstance(raw_acl, list):
        principals = [str(item) for item in raw_acl]
        return "*" in principals or bool(actors.intersection(principals))
    try:
        principals = json.loads(str(raw_acl or '[]'))
    except json.JSONDecodeError:
        principals = []
    return isinstance(principals, list) and ("*" in principals or bool(actors.intersection(str(p) for p in principals)))


rag_store = create_rag_store()
