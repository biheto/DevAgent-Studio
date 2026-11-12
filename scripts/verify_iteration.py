"""Run offline tests with isolated databases and without reading developer secrets."""
from pathlib import Path
import os
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.path.insert(0, str(ROOT))
    with tempfile.TemporaryDirectory(prefix="iteration-tests-") as directory:
        os.chdir(directory)
        os.environ["APP_ENV_FILE"] = str(Path(directory) / "absent.env")
        os.environ["APP_ENV"] = "test"
        os.environ["VALUSee_DATA_DIR"] = directory
        for key in ("DATABASE_URL", "VALUSee_SQLITE_PATH", "RABBITMQ_URL",
                    "REDIS_URL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                    "VALUSee_NOTIFICATION_WEBHOOK_URL", "SMTP_HOST", "VALUSee_SMTP_HOST"):
            os.environ.pop(key, None)
        os.environ["DEV_AGENT_RAG_STORE"] = "sqlite"
        os.environ["DEV_AGENT_RAG_RERANKER"] = "off"
        import pytest
        args = sys.argv[1:] or [str(ROOT / "tests"), "-q"]
        if not any((ROOT / arg.split("::")[0]).exists() for arg in args if not arg.startswith("-")):
            args.insert(0, str(ROOT / "tests"))
        args = [str(ROOT / arg) if (ROOT / arg).exists() else arg for arg in args]
        try:
            if "--rag-eval" in sys.argv:
                import json
                from app.rag_evaluation import evaluate
                print(json.dumps(evaluate(ROOT / "tests/data/rag_reliability_gold.json"), indent=2, ensure_ascii=False))
                return 0
            return pytest.main(args + ["-o", "cache_dir=" + directory + "/pytest-cache"])
        finally:
            os.chdir(ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
