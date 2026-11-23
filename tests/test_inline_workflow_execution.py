import pytest

from app.agents.workflow_tools import run_workflow
from app.graphs import workflow_compiler
from app.skills.base import SkillContext
from app.skills.builtin import WorkflowRunSkill


def test_workflow_skill_reads_real_code_and_retains_findings(tmp_path):
    (tmp_path / "auth.py").write_text('password = "test-credential-fixture"\n# TODO: add validation\n', encoding="utf-8")
    output = WorkflowRunSkill().execute(SkillContext(agent_code="skill_console"), {
        "workflow_name": "real review", "project_path": str(tmp_path), "input_text": "review code",
        "nodes": [{"id": "review", "type": "agent", "config": {"agent_type": "code_reviewer"}},
                  {"id": "report", "type": "reporter"}],
    })
    assert output["execution_status"] == "completed"
    assert any(item["path"] == "auth.py" for item in output["outputs"]["review"]["findings"])
    assert output["final_report"] and output["events"]


def test_workflow_edges_determine_execution_instead_of_flat_string_steps(monkeypatch):
    called = []
    def execute(node, state, outputs):
        called.append(node["id"])
        return {"node": node["id"]}, {}
    monkeypatch.setattr(workflow_compiler, "_execute_node", execute)
    output = run_workflow("branch", "select a branch", [
        {"id": "start", "type": "planner"}, {"id": "left", "type": "agent"}, {"id": "right", "type": "agent"},
    ], edges=[{"source": "start", "target": "left", "condition": "on_status", "value": "completed"},
              {"source": "start", "target": "right", "condition": "on_status", "value": "failed"}])
    assert called == ["start", "left"] and output["execution_status"] == "completed"


def test_unknown_agents_and_failed_tools_cannot_report_success(monkeypatch):
    with pytest.raises((RuntimeError, ValueError), match="Unsupported Agent"):
        run_workflow("bad agent", "goal", [{"id": "x", "type": "agent", "config": {"agent_type": "imaginary"}}])
    def fail(*_):
        raise TimeoutError("tool failed")
    monkeypatch.setattr(workflow_compiler, "_execute_node", fail)
    with pytest.raises((RuntimeError, TimeoutError), match="tool failed"):
        run_workflow("failed", "goal", [{"id": "x", "type": "mcp_tool"}])


@pytest.mark.parametrize("nodes", [
    [{"id": "review", "type": "human_review"}],
    [{"id": "work", "type": "agent", "config": {"confirm_before_run": True}}],
])
def test_inline_review_redirects_to_persisted_tasks_before_any_work(monkeypatch, nodes):
    monkeypatch.setattr(workflow_compiler, "run_compiled_workflow", lambda *args: pytest.fail("must reject before execution"))
    with pytest.raises(ValueError, match="persisted task"):
        run_workflow("review", "goal", nodes)


def test_recursive_inline_calls_are_bounded_and_context_is_reset(monkeypatch):
    original = workflow_compiler._execute_node
    monkeypatch.setattr(workflow_compiler, "_execute_node", lambda *_: (run_workflow("recursive", "goal", []), {}))
    with pytest.raises((RuntimeError, ValueError), match="Recursive"):
        run_workflow("outer", "goal", [{"id": "x", "type": "planner"}])
    monkeypatch.setattr(workflow_compiler, "_execute_node", original)
    assert run_workflow("next", "goal", [])["execution_status"] == "completed"
