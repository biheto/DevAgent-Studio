from __future__ import annotations

from typing import Any

from app.graphs.studio_graphs import _build_collaboration_graph, collaboration_result, _build_collaboration_report
from app.graphs.collaboration_policy import evidence_markdown
from app.harness.durable import execute_graph
from app.harness.budget import task_budget, snapshot


COLLABORATION_NODES = [
    ("planner", "Planner"),
    ("project_analyzer", "Project Analyzer"),
    ("code_reviewer", "Code Reviewer"),
    ("rag_processor", "RAG Processor"),
    ("supervisor", "Supervisor"),
    ("human_review", "Human Review"),
    ("reporter", "Reporter"),
]


def run_collaboration_task(input_state: dict[str, Any]) -> dict[str, Any]:
    if not input_state.get("task_id"):
        from app.harness.runtime import harness_runtime
        context = harness_runtime.create_context(str(input_state.get("goal") or ""), input_state.get("project_path"))
        return harness_runtime.run_graph(context, run_collaboration_task, input_state)
    graph_state = {
        "goal": input_state.get("goal") or input_state.get("input_text") or "",
        "project_path": input_state.get("project_path"),
        "max_files": input_state.get("max_files", 500),
        "require_human_review": input_state.get("require_human_review", True),
        "task_id": input_state["task_id"],
        "business_scenario": input_state.get("business_scenario", ""),
        "review_comment": input_state.get("review_comment", ""),
        "events": [],
        "parallel_workers": input_state.get("parallel_workers", False),
        "repair_options": input_state.get("repair_options", {"enabled": False}),
        "budget": input_state.get("budget", {}),
        "original_project_path": input_state.get("original_project_path") or input_state.get("project_path"),
        "repair_round": 0,
    }
    if not graph_state["project_path"]:
        raise ValueError("project_path is required for project collaboration")
    definition = {"graph_type": "collaboration", "graph_version": 2, "workflow_name": "multi_agent_collaboration"}
    with task_budget(input_state["task_id"], graph_state["budget"]):
        state = execute_graph(_build_collaboration_graph, definition, graph_state, input_state.get("durable_action"))
    result = collaboration_result(state)
    execution_status = state["execution_status"]
    draft = ""
    if execution_status != "completed":
        draft = "# 待审核或未完成的协作摘要\n\n此摘要不是正式报告。\n\n" + _build_collaboration_report(state) + "\n\n" + evidence_markdown(state)
    public_result = {
        **result,
        "budget_usage": snapshot(input_state["task_id"]),
        "execution_status": execution_status,
        "execution_error": state.get("execution_error"),
        "resume_checkpoint": state.get("durable_checkpoint"),
        "draft_report": draft,
        "workflow_name": "multi_agent_collaboration",
        "mermaid": build_collaboration_mermaid(result.get("execution_plan"), result.get("human_review_required", True), state.get("parallel_workers", False), state.get("repair_options", {}).get("enabled", False)),
        "suggestions": _collaboration_suggestions(result),
        "suggestion_records": _collaboration_suggestion_records(result),
        "tool_calls": [],
        "agent_outputs": _agent_outputs(result),
        **_collaboration_governance(result),
        "workflow_events": _enrich_events(result.get("events", [])),
    }
    return {
        "result": public_result,
        "events": public_result["workflow_events"],
        "final_report": result.get("final_report"),
    }


def build_collaboration_mermaid(plan: dict | None = None, review: bool = True, parallel: bool = False, repair: bool = False) -> str:
    selected = ["planner", *[step["agent"] for step in (plan or {}).get("steps", [])], "supervisor"] if plan else [node for node, _ in COLLABORATION_NODES if node not in {"human_review", "reporter"}]
    if review:
        selected.append("human_review")
    selected.append("reporter")
    labels = dict(COLLABORATION_NODES)
    lines = ["flowchart LR"]
    for node_id in selected:
        lines.append(f"  {node_id}[{labels[node_id]}]")
    if parallel:
        for worker in selected[1:selected.index("supervisor")]:
            lines.extend([f"  planner --> {worker}", f"  {worker} --> supervisor"])
        tail = selected[selected.index("supervisor"):]
    else:
        tail = selected
    for source, target in zip(tail, tail[1:]):
        lines.append(f"  {source} -->|{'approve' if source == 'human_review' else 'next'}| {target}")
    if repair:
        lines.extend(["  supervisor -->|findings and rounds remaining| repair[Candidate repair and validation]",
                      "  repair -->|validation feedback| replan[Replan]"])
        for worker in (selected[1:selected.index("supervisor")] if parallel else selected[1:2]):
            lines.append(f"  replan --> {worker}")
    if review:
        lines.append("  human_review -->|reject| stopped[Stopped]")
    return "\n".join(lines)


