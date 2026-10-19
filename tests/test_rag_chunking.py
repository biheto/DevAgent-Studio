from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.agents.rag_tools import _chunk_text, _token_count
from app.persistence.rag_store import SQLiteRagStore

GOLD_PATH = Path(__file__).parent / "data" / "rag_chunking_gold.json"


class RagChunkingTest(unittest.TestCase):
    def test_existing_sqlite_schema_migrates_metadata_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "legacy.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("CREATE TABLE rag_document (id INTEGER PRIMARY KEY, collection TEXT, path TEXT, size INTEGER, created_at TEXT)")
                conn.execute("CREATE TABLE rag_chunk (id INTEGER PRIMARY KEY, collection TEXT, chunk_id TEXT, path TEXT, content TEXT, created_at TEXT)")
                conn.commit()
            finally:
                conn.close()
            store = SQLiteRagStore(db_path)
            chunks = _chunk_text("readme.md", "# Existing\n\nThis document remains queryable after metadata migration.")
            store.ingest("legacy", [{"path": "readme.md", "size": 60}], chunks)
            result = store.query("legacy", "metadata migration", limit=1)[0]
            self.assertEqual(result["metadata"]["chunk_level"], "child")
            self.assertIn("Existing", result["context_content"])

    def test_markdown_heading_path_and_token_limits(self) -> None:
        text = "# Guide\n\n## Redis\n\n" + "Redis cache expiration improves latency. " * 80
        chunks = _chunk_text("guide.md", text, chunk_size=80, overlap=10)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(_token_count(item["content"]) <= 80 for item in chunks))
        self.assertTrue(all(item["metadata"]["chunking_strategy"] == "markdown_heading_paragraph_semantic" for item in chunks))
        self.assertTrue(any(item["metadata"].get("heading_path") == ["Guide", "Redis"] for item in chunks))
        self.assertTrue(all(item["metadata"]["chunk_level"] == "child" for item in chunks))
        self.assertTrue(all(item["metadata"]["parent_chunk_id"] for item in chunks))
        self.assertTrue(any(set(chunks[index]["content"].split()) & set(chunks[index + 1]["content"].split()) for index in range(len(chunks) - 1)))

    def test_code_chunks_keep_symbol_metadata(self) -> None:
        python = "class Worker:\n" + "\n".join(f"    def task_{i}(self):\n        return 'value_{i}'" for i in range(5))
        py_chunks = _chunk_text("worker.py", python, chunk_size=55, overlap=8)
        self.assertTrue(any(item["metadata"].get("symbol", "").startswith("Worker.task_") for item in py_chunks))
        self.assertTrue(all(_token_count(item["content"]) <= 55 for item in py_chunks))

        source = "\n\n".join(f"export function handler{i}() {{ return 'result{i}'; }}" for i in range(5))
        ts_chunks = _chunk_text("handlers.ts", source, chunk_size=50, overlap=6)
        self.assertTrue(any(item["metadata"].get("symbol", "").startswith("handler") for item in ts_chunks))
        self.assertTrue(all(_token_count(item["content"]) <= 50 for item in ts_chunks))

        java = "public class RetryPolicy {\n" + "\n".join(f"    public int retry{i}() {{ return {i}; }}" for i in range(5)) + "\n}"
        java_chunks = _chunk_text("RetryPolicy.java", java, chunk_size=50, overlap=6)
        self.assertTrue(any(item["metadata"].get("symbol", "").startswith("retry") for item in java_chunks))

    def test_recursive_fallback_and_gold_recall_at_3(self) -> None:
        gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
        chunks = []
        documents = []
        for document in gold["documents"]:
            document_chunks = _chunk_text(document["path"], document["content"])
            documents.append({"path": document["path"], "size": len(document["content"])})
            chunks.extend(document_chunks)
        self.assertTrue(all(_token_count(item["content"]) <= 350 for item in chunks))
        fallback = _chunk_text("notes.txt", "One paragraph. Another sentence.")
        self.assertEqual(fallback[0]["metadata"]["chunking_strategy"], "recursive_text_semantic")
        self.assertEqual(fallback[0]["metadata"]["semantic_embedding"], "local_token_hash")

        topic_shift = _chunk_text("topics.txt", "Redis cache stores product records. Redis cache expires hot keys.\n\nPostgreSQL advisory locks serialize invoice generation.")
        self.assertGreaterEqual(len(topic_shift), 1)
        self.assertTrue(any(item["metadata"].get("semantic_boundary") for item in topic_shift))

        with tempfile.TemporaryDirectory() as tmpdir:
            store = SQLiteRagStore(Path(tmpdir) / "rag.db")
            store.ingest("gold", documents, chunks)
            recalls = []
            for case in gold["cases"]:
                results = store.query("gold", case["question"], limit=3)
                hits = {item["path"] for item in results} & set(case["expected_paths"])
                recalls.append(len(hits) / len(case["expected_paths"]))
            recall_at_3 = sum(recalls) / len(recalls)
            self.assertEqual(recall_at_3, 1.0, f"Recall@3={recall_at_3:.2f}")

            markdown_result = store.query("gold", gold["cases"][0]["question"], limit=3)[0]
            self.assertIn("heading_path", markdown_result["metadata"])
            self.assertEqual(markdown_result["metadata"]["chunking_strategy"], "markdown_heading_paragraph_semantic")
            self.assertTrue(markdown_result["metadata"]["parent_chunk_id"])
            self.assertIn("Cache Design", markdown_result["context_content"])


if __name__ == "__main__":
    unittest.main()
