"""Reproducible offline ablation; explicitly reports fallback embeddings and corpus provenance."""
from __future__ import annotations

import json
import math
import statistics
import time
from pathlib import Path

from app.persistence.rag_store import EmbeddingProvider, _rank_hybrid, _tokens, _bm25_scores


def evaluate(path: str | Path, k: int = 3) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    embedding = EmbeddingProvider(1536)
    documents = [{**doc, "chunk_id": doc["path"] + "#1", "vector": embedding._hash_embedding(doc["content"])} for doc in data["documents"]]
    report = {"provenance": data["provenance"], "case_count": len(data["cases"]), "document_count": len(documents),
              "embedding_source": "hash_fallback", "dimension": 1536, "k": k, "llm_calls": 0, "api_token_cost": 0, "modes": {}}
    for mode in ("lexical", "vector", "hybrid"):
        recall, precision, reciprocal, no_answer, acl, latencies, failures = [], [], [], [], [], [], []
        for case in data["cases"]:
            start = time.perf_counter()
            vector = embedding._hash_embedding(case["question"])
            visible = [doc for doc in documents if "*" in doc["acl"] or case["actor_id"] in doc["acl"]]
            rows = [{**doc, "vector_score": sum(a*b for a,b in zip(vector,doc["vector"]))} for doc in visible]
            if mode == "hybrid":
                ranked = _rank_hybrid(case["question"], rows, "vector_score")
            elif mode == "vector":
                ranked = sorted([row for row in rows if row["vector_score"] >= .35], key=lambda row: row["vector_score"], reverse=True)
            else:
                scores = _bm25_scores(_tokens(case["question"]), [_tokens(row["content"]) for row in rows])
                ranked = [row for row, score in sorted(zip(rows, scores), key=lambda pair: pair[1], reverse=True) if score > 0]
            paths = [row["path"] for row in ranked[:k]]
            expected = set(case["expected_paths"])
            if expected:
                hits = len(expected.intersection(paths))
                recall.append(hits / len(expected))
                precision.append(hits / k)
                reciprocal.append(next((1/(i+1) for i,p in enumerate(paths) if p in expected), 0))
                passed = bool(hits)
            else:
                passed = not paths
                (acl if case["category"] == "acl" else no_answer).append(int(passed))
            if not passed:
                failures.append(case["id"])
            latencies.append((time.perf_counter()-start)*1000)
        ordered = sorted(latencies)
        report["modes"][mode] = {
            "recall_at_k": round(statistics.mean(recall), 4), "precision_at_k": round(statistics.mean(precision), 4),
            "mrr": round(statistics.mean(reciprocal), 4), "no_answer_accuracy": round(statistics.mean(no_answer), 4),
            "acl_abstention_accuracy": round(statistics.mean(acl), 4), "p95_ms": round(ordered[math.ceil(len(ordered)*.95)-1], 3),
            "failed_cases": failures,
        }
    return report
