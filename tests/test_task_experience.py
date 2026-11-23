import asyncio
import json
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.api.task_stream import task_stream_response
from app.graphs import studio_graphs
from app.harness import runtime
from app.harness.events import AgentEvent
from app.main import app
from app.persistence import sqlite_store
from app.persistence.sqlite_store import SQLiteTaskStore
from app.schemas.studio import BusinessScenarioRequest, TaskRunRequest, TaskRunResponse
from app.security import Principal, _principal, current_principal


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = SQLiteTaskStore(tmp_path / "tasks.db")
    for module in (routes, runtime, sqlite_store, studio_graphs):
        monkeypatch.setattr(module, "task_store", store)
    monkeypatch.setenv("DEV_AGENT_AUTH_MODE", "local")
    monkeypatch.setenv("APP_ENV", "test")
    return store


@pytest.mark.parametrize("endpoint,runner,payload", [
    (routes.run_task_stream, "run_task_workflow", TaskRunRequest(goal="stream test")),
    (routes.run_collaboration_task_stream, "run_collaboration_task", TaskRunRequest(goal="stream test")),
    (routes.run_business_scenario_stream, "run_business_scenario", BusinessScenarioRequest(
        business_scenario="onboarding", goal="stream test", project_path=".")),
])
def test_events_arrive_before_execution_finishes(store, monkeypatch, endpoint, runner, payload):
    release = Event()
    finished = Event()
    identities = []

    def graph(state):
        identities.append(current_principal().actor_id)
        event = AgentEvent(state["task_id"], "workflow_node", "正在分析", node="analyze").to_dict()
        store.append_event(event)
        assert release.wait(5), "consumer never received the live node event"
        finished.set()
        return {"events": [{**event, "persisted": True}], "result": {"final_report": "# 分析结果"}}

    monkeypatch.setattr(routes, runner, graph)

    async def check():
        token = _principal.set(Principal("stream-operator", "admin"))
        response = await endpoint(payload)
        assert response.headers["x-accel-buffering"] == "no"
        events = []
        try:
            async for chunk in response.body_iterator:
                event = json.loads(chunk.removeprefix("data: "))
                events.append(event)
                if event.get("node") == "analyze":
                    assert not finished.is_set()
                    # Other coroutines continue to run while the graph is blocked.
                    await asyncio.wait_for(asyncio.sleep(0), timeout=0.2)
                    release.set()
        finally:
            release.set()
            _principal.reset(token)
        ids = [event["event_id"] for event in events if event.get("event_id")]
        assert len(ids) == len(set(ids))
        assert events[-2]["type"] == "task_result"
        assert events[-2]["final_report"] == "# 分析结果"
        assert events[-1]["type"] == "complete"
        assert store.get_task(events[0]["task_id"])["status"] == "completed"

    asyncio.run(check())
    assert identities == ["stream-operator"]


def test_disconnect_preserves_execution_and_saved_result(store):
    release = Event()
    finished = Event()

    def execute():
        context = runtime.harness_runtime.create_context("disconnect")

        def graph(_state):
            assert release.wait(5)
            return {"result": {"final_report": "saved after disconnect"}, "events": []}

        try:
            return TaskRunResponse(**runtime.harness_runtime.run_graph(context, graph, {}))
        finally:
            finished.set()

    async def check():
        iterator = task_stream_response(execute).body_iterator
        first = json.loads((await anext(iterator)).removeprefix("data: "))
        await iterator.aclose()
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
        task = store.get_task(first["task_id"])
        assert task["status"] == "completed"
        assert task["final_report"] == "saved after disconnect"

    asyncio.run(check())


def test_real_langgraph_node_events_cross_worker_contexts(store, monkeypatch, tmp_path):
    from app.graphs import workflow_compiler
    release = Event()
    monkeypatch.setenv("DEV_AGENT_CHECKPOINT_DB", str(tmp_path / "checkpoints.db"))

    def execute_node(node, *_args):
        assert release.wait(5), "node event was buffered until graph completion"
        return {"done": node["id"]}, {}

    monkeypatch.setattr(workflow_compiler, "_execute_node", execute_node)

    async def check():
        response = await routes.run_task_stream(TaskRunRequest(
            goal="real graph", nodes=[{"id": "work", "type": "agent"}], require_human_review=False))
        seen_running = False
        try:
            async for chunk in response.body_iterator:
                event = json.loads(chunk.removeprefix("data: "))
                if event.get("node") == "work" and event.get("status") == "running":
                    seen_running = True
                    release.set()
                if event.get("type") == "task_result":
                    assert event["status"] == "completed"
        finally:
            release.set()
        assert seen_running

    asyncio.run(check())


