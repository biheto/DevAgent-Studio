from pathlib import Path
from app.rag_evaluation import evaluate


def test_reproducible_bilingual_retrieval_ablation():
    result = evaluate(Path(__file__).parent / "data/rag_reliability_gold.json")
    assert result["case_count"] == 100
    assert result["embedding_source"] == "hash_fallback"
    assert result["llm_calls"] == 0
    assert result["modes"]["hybrid"]["recall_at_k"] >= 0.9
    assert result["modes"]["hybrid"]["no_answer_accuracy"] == 1
    assert result["modes"]["hybrid"]["acl_abstention_accuracy"] == 1
