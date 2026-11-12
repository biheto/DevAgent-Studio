from uuid import uuid4

import pytest

from app.graphs import workflow_compiler as compiler
from app.harness.durable import execute_operation, journal, RunBusyError, run_lease, UncertainSideEffect
from app.harness.runtime import _resolve_task_status


@pytest.fixture(autouse=True)
def isolated_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_AGENT_CHECKPOINT_DB", str(tmp_path / "checkpoints.db"))


def payload():
    return {"task_id": uuid4().hex, "goal": "recovery test", "nodes": [
        {"id": "plan", "type": "planner"}, {"id": "work", "type": "agent"}, {"id": "report", "type": "reporter"}]}


def test_failure_preserves_progress_and_resume_does_not_rerun_completed_nodes(monkeypatch):
    calls = []
    def execute(node, *_args):
        calls.append(node["id"])
        if node["id"] == "work" and calls.count("work") == 1:
            raise TimeoutError("injected failure")
        return {"done": node["id"]}, {}
    monkeypatch.setattr(compiler, "_execute_node", execute)
    first = compiler.run_task_workflow(payload())["result"]
    assert _resolve_task_status(first) == "failed"
    assert first["resume_checkpoint"]["durable"]
    resumed = compiler.resume_task_workflow(first["resume_checkpoint"], "retry")["result"]
    assert _resolve_task_status(resumed) == "completed"
    assert calls == ["plan", "work", "work", "report"]
    compiler.resume_task_workflow(first["resume_checkpoint"], "retry")
    assert calls == ["plan", "work", "work", "report"]


def test_native_review_resume_and_repeated_approval():
    request = payload()
    request["nodes"][1]["type"] = "human_review"
    first = compiler.run_task_workflow(request)["result"]
    assert _resolve_task_status(first) == "waiting_review"
    checkpoint = first["resume_checkpoint"]
    result = compiler.resume_task_workflow(checkpoint)["result"]
    assert _resolve_task_status(result) == "completed"
    assert _resolve_task_status(compiler.resume_task_workflow(checkpoint)["result"]) == "completed"


def test_side_effect_uncertainty_blocks_replay_and_completed_result_is_cached():
    node = {"id": "tool", "type": "mcp_tool"}
    state = {"durable_run_id": "test"}
    count = []
    def failed():
        count.append(1)
        raise TimeoutError("after external side effect")
    with pytest.raises(TimeoutError):
        execute_operation(node, state, {}, failed)
    with pytest.raises(UncertainSideEffect):
        execute_operation(node, state, {}, failed)
    assert len(count) == 1
    safe_state = {"durable_run_id": "completed"}
    assert execute_operation(node, safe_state, {}, lambda: ({"ok": True}, {}))[0]["ok"]
    assert execute_operation(node, safe_state, {}, failed)[0]["ok"]


def test_run_lease_and_definition_are_enforced():
    with run_lease("one", {"version": 1}, {}):
        with pytest.raises(RunBusyError):
            with run_lease("one", {"version": 1}, {}):
                pass
    with pytest.raises(ValueError):
        with run_lease("one", {"version": 2}, {}):
            pass


def test_legacy_failed_events_cannot_be_reported_completed():
    assert _resolve_task_status({"workflow_events": [{"node": "x", "status": "failed"}]}) == "failed"


def test_failure_after_approval_is_recoverable(monkeypatch):
    request = payload()
    request["nodes"][0]["type"] = "human_review"
    original = compiler._execute_node
    def execute(node, *args):
        if node["id"] == "work":
            raise TimeoutError("after approval")
        return original(node, *args)
    monkeypatch.setattr(compiler, "_execute_node", execute)
    waiting = compiler.run_task_workflow(request)["result"]
    failed = compiler.resume_task_workflow(waiting["resume_checkpoint"])["result"]
    assert failed["execution_status"] == "failed"
    assert failed["resume_checkpoint"]["durable"]


def test_uncertain_operation_has_audited_manual_reconciliation():
    from app.harness.reconciliation import reconcile
    node, state = {"id": "tool", "type": "mcp_tool"}, {"durable_run_id": "manual"}
    def timeout():
        raise TimeoutError("unknown")
    with pytest.raises(TimeoutError):
        execute_operation(node, state, {}, timeout)
    with journal() as conn:
        operation_id = conn.execute("SELECT operation_id FROM node_operation WHERE run_id='manual'").fetchone()[0]
    reconcile("manual", operation_id, "completed", "Verified external receipt 42", "operator", {"receipt": 42})
    assert execute_operation(node, state, {}, timeout)[0] == {"receipt": 42}
    with journal() as conn:
        assert conn.execute("SELECT actor_id FROM operation_reconciliation WHERE operation_id=?", (operation_id,)).fetchone()[0] == "operator"


def test_process_death_recovers_from_durable_checkpoint(tmp_path, monkeypatch):
    import json
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("DEV_AGENT_RUN_LEASE_SECONDS", "2")
    script = '''
import os,sys,json
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from app.graphs import workflow_compiler as c
def execute(node,*args):
    with open('calls.txt','a') as output:
        output.write(node['id']+'\\n')
    if node['id']=='work' and sys.argv[2]=='crash':
        os._exit(17)
    return {'ok':node['id']},{}
c._execute_node=execute
result=c.run_task_workflow({'task_id':'crash-test','goal':'recover','nodes':[
    {'id':'plan','type':'planner'},{'id':'work','type':'agent'},{'id':'report','type':'reporter'}]})
print(json.dumps({'status':result['result']['execution_status']}))
'''
    first = subprocess.run([sys.executable, "-c", script, str(root), "crash"], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert first.returncode == 17, first.stderr
    time.sleep(2.2)
    second = subprocess.run([sys.executable, "-c", script, str(root), "resume"], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["status"] == "completed"
    assert (tmp_path / "calls.txt").read_text().splitlines() == ["plan", "work", "work", "report"]