def _agent_outputs(result: dict[str, Any]) -> list[dict[str, str]]:
    outputs: list[dict[str, str]] = []
    plan = result.get("plan") or []
    if plan:
        outputs.append(
            {
                "node_id": "planner",
                "node_name": "Planner",
                "agent": "planner",
                "content": f"计划来源：{result.get('planning', {}).get('source', 'unknown')}\n" + "\n".join(f"- {item}" for item in plan),
            }
        )
    for item in result.get("worker_results", []):
        agent = str(item.get("agent") or "agent")
        outputs.append(
            {
                "node_id": agent,
                "node_name": _label_for(agent),
                "agent": agent,
                "content": str(item.get("result") or ""),
            }
        )
    notes = result.get("supervisor_notes") or []
    if notes:
        outputs.append(
            {
                "node_id": "supervisor",
                "node_name": "Supervisor",
                "agent": "supervisor",
                "content": "\n".join(f"- {item}" for item in notes),
            }
        )
    if result.get("human_review_packet"):
        outputs.append(
            {
                "node_id": "human_review",
                "node_name": "Human Review",
                "agent": "human_reviewer",
                "content": "Human review packet generated.",
            }
        )
    if result.get("final_report"):
        outputs.append(
            {
                "node_id": "reporter",
                "node_name": "Reporter",
                "agent": "reporter",
                "content": str(result.get("final_report")),
            }
        )
    return outputs


def _collaboration_suggestions(result: dict[str, Any]) -> list[str]:
    suggestions: list[str] = []
    suggestions.extend(str(item) for item in result.get("supervisor_notes", []))
    project_analysis = result.get("project_analysis") or {}
    code_review = result.get("code_review") or {}
    rag_result = result.get("rag_result") or {}
    suggestions.extend(str(item) for item in project_analysis.get("suggestions", [])[:3])
    suggestions.extend(str(item) for item in code_review.get("suggestions", [])[:3])
    if rag_result.get("chunk_count", 0) > 0:
        suggestions.append("Persist RAG chunks into a vector store for semantic retrieval.")
    return suggestions


def _collaboration_suggestion_records(result: dict[str, Any]) -> list[dict[str, Any]]:
    code_review = result.get("code_review") or {}
    records = code_review.get("suggestion_records") or []
    return [item for item in records if isinstance(item, dict)]


def _collaboration_governance(result: dict[str, Any]) -> dict[str, Any]:
    records = _collaboration_suggestion_records(result)
    code_review = result.get("code_review") or {}
    review_required = bool(result.get("human_review_required")) or any(record.get("review_required") for record in records)
    risk_level = (result.get("quality_gate") or {}).get("risk_level") or _highest_risk([record.get("risk_level") for record in records])
    score = code_review.get("score")
    if isinstance(score, int) and score < 70 and risk_level == "low":
        risk_level = "high"
    next_actions = _collect_next_actions(records, _collaboration_suggestions(result))
    return {
        "risk_level": risk_level,
        "review_required": review_required,
        "next_actions": next_actions,
        "governance": {
            "risk_level": risk_level,
            "review_required": review_required,
            "next_actions": next_actions,
            "suggestion_record_count": len(records),
        },
    }


def _highest_risk(values: list[Any]) -> str:
    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    level = "low"
    for value in values:
        key = str(value or "low")
        if order.get(key, 0) > order[level]:
            level = key
    return level


def _collect_next_actions(records: list[dict[str, Any]], suggestions: list[str]) -> list[str]:
    actions: list[str] = []
    for record in records:
        for action in record.get("next_actions", [])[:2]:
            if action not in actions:
                actions.append(str(action))
    for suggestion in suggestions[:5]:
        if suggestion not in actions:
            actions.append(suggestion)
    return actions[:6] or ["Review supervisor notes.", "Save stable conclusions to project-memory."]


def _enrich_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = []
    for event in events:
        node_id = str(event.get("node") or event.get("agent") or "collaboration")
        item = dict(event)
        item["data"] = {
            **(item.get("data") or {}),
            "node_id": node_id,
            "node_type": "collaboration",
            "node_name": _label_for(node_id),
        }
        enriched.append(item)
    return enriched


def _label_for(node_id: str) -> str:
    labels = dict(COLLABORATION_NODES)
    aliases = {
        "project_analyzer": "Project Analyzer",
        "code_reviewer": "Code Reviewer",
        "rag_processor": "RAG Processor",
    }
    return labels.get(node_id) or aliases.get(node_id) or node_id.replace("_", " ").title()
