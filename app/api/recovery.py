"""Recovery endpoints kept separate from the general-purpose studio routes."""
from fastapi import APIRouter, HTTPException
import time

from app.harness.context import AgentExecutionContext
from app.harness.durable import load_run, RunBusyError
from app.harness.runtime import harness_runtime
from app.graphs.task_execution import runner_for
from app.persistence.sqlite_store import task_store
from app.harness.durable import journal
from app.harness.reconciliation import reconcile
from app.security import current_principal
from pydantic import BaseModel, Field
from typing import Any, Literal
from app.harness.budget import BudgetOptions, snapshot, update_budget, estimate
from app.harness.durable import run_lease
from app.schemas.studio import TaskRunRequest
from app.providers.llm_provider import llm_provider

router = APIRouter(prefix="/api/v1/tasks", tags=["Workflow Recovery"])


@router.post("/estimate")
def estimate_task(request: TaskRunRequest):
    return estimate(llm_provider, request.budget.model_dump(), max_files=request.max_files,
                    repair_rounds=request.repair_options.max_rounds if request.repair_options.enabled else 0)


@router.get("/{task_id}/budget")
def task_usage(task_id: str):
    try:
        return snapshot(task_id)
    except KeyError as exc:
        raise HTTPException(404, "Task has no budget ledger") from exc


@router.put("/{task_id}/budget")
def change_budget(task_id: str, request: BudgetOptions):
    if current_principal().role not in {"admin", "editor"}:
        raise HTTPException(403, "Editor role required")
    try:
        stored = load_run(task_id)
        if stored["status"] not in {"budget_paused", "failed", "waiting_review"}:
            raise HTTPException(409, "Only a paused task budget can be changed")
        with run_lease(task_id, stored["definition"], stored["input"]):
            return update_budget(task_id, request.model_dump())
    except KeyError as exc:
        raise HTTPException(404, "Task not found") from exc
    except RunBusyError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{task_id}/repair/apply")
def apply_repair(task_id: str):
    if current_principal().role not in {"admin", "editor"}:
        raise HTTPException(403, "Editor role required")
    try:
        stored = load_run(task_id)
        artifacts = task_store.get_artifacts(task_id)
        result = next((a["content"] for a in reversed(artifacts) if a["artifact_type"] == "graph_result"), {})
        if stored["status"] != "completed" or (result.get("repair_result") or {}).get("status") != "validated" or result.get("review_decision") != "approved":
            raise HTTPException(409, "A validated candidate and completed human approval are required")
        from app.agents.repair_loop import apply_candidate
        with run_lease(task_id, stored["definition"], stored["input"]):
            applied = apply_candidate(task_id)
            task_store.save_artifact(task_id, "repair_application", "applied", {**applied, "actor": current_principal().actor_id})
            task_store.save_artifact(task_id, "graph_result", "result", {**result, "repair_result": {**result["repair_result"], "applied": True}})
            return applied
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(404, "Repair candidate not found") from exc
    except (ValueError, RunBusyError, FileExistsError) as exc:
        raise HTTPException(409, str(exc)) from exc


class ReconcileRequest(BaseModel):
    outcome: Literal["not_executed", "completed"]
    reason: str = Field(min_length=5)
    output: Any = None
    extra: dict = Field(default_factory=dict)


@router.get("/{task_id}/operations")
def operations(task_id: str):
    with journal() as conn:
        return [dict(row) for row in conn.execute("SELECT operation_id,node_id,status,attempts FROM node_operation WHERE run_id=?", (task_id,)).fetchall()]


@router.post("/{task_id}/operations/{operation_id}/reconcile")
def reconcile_tool(task_id: str, operation_id: str, request: ReconcileRequest):
    try:
        reconcile(task_id, operation_id, request.outcome, request.reason, current_principal().actor_id, request.output, request.extra)
    except KeyError as exc:
        raise HTTPException(404, "Operation not found") from exc
    except (ValueError, RunBusyError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"operation_id": operation_id, "outcome": request.outcome}


@router.post("/{task_id}/recover")
def recover_task(task_id: str):
    try:
        stored = load_run(task_id)
    except KeyError as exc:
        raise HTTPException(404, "No durable workflow was found.") from exc
    if stored["status"] == "waiting_review":
        raise HTTPException(409, "Use the human review endpoint to approve the pending interrupt.")
    if stored["status"] in {"completed", "cancelled"}:
        raise HTTPException(409, "A terminal task cannot be recovered; create a new task instead.")
    if (stored["lease_until"] or 0) > time.time():
        raise HTTPException(409, "Workflow is already executing.")
    context = AgentExecutionContext(goal=stored["input"].get("goal", ""), task_id=task_id)
    try:
        return harness_runtime.run_graph(context, runner_for(stored), {**stored["input"], **stored["definition"], "durable_action": "retry"})
    except RunBusyError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{task_id}/recovery")
def recovery_status(task_id: str):
    try:
        stored = load_run(task_id)
    except KeyError as exc:
        raise HTTPException(404, "No durable workflow was found.") from exc
    return {"task_id": task_id, "status": stored["status"], "definition_hash": stored["definition_hash"],
            "lease_until": stored["lease_until"], "task": task_store.get_task(task_id)}
