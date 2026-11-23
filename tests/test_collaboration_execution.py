import json
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import routes, recovery
from app.graphs import studio_graphs as graphs
from app.graphs.collaboration_policy import create_plan
from app.harness import runtime
from app.harness.durable import load_run
from app.main import app
from app.persistence import sqlite_store
from app.persistence.sqlite_store import SQLiteTaskStore
from app.schemas.studio import TaskRunRequest


def test_parallel_workers_overlap_and_merge_outputs(rig, monkeypatch):
    barrier = threading.Barrier(3, timeout=5)
    for graph in (graphs.project_analyzer_graph, graphs.code_review_graph, graphs.rag_process_graph):
        original = graph.invoke
        def invoke(value, original=original):
            barrier.wait()
            return original(value)
        monkeypatch.setattr(graph, "invoke", invoke)
    response = routes.run_collaboration_task_api(TaskRunRequest(goal="review", project_path="fixture", parallel_workers=True))
    assert response.status == "waiting_review"
    assert set(response.result["completed_agents"]) == set(rig.agents)
    events = response.result["events"]
    assert len({event["event_id"] for event in events}) == len(events)
    assert sum(event["node"] == "supervisor" for event in events) == 1


def test_parallel_recovery_does_not_repeat_successful_siblings(rig):
    rig.fail_agent = "code_reviewer"
    response = routes.run_collaboration_task_api(TaskRunRequest(goal="review", project_path="fixture", parallel_workers=True))
    assert response.status == "failed"
    resumed = rig.client.post(f"/api/v1/tasks/{response.task_id}/recover")
    assert resumed.status_code == 200 and resumed.json()["status"] == "waiting_review"
    assert rig.calls.count("code_reviewer") == 2
    assert rig.calls.count("project_analyzer") == rig.calls.count("rag_processor") == 1


def test_parallel_workers_share_task_budget_context(rig, monkeypatch):
    from app.harness import budget
    provider = SimpleNamespace(_read_env_file=lambda: {}, _agent_env_key=lambda m: m, _read_env_value=lambda *_: "")
    for graph in (graphs.project_analyzer_graph, graphs.code_review_graph, graphs.rag_process_graph):
        original = graph.invoke
        def invoke(value, original=original):
            assert budget.active_task.get()
            budget.reserve(provider, "worker", "test", "x")
            return original(value)
        monkeypatch.setattr(graph, "invoke", invoke)
    response = routes.run_collaboration_task_api(TaskRunRequest(goal="review", project_path="fixture", parallel_workers=True, budget={"max_tokens": 2400}))
    assert response.status == "budget_paused"
    assert budget.snapshot(response.task_id)["calls"] == 1