def test_streams_are_isolated_and_failures_are_explicit(store):
    def run(goal):
        context = runtime.harness_runtime.create_context(goal)
        def graph(_state):
            if goal == "failure":
                raise ValueError("injected failure")
            return {"result": {"final_report": goal}, "events": []}
        return TaskRunResponse(**runtime.harness_runtime.run_graph(context, graph, {}))

    async def collect(goal):
        return [json.loads(chunk.removeprefix("data: ")) async for chunk in
                task_stream_response(lambda: run(goal)).body_iterator]

    async def check():
        success, failure = await asyncio.gather(collect("success"), collect("failure"))
        assert {e["task_id"] for e in success if "task_id" in e}.isdisjoint(
            {e["task_id"] for e in failure if "task_id" in e})
        assert success[-2]["type"] == "task_result"
        assert failure[-1]["type"] == "complete"
        assert any(e["type"] == "error" and e["content"] == "injected failure" for e in failure)
        assert not any(e["type"] == "task_result" for e in failure)
        assert store.get_task(failure[0]["task_id"])["status"] == "failed"

    asyncio.run(check())


def test_collaboration_events_are_saved_at_each_step(store):
    task_id = uuid4().hex
    store.create_task(task_id, "collaboration", None, "running")
    result = studio_graphs._append_event({"task_id": task_id}, "planner", "planner", "规划完成")
    assert result[-1]["persisted"]
    assert store.get_events(task_id)[0]["content"] == "规划完成"


def test_history_pagination_filtering_and_lightweight_results(store):
    for index in range(25):
        store.create_task(f"task_{index}", f"审查 {index}", "/projects/demo", "completed" if index % 2 else "failed")
        store.update_task(f"task_{index}", "completed" if index % 2 else "failed", "long report" * 100)
    client = TestClient(app)
    first = client.get("/api/v1/tasks?limit=20").json()
    second = client.get("/api/v1/tasks?limit=20&offset=20").json()
    assert first["total"] == second["total"] == 25
    assert len(first["tasks"]) == 20 and len(second["tasks"]) == 5
    assert first["tasks"][0]["task_id"] == "task_24"  # stable ordering within one minute
    assert {t["task_id"] for t in first["tasks"]}.isdisjoint(t["task_id"] for t in second["tasks"])
    assert all("final_report" not in task and task["has_report"] for task in first["tasks"])
    filtered = client.get("/api/v1/tasks", params={"q": "审查 1", "status": "completed"}).json()
    assert filtered["total"] == 6
    assert client.get("/api/v1/tasks", params={"q": "/projects/demo"}).json()["total"] == 25
    assert client.get("/api/v1/tasks", params={"q": "task_24"}).json()["total"] == 1
    assert client.get("/api/v1/tasks?offset=100").json()["tasks"] == []
    for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"q": "x" * 201}):
        assert client.get("/api/v1/tasks", params=params).status_code == 422


@pytest.mark.parametrize("query", ["%", "_", "\\", "' OR 1=1 --"])
def test_history_search_treats_special_characters_literally(store, query):
    store.create_task("match", "literal " + query, None, "completed")
    store.create_task("other", "ordinary text", None, "completed")
    result = store.search_tasks(q=query)
    assert result["total"] == 1 and result["tasks"][0]["task_id"] == "match"


def test_report_download_preserves_markdown_and_rejects_missing_reports(store):
    client = TestClient(app)
    assert client.get("/api/v1/tasks/missing/report/download").status_code == 404
    store.create_task("task_report", "review", None, "running")
    assert client.get("/api/v1/tasks/task_report/report/download").status_code == 409
    report = "# 中文报告\n\n- 风险：低\n\n```python\nprint('你好')\n```\n"
    store.update_task("task_report", "completed", report)
    response = client.get("/api/v1/tasks/task_report/report/download")
    assert response.status_code == 200 and response.text == report
    assert response.headers["content-type"].startswith("text/markdown")
    assert "attachment" in response.headers["content-disposition"]
    assert "devagent-task_report.md" in response.headers["content-disposition"]
