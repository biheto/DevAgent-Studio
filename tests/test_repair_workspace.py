import json
from pathlib import Path

import pytest

from app.agents import repair_loop as repair


def test_exact_patch_validation_and_conflict_protection(tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_AGENT_REPAIR_DIR", str(tmp_path / "repairs"))
    project = tmp_path / "project"
    project.mkdir()
    source = project / "maths.py"
    source.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (project / "test_maths.py").write_text("from maths import add\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8")
    assert not repair.validate(project, "pytest")["passed"]
    monkeypatch.setattr(repair.llm_provider, "generate_with_status", lambda *_args, **_kwargs: {
        "text": json.dumps({"reason": "addition", "edits": [{"path": "maths.py", "old_text": "a - b", "new_text": "a + b"}]}), "answer_source": "llm",
    })
    result = repair.attempt_repair({"task_id": "task_test", "goal": "fix add", "project_path": str(project),
        "repair_options": {"validation": "pytest"}, "code_review": {"findings": [{"path": "maths.py", "line": 2}]}})
    assert result["validation"]["passed"] and result["validation"]["kind"] == "pytest"
    assert "a - b" in source.read_text()
    source.write_text("# user's new change\n" + source.read_text(), encoding="utf-8")
    with pytest.raises(ValueError, match="Original file changed"):
        repair.apply_candidate("task_test")
    assert "user's new change" in source.read_text()


@pytest.mark.parametrize("name", ["../outside.py", "C:outside.py", ".env", "tests/../../outside.py", "secrets.py", ".git/config", "requirements.lock"])
def test_repair_path_rejects_escape_and_sensitive_files(tmp_path, name):
    with pytest.raises(ValueError):
        repair.safe_file(tmp_path, name)


@pytest.mark.parametrize("name", ["conftest.py", "test_app.py", "app_test.py", "test/app.py", "__tests__/app.ts", "app.test.ts", "app.spec.ts", "pyproject.toml"])
def test_test_harness_cannot_be_edited_to_hide_failures(name):
    assert not repair.editable_source(name)


def test_tampered_candidate_cannot_be_applied(tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_AGENT_REPAIR_DIR", str(tmp_path / "repairs"))
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(repair.llm_provider, "generate_with_status", lambda *_a, **_kw: {"text": json.dumps({"reason": "change", "edits": [{"path": "app.py", "old_text": "x = 1", "new_text": "x = 2"}]})})
    result = repair.attempt_repair({"task_id": "task_test", "goal": "fix", "project_path": str(project),
        "repair_options": {"validation": "python_compile"}, "code_review": {"findings": [{"path": "app.py"}]}})
    (Path(result["workspace"]) / "app.py").write_text("changed after validation", encoding="utf-8")
    with pytest.raises(ValueError, match="Candidate changed"):
        repair.apply_candidate("task_test")