def test_repair_replans_from_failed_validation_and_applies_only_after_review(rig, tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setenv("DEV_AGENT_REPAIR_DIR", str(tmp_path / "repairs"))
    project = tmp_path / "project"
    project.mkdir()
    source = project / "answer.py"
    source.write_text("def answer():\n    return 1\n", encoding="utf-8")
    original = graphs.llm_provider.generate_with_status
    repair_calls = []
    def generate(system, prompt, fallback, **options):
        if options["agent"] != "repairer":
            return original(system, prompt, fallback, **options)
        data = json.loads(prompt)
        repair_calls.append(data)
        old = "return 1" if len(repair_calls) == 1 else "return ("
        new = "return (" if len(repair_calls) == 1 else "return 2"
        return {"text": json.dumps({"reason": "fix answer", "edits": [{"path": "answer.py", "old_text": old, "new_text": new}]}), "answer_source": "llm"}
    monkeypatch.setattr(graphs.llm_provider, "generate_with_status", generate)
    monkeypatch.setattr(graphs.code_review_graph, "invoke", lambda state: {"result": {
        "score": 95, "findings": [] if "return 2" in (Path(state["project_path"]) / "answer.py").read_text() else
        [{"path": "answer.py", "line": 2, "severity": "high", "message": "wrong answer"}],
    }})
    response = routes.run_collaboration_task_api(TaskRunRequest(goal="fix answer", project_path=str(project), parallel_workers=True,
        repair_options={"enabled": True, "max_rounds": 2}))
    assert response.status == "waiting_review"
    assert response.result["repair_result"]["status"] == "validated"
    assert len(repair_calls) == 2 and not repair_calls[1]["previous_validation"]["passed"]
    assert [r["status"] for r in response.result["repair_history"]] == ["validation_failed", "validated"]
    assert "return 1" in source.read_text()
    assert rig.client.post(f"/api/v1/tasks/{response.task_id}/repair/apply").status_code == 409
    assert rig.client.post(f"/api/v1/tasks/{response.task_id}/approve", json={}).status_code == 200
    applied = rig.client.post(f"/api/v1/tasks/{response.task_id}/repair/apply")
    assert applied.status_code == 200, applied.text
    assert "return 2" in source.read_text()
    assert rig.client.post(f"/api/v1/tasks/{response.task_id}/repair/apply").status_code == 200


def test_budget_pause_and_recovery_use_persisted_ledger(rig, monkeypatch):
    from app.providers.llm_provider import LLMProvider
    from app.harness.budget import snapshot
    from types import SimpleNamespace
    calls = []
    def invoke(messages):
        calls.append(messages)
        text = json.dumps({"steps": [{"agent": "code_reviewer", "objective": "review"}]}) if "规划器" in messages[0][1] else "report"
        return SimpleNamespace(content=text, usage_metadata={"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(graphs.llm_provider, "generate_with_status", LLMProvider.generate_with_status.__get__(graphs.llm_provider))
    monkeypatch.setattr(graphs.llm_provider, "_config", lambda *_: {"api_key": "fake", "model": "test", "base_url": ""})
    monkeypatch.setattr(graphs.llm_provider, "_resolve_prompt", lambda _a, version, system, **_: (system, version))
    monkeypatch.setattr(graphs.llm_provider, "_build_chat_openai", lambda *_: SimpleNamespace(invoke=invoke))
    monkeypatch.setattr(graphs.llm_provider, "_save_trace", lambda **_: None)
    response = routes.run_collaboration_task_api(TaskRunRequest(goal="review", project_path="fixture", budget={"max_tokens": 1}))
    assert response.status == "budget_paused" and not calls
    updated = rig.client.put(f"/api/v1/tasks/{response.task_id}/budget", json={"max_tokens": 100000})
    assert updated.status_code == 200
    resumed = rig.client.post(f"/api/v1/tasks/{response.task_id}/recover")
    assert resumed.json()["status"] == "waiting_review"
    assert snapshot(response.task_id)["reported_tokens"] == 150
    assert rig.client.post(f"/api/v1/tasks/{response.task_id}/approve", json={}).status_code == 200
    assert snapshot(response.task_id)["reported_tokens"] == 300


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DEV_AGENT_AUTH_MODE", "local")
    monkeypatch.setenv("DEV_AGENT_CHECKPOINT_DB", str(tmp_path / "checkpoints.db"))
    store = SQLiteTaskStore(tmp_path / "tasks.db")
    for module in (routes, recovery, graphs, runtime, sqlite_store):
        monkeypatch.setattr(module, "task_store", store)
    state = SimpleNamespace(
        agents=["project_analyzer", "code_reviewer", "rag_processor"], calls=[], report_calls=0,
        plan_text=None, fail_agent=None, report_prompt="",
        review={"score": 95, "findings": [], "suggestion_records": [], "risks": [], "suggestions": []},
    )

    def generate(system, prompt, fallback, **options):
        if options["agent"] == "planner":
            text = state.plan_text or json.dumps({"steps": [{"agent": agent, "objective": f"Inspect with {agent}"} for agent in state.agents]})
        else:
            state.report_calls += 1
            state.report_prompt = prompt
            text = fallback
        return {"text": text, "answer_source": "llm", "fallback_used": False, "trace_id": "test-trace"}

    def worker(agent, result):
        state.calls.append(agent)
        if state.fail_agent == agent:
            state.fail_agent = None
            raise TimeoutError("injected worker failure")
        return result()

    monkeypatch.setattr(graphs.llm_provider, "generate_with_status", generate)
    # A model saying "safe" must not override the deterministic risk gate.
    monkeypatch.setattr(graphs.llm_provider, "generate", lambda *args, **kwargs: "模型认为无需审核。")
    monkeypatch.setattr(graphs.project_analyzer_graph, "invoke", lambda _: worker("project_analyzer", lambda: {
        "scan": {"project_name": "fixture", "files": [{"path": "app.py"}]}, "tech_stack": ["Python"],
        "modules": ["app"], "quality_score": 90, "risks": [], "suggestions": [],
    }))
    monkeypatch.setattr(graphs.code_review_graph, "invoke", lambda _: worker("code_reviewer", lambda: {"result": state.review}))
    monkeypatch.setattr(graphs.rag_process_graph, "invoke", lambda _: worker("rag_processor", lambda: {
        "result": {"documents": [{"path": "README.md"}], "chunks": [{"content": "fixture"}], "keywords": ["fixture"]},
    }))
    state.store, state.client = store, TestClient(app)
    return state


def start(rig, *, review=True, goal="Inspect project"):
    return routes.run_collaboration_task_api(TaskRunRequest(goal=goal, project_path="fixture", require_human_review=review))


def test_plan_selects_and_orders_actual_workers(rig):
    rig.agents = ["rag_processor", "project_analyzer"]
    response = start(rig, review=False)
    assert rig.calls == rig.agents
    assert response.status == "completed"
    assert response.result["completed_agents"] == rig.agents
    assert [item["agent"] for item in response.result["worker_results"]] == rig.agents
    assert response.result["risk_level"] == "unknown"
    assert "未执行代码审查" in response.result["final_report"]
    assert "问题数量：0" not in response.result["final_report"]
    assert "code_reviewer[" not in response.result["mermaid"]
    assert response.result["planning"]["source"] == "llm"


@pytest.mark.parametrize("bad_plan", [
    "not JSON", '{"steps":[]}',
    '{"steps":[{"agent":"shell","objective":"execute a command"}]}',
    '{"steps":[{"agent":"code_reviewer","objective":"review","command":"rm"}]}',
    '{"steps":[{"agent":"code_reviewer","objective":"one"},{"agent":"code_reviewer","objective":"two"}]}',
])
def test_invalid_model_plans_fall_back_to_validated_scope(rig, bad_plan):
    rig.plan_text = bad_plan
    response = start(rig, review=False, goal="仅审查代码")
    assert rig.calls == ["code_reviewer"]
    assert response.result["planning"]["source"] == "fallback"
    assert response.result["planning"]["fallback_reason"]
    assert response.status == "completed"


def test_business_scenarios_keep_their_promised_coverage(rig):
    rig.agents = ["rag_processor"]
    plan, metadata = create_plan("project onboarding", "fixture", "onboarding")
    assert {step["agent"] for step in plan["steps"]} == {"project_analyzer", "code_reviewer", "rag_processor"}
    assert metadata["source"] == "fallback"


def test_disabled_model_is_explicitly_recorded_as_fallback(rig, monkeypatch):
    monkeypatch.setattr(graphs.llm_provider, "generate_with_status", lambda _system, _prompt, fallback, **_kwargs: {
        "text": fallback, "answer_source": "fallback", "fallback_used": True, "trace_id": "offline-trace",
        "error_message": "LLM is disabled",
    })
    result = start(rig, review=False, goal="审查代码").result
    assert result["planning"]["source"] == "fallback"
    assert result["report_generation"]["source"] == "fallback"
    assert result["report_generation"]["trace_id"] == "offline-trace"


def test_benchmark_evaluates_the_gate_without_implicitly_approving(rig, monkeypatch):
    from app import benchmark_runner
    monkeypatch.setattr(benchmark_runner, "_append_case_result", lambda _run, _case, _iteration, status, _started, _args, output, _error, **_kw: {"status": status, "output": output})
    result = benchmark_runner._run_single_collaboration_case("run", {"arguments": {
        "goal": "review", "project_path": "fixture", "require_human_review": True,
    }}, 0, "benchmark")
    assert result["status"] == "completed"
    assert result["output"]["evaluation_target"] == "review_gate"
    assert not result["output"]["report_evaluated"]
    assert rig.report_calls == 0


def test_review_really_pauses_and_approval_resumes_without_replaying_workers(rig):
    first = start(rig)
    assert first.status == "waiting_review"
    assert first.result["final_report"] == "" and rig.report_calls == 0
    assert "不是正式报告" in first.result["draft_report"]
    assert first.result["resume_checkpoint"]["graph_type"] == "collaboration"
    calls = list(rig.calls)
    # Reconstruct the graph from persisted storage through the actual HTTP review route.
    approved = rig.client.post(f"/api/v1/tasks/{first.task_id}/approve", json={"comment": "核对了范围，请突出风险"})
    assert approved.status_code == 200 and approved.json()["status"] == "completed"
    assert rig.calls == calls and rig.report_calls == 1
    assert "核对了范围" in rig.report_prompt
    assert rig.store.get_task(first.task_id)["final_report"]
    assert load_run(first.task_id)["status"] == "completed"
    assert rig.client.post(f"/api/v1/tasks/{first.task_id}/approve", json={}).status_code == 200
    assert rig.report_calls == 1
    assert rig.client.post(f"/api/v1/tasks/{first.task_id}/reject", json={}).status_code == 409


def test_rejection_is_terminal_and_never_calls_reporter(rig):
    first = start(rig)
    response = rig.client.post(f"/api/v1/tasks/{first.task_id}/reject", json={"comment": "证据不足"})
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    assert rig.report_calls == 0 and not rig.store.get_task(first.task_id)["final_report"]
    assert rig.client.post(f"/api/v1/tasks/{first.task_id}/reject", json={}).status_code == 200
    assert rig.client.post(f"/api/v1/tasks/{first.task_id}/approve", json={}).status_code == 409
    assert rig.client.post(f"/api/v1/tasks/{first.task_id}/revise", json={}).status_code == 409


def test_revision_keeps_the_gate_closed_and_recover_cannot_bypass_it(rig):
    first = start(rig)
    assert rig.client.post(f"/api/v1/tasks/{first.task_id}/revise", json={"comment": "需要补充证据"}).json()["status"] == "waiting_review"
    assert rig.client.post(f"/api/v1/tasks/{first.task_id}/recover").status_code == 409
    assert rig.report_calls == 0


@pytest.mark.parametrize("path_key", ["path", "file"])
def test_critical_evidence_forces_review_even_when_model_and_user_prefer_no_review(rig, path_key):
    rig.agents = ["code_reviewer"]
    rig.review["findings"] = [{"severity": "critical", path_key: "src/auth.py", "line": 17,
                               "category": "security", "message": "Credential exposure"}]
    first = start(rig, review=False)
    assert first.status == "waiting_review" and first.result["risk_level"] == "critical"
    assert rig.report_calls == 0
    assert first.result["evidence"][0]["file"] == "src/auth.py"
    assert first.result["evidence"][0]["line"] == 17
    assert first.result["code_review"]["finding_count"] == 1
    assert rig.client.post(f"/api/v1/tasks/{first.task_id}/approve", json={}).status_code == 200
    report = rig.store.get_task(first.task_id)["final_report"]
    assert "src/auth.py:17" in report and "finding-1" in report


def test_real_review_findings_reach_the_gate_and_report_evidence(rig, tmp_path):
    from app.agents.code_review_tools import review_project
    (tmp_path / "auth.py").write_text('password = "only-a-test-fixture"\n', encoding="utf-8")
    rig.review = review_project(str(tmp_path), 20)
    first = start(rig, review=False)
    assert first.status == "waiting_review" and first.result["risk_level"] == "critical"
    assert first.result["evidence"][0]["file"] == "auth.py"
    assert first.result["evidence"][0]["line"] == 1


def test_worker_failure_can_resume_without_repeating_completed_work(rig):
    rig.fail_agent = "code_reviewer"
    first = start(rig, review=False)
    assert first.status == "failed" and first.result["resume_checkpoint"]["durable"]
    assert rig.calls == ["project_analyzer", "code_reviewer"]
    assert rig.report_calls == 0
    assert rig.client.post(f"/api/v1/tasks/{first.task_id}/approve", json={}).status_code == 409
    recovered = rig.client.post(f"/api/v1/tasks/{first.task_id}/recover")
    assert recovered.status_code == 200 and recovered.json()["status"] == "completed"
    assert rig.calls == ["project_analyzer", "code_reviewer", "code_reviewer", "rag_processor"]
    assert rig.report_calls == 1


def test_business_artifacts_survive_resume_and_legacy_api_returns_a_real_task(rig):
    response = rig.client.post("/api/v1/business-scenarios/run", json={
        "business_scenario": "onboarding", "project_path": "fixture", "require_human_review": True,
    }).json()
    task_id = response["task_id"]
    assert response["status"] == "waiting_review"
    assert rig.client.post(f"/api/v1/tasks/{task_id}/approve", json={}).status_code == 200
    result = [a for a in rig.store.get_artifacts(task_id) if a["artifact_type"] == "graph_result"][-1]["content"]
    assert result["business_scenario"] == "onboarding" and result["onboarding_artifacts"]
    legacy = rig.client.post("/api/v1/agents/collaborate", json={
        "goal": "review", "project_path": "fixture", "require_human_review": True,
    })
    assert legacy.status_code == 200
    assert legacy.json()["task_id"] and legacy.json()["status"] == "waiting_review"
    assert not legacy.json()["final_report"]
