import os
from uuid import uuid4

import pytest

from app.persistence.rag_store import SQLiteRagStore, PgVectorRagStore, _acl_allows, _vector_literal


def test_no_answer_empty_acl_and_malformed_acl(tmp_path):
    store = SQLiteRagStore(tmp_path / "rag.db")
    store.add_note("one", "note", "TransactionalOutbox publisher confirms")
    assert store.query("one", "volcanology quasar astronomy") == []
    assert store.query("one", "the and what") == []
    assert store.query("one", "TransactionalOutbox")
    store.set_document_acl("one", "note", [])
    assert store.query("one", "TransactionalOutbox") == []
    assert not _acl_allows("invalid JSON", "alice")
    assert not _acl_allows('{"alice":true}', "alice")


@pytest.mark.skipif(os.getenv("DEV_AGENT_TEST_PGVECTOR") != "1", reason="disposable pgvector not configured")
def test_independent_lexical_recall_and_acl_before_vector_limit():
    store = PgVectorRagStore()
    collection = "iteration_recall_" + uuid4().hex
    docs = [{"path": f"d{i}.txt", "size": 40} for i in range(72)]
    chunks = [{"path": doc["path"], "chunk_id": doc["path"] + "#1", "content":
               "NeedleLookup777 target" if i == 70 else "VisibleLookup888 target" if i == 71 else "ordinary unrelated background"}
              for i, doc in enumerate(docs)]
    store.embedding.embed_documents = lambda texts: [[1.0] + [0.0] * (store.dimension-1) for _ in texts]
    store.embedding.embed_query = lambda text: [1.0] + [0.0] * (store.dimension-1)
    try:
        store.ingest(collection, docs, chunks)
        with store._connect() as conn, conn.cursor() as cur:
            vector = [0.2, 0.9797959] + [0.0] * (store.dimension-2)
            cur.execute("UPDATE rag_chunk SET embedding=%s::vector WHERE collection=%s", (_vector_literal(vector), collection))
            far = [-1.0] + [0.0] * (store.dimension-1)
            cur.execute("UPDATE rag_chunk SET embedding=%s::vector WHERE collection=%s AND path='d70.txt'", (_vector_literal(far), collection))
            conn.commit()
        result = store.query(collection, "NeedleLookup777", limit=1)
        assert result and result[0]["path"] == "d70.txt"
        assert "lexical" in result[0]["retrieval_sources"] and "vector" not in result[0]["retrieval_sources"]
        with store._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE rag_document SET acl_json='[\"private-owner\"]'::jsonb WHERE collection=%s AND path<>'d71.txt'", (collection,))
            conn.commit()
        result = store.query(collection, "VisibleLookup888", limit=1, actor_id="visitor")
        assert result and result[0]["path"] == "d71.txt"
        assert "vector" in result[0]["retrieval_sources"]
    finally:
        with store._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM rag_chunk WHERE collection=%s", (collection,))
            cur.execute("DELETE FROM rag_document WHERE collection=%s", (collection,))
            conn.commit()
