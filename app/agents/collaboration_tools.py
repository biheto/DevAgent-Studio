from __future__ import annotations

from typing import Any


def collaborate(goal: str, project_path: str | None, require_human_review: bool) -> dict[str, Any]:
    """Compatibility entry point; use the same persisted agents as the workbench."""
    from app.graphs.collaboration_runner import run_collaboration_task
    response = run_collaboration_task({"goal": goal, "project_path": project_path,
                                       "require_human_review": require_human_review})
    return {**response["result"], "task_id": response["task_id"], "status": response["status"]}
