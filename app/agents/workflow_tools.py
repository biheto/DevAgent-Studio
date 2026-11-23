from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_inline_workflow_active: ContextVar[bool] = ContextVar("inline_workflow_active", default=False)


def run_workflow(
    workflow_name: str, input_text: str, nodes: list[dict[str, Any]],
    *, edges: list[dict[str, Any]] | None = None, project_path: str | None = None,
    max_files: int = 100,
) -> dict[str, Any]:
    """Execute inline skills through the real compiler, with bounded scope.

    Pausable/nested workflows belong to the persisted task API; an inline skill must
    never claim completion while a child graph is awaiting a decision.
    """
    if _inline_workflow_active.get():
        raise ValueError("Recursive workflow.run calls are not supported; use a top-level task workflow.")
    nodes = nodes or [
        {"id": "plan", "type": "planner", "name": "任务规划", "config": {}},
        {"id": "report", "type": "reporter", "name": "结果汇总", "config": {}},
    ]
    if len(nodes) > 20:
        raise ValueError("Inline workflow.run supports at most 20 nodes.")
    if not 1 <= max_files <= 5000:
        raise ValueError("max_files must be between 1 and 5000.")
    if any(node.get("type") == "human_review" or (node.get("config") or {}).get("confirm_before_run") for node in nodes):
        raise ValueError("Human review requires a persisted task; run this workflow from the task workbench.")
    # Lazy import avoids the built-in skill registry/compiler import cycle.
    from app.graphs.workflow_compiler import run_compiled_workflow
    token = _inline_workflow_active.set(True)
    try:
        result = run_compiled_workflow(workflow_name, input_text, nodes, edges, {
            "project_path": project_path, "max_files": max_files, "require_human_review": False,
        })
        latest = {event.get("node"): event.get("status") for event in result["events"]}
        if any(status in {"failed", "cancelled", "waiting_review"} for status in latest.values()):
            messages = [event.get("content", "") for event in result["events"] if event.get("status") == "failed"]
            raise RuntimeError("Inline workflow did not complete: " + "; ".join(messages))
        return {**result, "execution_status": "completed"}
    finally:
        _inline_workflow_active.reset(token)
