"""Recovery endpoints kept separate from the general-purpose studio routes."""
from fastapi import APIRouter, HTTPException
import time

from app.harness.context import AgentExecutionContext
from app.harness.durable import load_run, RunBusyError
from app.harness.runtime import harness_runtime
from app.graphs.workflow_compiler import run_task_workflow
from app.persistence.sqlite_store import task_store
from app.harness.durable import journal
from app.harness.reconciliation import reconcile
from app.security import current_principal
from pydantic import BaseModel, Field
from typing import Any, Literal

router = APIRouter(prefix="/api/v1/tasks", tags=["Workflow Recovery"])


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
    if (stored["lease_until"] or 0) > time.time():
        raise HTTPException(409, "Workflow is already executing.")
    context = AgentExecutionContext(goal=stored["input"].get("goal", ""), task_id=task_id)
    try:
        return harness_runtime.run_graph(context, run_task_workflow, {**stored["input"], **stored["definition"], "durable_action": "retry"})
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
