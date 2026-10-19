from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from uuid import uuid4

from app.agents.rag_tools import _chunk_text
from app.persistence.rag_store import PgVectorRagStore


@unittest.skipUnless(os.getenv("DEV_AGENT_TEST_PGVECTOR") == "1", "set DEV_AGENT_TEST_PGVECTOR=1 to run against PostgreSQL")
class PgVectorIntegrationTest(unittest.TestCase):
    def test_ingest_vector_recall_and_parent_context_round_trip(self) -> None:
        store = PgVectorRagStore()
        store.embedding._last_source = "hash_fallback"
        store.embedding.embed_documents = lambda texts: [store.embedding._hash_embedding(text) for text in texts]
        store.embedding.embed_query = lambda text: store.embedding._hash_embedding(text)
        collection = f"codex_pgvector_test_{uuid4().hex}"
        gold_path = Path(__file__).parent / "data" / "rag_chunking_gold.json"
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        documents = []
        chunks = []

        try:
            for document in gold["documents"]:
                document_chunks = _chunk_text(document["path"], document["content"])
                documents.append({"path": document["path"], "size": len(document["content"])})
                chunks.extend(document_chunks)

            ingested = store.ingest(collection, documents, chunks)
            self.assertEqual(ingested["changed_chunk_count"], len(chunks))

            hits = 0
            for case in gold["cases"]:
                results = store.query(collection, case["question"], limit=3)
                hits += int(any(result["path"] in case["expected_paths"] for result in results))
            self.assertEqual(hits / len(gold["cases"]), 1.0)

            result = store.query(collection, gold["cases"][0]["question"], limit=3)[0]
            self.assertTrue(result["metadata"]["parent_chunk_id"])
            self.assertIn("Cache Design", result["context_content"])

            with store._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT count(*), min(vector_dims(embedding)), max(vector_dims(embedding)), min(embedding_source)
                    FROM rag_chunk WHERE collection = %s
                    """,
                    (collection,),
                )
                stored_count, min_dim, max_dim, embedding_source = cur.fetchone()
            self.assertEqual(stored_count, len(chunks))
            self.assertEqual((min_dim, max_dim), (store.dimension, store.dimension))
            self.assertEqual(embedding_source, "hash_fallback")
        finally:
            with store._connect() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM rag_chunk WHERE collection = %s", (collection,))
                cur.execute("DELETE FROM rag_document WHERE collection = %s", (collection,))
                conn.commit()


if __name__ == "__main__":
    unittest.main()
