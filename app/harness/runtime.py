from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.harness.context import AgentExecutionContext
from app.persistence.sqlite_store import task_store


GraphRunner = Callable[[dict[str, Any]], dict[str, Any]]


class HarnessRuntime:
    def create_context(
        self,
        goal: str,
        project_path: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> AgentExecutionContext:
        context = AgentExecutionContext(goal=goal, project_path=project_path, variables=variables or {})
        task_store.create_task(context.task_id, goal, project_path, "created")
        event = context.events.emit(context.task_id, "task", "任务已创建", status="created")
        task_store.append_event(event.to_dict())
        return context

    def run_graph(self, context: AgentExecutionContext, graph_runner: GraphRunner, input_state: dict[str, Any]) -> dict[str, Any]:
        context.status = "running"
        task_store.update_task(context.task_id, "running")
        event = context.events.emit(context.task_id, "task", "任务开始执行", status="running")
        task_store.append_event(event.to_dict())
        try:
            result = graph_runner({**input_state, "task_id": context.task_id, "events": context.events.to_list()})
            final_report = _extract_final_report(result)
            public_result = _public_result(result)
            context.status = _resolve_task_status(public_result)

            task_store.save_artifact(context.task_id, "graph_result", "result", public_result)
            if public_result.get("resume_checkpoint"):
                task_store.save_artifact(context.task_id, "workflow_checkpoint", "resume", public_result["resume_checkpoint"])
            governance_artifact = _governance_artifact(public_result)
            if governance_artifact:
                task_store.save_artifact(context.task_id, "governance", "governance", governance_artifact)
            task_store.update_task(context.task_id, context.status, final_report)

            graph_events = result.get("events", [])
            for graph_event in graph_events:
                if graph_event.get("task_id") and not graph_event.get("persisted"):
                    task_store.append_event(graph_event)

            final_message = {"waiting_review": "等待人工审核", "failed": "任务执行失败，可恢复", "cancelled": "任务已取消", "partial_success": "任务部分成功"}.get(context.status, "任务执行完成")
            event = context.events.emit(context.task_id, "task", final_message, status=context.status)
            task_store.append_event(event.to_dict())
            combined_events = context.events.to_list()[:-1] + graph_events + [event.to_dict()]
            return {
                "task_id": context.task_id,
                "status": context.status,
                "events": combined_events,
                "result": public_result,
            }
        except Exception as exc:
            context.status = "failed"
            task_store.update_task(context.task_id, "failed")
            event = context.events.emit(context.task_id, "error", str(exc), status="failed")
            task_store.append_event(event.to_dict())
            raise


harness_runtime = HarnessRuntime()


def _extract_final_report(result: dict[str, Any]) -> str | None:
    if "final_report" in result:
        return result["final_report"]
    nested = result.get("result")
    if isinstance(nested, dict):
        return nested.get("final_report") or nested.get("report_markdown")
    return None


def _public_result(result: dict[str, Any]) -> dict[str, Any]:
    nested = result.get("result")
    if isinstance(nested, dict):
        return nested
    return {
        "final_report": result.get("final_report"),
        "events": result.get("events", []),
    }


def _governance_artifact(public_result: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "governance",
        "risk_level",
        "review_required",
        "next_actions",
        "suggestion_records",
        "suggestions",
    ]
    artifact = {key: public_result.get(key) for key in keys if public_result.get(key) not in (None, [], {})}
    if "governance" not in artifact and any(key in artifact for key in ["risk_level", "review_required", "next_actions"]):
        artifact["governance"] = {
            "risk_level": artifact.get("risk_level", "low"),
            "review_required": bool(artifact.get("review_required")),
            "next_actions": artifact.get("next_actions", []),
            "suggestion_record_count": len(artifact.get("suggestion_records", [])),
        }
    return artifact


def _resolve_task_status(public_result: dict[str, Any]) -> str:
    explicit = public_result.get("execution_status")
    if explicit in {"failed", "partial_success", "cancelled", "waiting_review", "completed"}:
        return explicit
    latest = {}
    for event in public_result.get("workflow_events", public_result.get("events", [])):
        latest[event.get("node")] = event.get("status")
    if "failed" in latest.values():
        return "failed"
    review_packet = public_result.get("human_review_packet")
    if public_result.get("human_review_required") and review_packet:
        return "waiting_review"
    return "completed"
