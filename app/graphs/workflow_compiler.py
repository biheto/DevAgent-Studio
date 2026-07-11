from __future__ import annotations

from collections import defaultdict, deque
from typing import Annotated, Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, StateGraph

from app.agents.code_review_tools import review_single_file
from app.graphs.project_analyzer_graph import project_analyzer_graph
from app.graphs.studio_graphs import code_review_graph, learning_coach_graph, rag_process_graph
from app.harness.events import utc_now_iso
from app.persistence.rag_store import rag_store
from app.persistence.sqlite_store import task_store
from app.providers.mcp_provider import mcp_provider


def _append_list(left: list[Any] | None, right: list[Any] | None) -> list[Any]:
    return (left or []) + (right or [])


def _merge_dict(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    return {**(left or {}), **(right or {})}


def _take_latest(left: Any, right: Any) -> Any:
    return right if right not in (None, "") else left


class WorkflowState(TypedDict, total=False):
    workflow_name: str
    input_text: str
    goal: str
    project_path: str
    max_files: int
    require_human_review: bool
    task_id: str
    current: Annotated[str, _take_latest]
    events: Annotated[list[dict[str, Any]], _append_list]
    outputs: Annotated[dict[str, Any], _merge_dict]
    tool_calls: Annotated[list[dict[str, Any]], _append_list]
    agent_outputs: Annotated[list[dict[str, Any]], _append_list]
    suggestions: Annotated[list[str], _append_list]
    human_review_packet: Annotated[dict[str, Any], _merge_dict]
    human_approvals: Annotated[dict[str, Any], _merge_dict]
    review_retries: Annotated[dict[str, Any], _merge_dict]
    resume_mode: bool
    review_action: str
    review_comment: str
    pause_workflow: Annotated[bool, _take_latest]
    final_report: Annotated[str, _take_latest]
    mermaid: Annotated[str, _take_latest]
    validation: dict[str, Any]


SUPPORTED_NODE_TYPES = {"planner", "agent", "rag", "mcp_tool", "skill", "supervisor", "human_review", "reporter"}
SUPPORTED_EDGE_CONDITIONS = {"always", "on_status", "contains", "truthy_output"}


def compile_workflow_graph(
    workflow_name: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]] | None = None,
    entry_node_id: str | None = None,
):
    normalized_nodes = _normalize_nodes(nodes)
    normalized_edges = _normalize_edges(normalized_nodes, edges or [])
    validation = validate_workflow_definition(normalized_nodes, normalized_edges)
    if not validation["valid"]:
        raise ValueError("; ".join(validation["errors"]))
    graph = StateGraph(WorkflowState)

    for node in normalized_nodes:
        graph.add_node(node["id"], _node_runner(node, normalized_nodes, normalized_edges))

    entry_id = entry_node_id or normalized_nodes[0]["id"]
    if entry_id not in {node["id"] for node in normalized_nodes}:
        raise ValueError(f"Workflow entry node `{entry_id}` does not exist.")
    graph.set_entry_point(entry_id)
    outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in normalized_edges:
        outgoing[edge["source"]].append(edge)
    for node in normalized_nodes:
        source = node["id"]
        candidates = outgoing.get(source, [])
        if candidates:
            path_map = {edge["target"]: edge["target"] for edge in candidates}
            path_map["__end__"] = END
            graph.add_conditional_edges(source, _edge_router(source, candidates), path_map)
        else:
            graph.add_edge(source, END)
    return graph.compile()


def run_compiled_workflow(
    workflow_name: str,
    input_text: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]] | None = None,
    extra_state: dict[str, Any] | None = None,
    entry_node_id: str | None = None,
) -> dict[str, Any]:
    normalized_nodes = _normalize_nodes(nodes)
    normalized_edges = _normalize_edges(normalized_nodes, edges or [])
    validation = validate_workflow_definition(normalized_nodes, normalized_edges)
    graph = compile_workflow_graph(workflow_name, normalized_nodes, normalized_edges, entry_node_id)
    initial_state: dict[str, Any] = {
        "workflow_name": workflow_name,
        "input_text": input_text,
        "goal": input_text,
        "current": input_text,
        "events": [],
        "outputs": {},
        "tool_calls": [],
        "agent_outputs": [],
        "suggestions": [],
        "validation": validation,
    }
    if extra_state:
        initial_state.update({key: value for key, value in extra_state.items() if key != "events"})
        initial_state["events"] = []
        if extra_state.get("resume_mode"):
            initial_state["current"] = extra_state.get("current") or extra_state.get("input_text") or extra_state.get("goal") or input_text
            initial_state["pause_workflow"] = False
            initial_state["human_review_packet"] = {}
        else:
            initial_state["current"] = extra_state.get("input_text") or extra_state.get("goal") or input_text

    result = graph.invoke(initial_state)
    final_report = result.get("final_report") or _build_final_report(result)
    resume_checkpoint = _build_resume_checkpoint(
        workflow_name,
        input_text,
        normalized_nodes,
        normalized_edges,
        result,
        entry_node_id,
    )
    return {
        "workflow_name": workflow_name,
        "events": result.get("events", []),
        "output": result.get("current", ""),
        "final_report": final_report,
        "mermaid": result.get("mermaid") or _build_mermaid(_normalize_nodes(nodes), edges or []),
        "suggestions": result.get("suggestions", []),
        "tool_calls": result.get("tool_calls", []),
        "agent_outputs": result.get("agent_outputs", []),
        "human_review_packet": result.get("human_review_packet"),
        "validation": validation,
        "outputs": result.get("outputs", {}),
        "resume_checkpoint": resume_checkpoint,
    }


def run_task_workflow(input_state: dict[str, Any]) -> dict[str, Any]:
    workflow_name = input_state.get("workflow_name") or "visual_task_workflow"
    input_text = input_state.get("input_text") or input_state.get("goal") or ""
    nodes = input_state.get("nodes") or []
    edges = input_state.get("edges") or []
    result = run_compiled_workflow(workflow_name, input_text, nodes, edges, input_state)
    public_result = {
        "goal": input_state.get("goal") or input_text,
        "workflow_name": workflow_name,
        "final_report": result["final_report"],
        "mermaid": result["mermaid"],
        "suggestions": result["suggestions"],
        "suggestion_records": _collect_suggestion_records(result),
        "tool_calls": result["tool_calls"],
        "agent_outputs": result["agent_outputs"],
        **_governance_summary(result),
        "human_review_required": bool(result.get("human_review_packet")),
        "human_review_packet": result.get("human_review_packet"),
        "workflow_events": result["events"],
        "planned_workflow": input_state.get("planned_workflow"),
        "validation": result.get("validation", {}),
        "resume_checkpoint": result.get("resume_checkpoint"),
    }
    return {"result": public_result, "events": result["events"], "final_report": result["final_report"]}


def resume_task_workflow(checkpoint: dict[str, Any], action: str = "approved", comment: str | None = None) -> dict[str, Any]:
    workflow_name = str(checkpoint.get("workflow_name") or "visual_task_workflow")
    input_text = str(checkpoint.get("input_text") or checkpoint.get("goal") or "")
    paused_node_id = str(checkpoint.get("paused_node_id") or "")
    state = checkpoint.get("state") if isinstance(checkpoint.get("state"), dict) else {}
    approvals = dict(state.get("human_approvals") or {})
    if paused_node_id:
        approvals[paused_node_id] = action
    outputs = dict(state.get("outputs") or {})
    paused_node = next((node for node in checkpoint.get("nodes", []) if isinstance(node, dict) and node.get("id") == paused_node_id), None)
    if paused_node:
        outputs.pop(_output_key(paused_node), None)
    resume_state = {
        **state,
        "outputs": outputs,
        "resume_mode": True,
        "human_approvals": approvals,
        "review_action": action,
        "review_comment": comment,
    }
    result = run_compiled_workflow(
        workflow_name,
        input_text,
        list(checkpoint.get("nodes") or []),
        list(checkpoint.get("edges") or []),
        resume_state,
        paused_node_id,
    )
    public_result = {
        "goal": checkpoint.get("goal") or state.get("goal") or input_text,
        "workflow_name": workflow_name,
        "final_report": result["final_report"],
        "mermaid": result["mermaid"],
        "suggestions": result["suggestions"],
        "suggestion_records": _collect_suggestion_records(result),
        "tool_calls": result["tool_calls"],
        "agent_outputs": result["agent_outputs"],
        **_governance_summary(result),
        "human_review_required": bool(result.get("human_review_packet")),
        "human_review_packet": result.get("human_review_packet"),
        "workflow_events": result["events"],
        "planned_workflow": checkpoint.get("planned_workflow"),
        "validation": result.get("validation", {}),
        "resume_checkpoint": result.get("resume_checkpoint"),
        "resumed_from": paused_node_id,
        "review_action": action,
    }
    return {"result": public_result, "events": result["events"], "final_report": result["final_report"]}


def _normalize_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return nodes or [
        {"id": "plan", "type": "planner", "name": "Task Planner", "config": {}},
        {"id": "analyze", "type": "agent", "name": "Project Agent", "config": {"agent_type": "project_analyzer"}},
        {"id": "review", "type": "human_review", "name": "Human Review", "config": {}},
        {"id": "report", "type": "reporter", "name": "Reporter", "config": {}},
    ]


def _normalize_edges(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if edges:
        return [
            {
                "source": str(edge.get("source") or ""),
                "target": str(edge.get("target") or ""),
                "condition": str(edge.get("condition") or "always"),
                "value": edge.get("value"),
                "source_path": str(edge.get("source_path") or ""),
            }
            for edge in edges
        ]
    return [
        {"source": str(nodes[index]["id"]), "target": str(nodes[index + 1]["id"]), "condition": "always", "value": None, "source_path": ""}
        for index in range(len(nodes) - 1)
    ]


def _build_resume_checkpoint(
    workflow_name: str,
    input_text: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    state: dict[str, Any],
    entry_node_id: str | None,
) -> dict[str, Any] | None:
    packet = state.get("human_review_packet")
    if not isinstance(packet, dict) or not packet:
        return None
    paused_node_id = str(packet.get("node_id") or "")
    if not paused_node_id:
        return None
    checkpoint_state = {
        "workflow_name": workflow_name,
        "input_text": input_text,
        "goal": state.get("goal") or input_text,
        "current": state.get("current") or input_text,
        "project_path": state.get("project_path"),
        "max_files": state.get("max_files"),
        "require_human_review": state.get("require_human_review"),
        "task_id": state.get("task_id"),
        "outputs": state.get("outputs", {}),
        "tool_calls": state.get("tool_calls", []),
        "agent_outputs": state.get("agent_outputs", []),
        "suggestions": state.get("suggestions", []),
        "review_retries": state.get("review_retries", {}),
        "validation": state.get("validation", {}),
    }
    return {
        "workflow_name": workflow_name,
        "input_text": input_text,
        "goal": checkpoint_state["goal"],
        "paused_node_id": paused_node_id,
        "entry_node_id": entry_node_id,
        "nodes": nodes,
        "edges": edges,
        "state": checkpoint_state,
        "created_at": utc_now_iso(),
    }


def validate_workflow_definition(nodes: list[dict[str, Any]], edges: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    normalized_nodes = _normalize_nodes(nodes)
    normalized_edges = _normalize_edges(normalized_nodes, edges or [])
    errors: list[str] = []
    warnings: list[str] = []
    node_ids = [str(node.get("id") or "") for node in normalized_nodes]
    node_id_set = set(node_ids)

    if not normalized_nodes:
        errors.append("Workflow must contain at least one node.")
    if any(not node_id for node_id in node_ids):
        errors.append("Workflow node id cannot be empty.")
    if len(node_ids) != len(node_id_set):
        errors.append("Workflow node ids must be unique.")

    for node in normalized_nodes:
        if node.get("type") not in SUPPORTED_NODE_TYPES:
            errors.append(f"Unsupported node type `{node.get('type')}` in node `{node.get('id')}`.")
        config = node.get("config") if isinstance(node.get("config"), dict) else {}
        retry_count = config.get("retry_count", 0)
        if not isinstance(retry_count, int) or retry_count < 0 or retry_count > 5:
            warnings.append(f"Node `{node.get('id')}` retry_count should be between 0 and 5.")
        if node.get("type") == "agent" and config.get("agent_type") == "file_reviewer" and not config.get("file_path"):
            warnings.append(f"File review node `{node.get('id')}` should set file_path.")

    seen_edges: set[tuple[str, str, str]] = set()
    for edge in normalized_edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        condition = str(edge.get("condition") or "always")
        if source not in node_id_set:
            errors.append(f"Edge source `{source}` does not exist.")
        if target not in node_id_set:
            errors.append(f"Edge target `{target}` does not exist.")
        if condition not in SUPPORTED_EDGE_CONDITIONS:
            errors.append(f"Edge `{source}->{target}` uses unsupported condition `{condition}`.")
        marker = (source, target, condition)
        if marker in seen_edges:
            warnings.append(f"Duplicate edge `{source}->{target}` with condition `{condition}`.")
        seen_edges.add(marker)
        if condition in {"on_status", "contains"} and edge.get("value") in (None, ""):
            warnings.append(f"Conditional edge `{source}->{target}` should set value.")

    reachable = _reachable_nodes(node_ids[0] if node_ids else "", normalized_edges)
    disconnected = sorted(node_id_set - reachable)
    if disconnected:
        warnings.append(f"Disconnected node(s): {', '.join(disconnected)}.")
    if not any(node.get("type") == "reporter" for node in normalized_nodes):
        warnings.append("Workflow has no reporter node; final_report will be synthesized from node outputs.")
    if _has_cycle(normalized_edges):
        warnings.append("Workflow contains a cycle; make sure a conditional branch can terminate it.")
    parallel_sources = sorted(source for source, count in _fan_out_counts(normalized_edges).items() if count > 1)
    if parallel_sources:
        warnings.append(f"Parallel fan-out detected at: {', '.join(parallel_sources)}.")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "node_count": len(normalized_nodes),
        "edge_count": len(normalized_edges),
        "parallel_sources": parallel_sources,
    }


def _node_runner(node: dict[str, Any], nodes: list[dict[str, Any]], edges: list[dict[str, Any]]):
    def run(state: WorkflowState) -> WorkflowState:
        config = node.get("config", {}) if isinstance(node.get("config"), dict) else {}
        start_event = _event(state, node, "running", f"{_node_name(node)} started")
        outputs = dict(state.get("outputs", {}))
        if config.get("confirm_before_run") and not _confirmation_is_approved(state, node):
            packet = _confirmation_packet(node, state, outputs)
            return {
                "current": _compact_output(packet),
                "events": [start_event, _event(state, node, "waiting_review", packet["question"], {"output": packet})],
                "outputs": {_output_key(node): packet},
                "human_review_packet": packet,
                "pause_workflow": True,
            }
        attempts = max(1, int(config.get("retry_count") or 0) + 1)
        retry_events: list[dict[str, Any]] = []
        try:
            for attempt in range(1, attempts + 1):
                try:
                    node_state = _state_with_mapped_input(state, node, outputs)
                    output, extra = _execute_node(node, node_state, outputs)
                    break
                except Exception as exc:
                    if attempt >= attempts:
                        raise
                    retry_events.append(
                        _event(
                            state,
                            node,
                            "retrying",
                            f"{_node_name(node)} failed on attempt {attempt}: {exc}. Retrying.",
                            {"attempt": attempt, "max_attempts": attempts},
                        )
                    )
        except Exception as exc:
            update: WorkflowState = {
                "events": [start_event, *retry_events, _event(state, node, "failed", str(exc))],
                "outputs": {_output_key(node): {"error": str(exc), "status": "failed"}},
            }
            if config.get("fail_strategy", "halt") != "continue":
                update["pause_workflow"] = True
            return update

        current = _compact_output(output)
        status = "waiting_review" if node.get("type") == "human_review" and extra.get("human_review_packet") else "completed"
        update: WorkflowState = {
            "current": current,
            "events": [start_event, *retry_events, _event(state, node, status, current, {"output": output})],
            "outputs": {_output_key(node): output},
        }
        if extra.get("tool_call"):
            update["tool_calls"] = [extra["tool_call"]]
        if extra.get("agent_output"):
            update["agent_outputs"] = [extra["agent_output"]]
        if extra.get("suggestions"):
            update["suggestions"] = extra["suggestions"]
        if extra.get("human_review_packet"):
            update["human_review_packet"] = extra["human_review_packet"]
            if config.get("pause_on_review", node.get("type") == "human_review"):
                update["pause_workflow"] = True
        if node.get("type") == "reporter":
            update["final_report"] = current
            update["mermaid"] = _build_mermaid(nodes, edges)
        return update

    return run


def _edge_router(source: str, edges: list[dict[str, Any]]):
    def route(state: WorkflowState) -> list[str]:
        if state.get("pause_workflow"):
            return ["__end__"]
        matched = [edge["target"] for edge in edges if _edge_matches(edge, state, source)]
        return matched or ["__end__"]

    return route


def _edge_matches(edge: dict[str, Any], state: WorkflowState, source: str) -> bool:
    condition = str(edge.get("condition") or "always")
    if condition == "always":
        return True
    source_output = state.get("outputs", {}).get(source)
    value = str(edge.get("value") or "")
    if condition == "on_status":
        latest = _latest_node_event(state, source)
        return str(latest.get("status") or "") == value
    if condition == "contains":
        haystack = _path_value(source_output, str(edge.get("source_path") or "")) if edge.get("source_path") else source_output
        return value.lower() in _condition_text(haystack).lower()
    if condition == "truthy_output":
        target = _path_value(source_output, str(edge.get("source_path") or "")) if edge.get("source_path") else source_output
        return bool(target)
    return False


def _condition_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return str(value)
    return _compact_output(value)


def _latest_node_event(state: WorkflowState, node_id: str) -> dict[str, Any]:
    for event in reversed(state.get("events", [])):
        if event.get("node") == node_id or event.get("data", {}).get("node_id") == node_id:
            return event
    return {}


def _state_with_mapped_input(state: WorkflowState, node: dict[str, Any], outputs: dict[str, Any]) -> WorkflowState:
    config = node.get("config", {}) if isinstance(node.get("config"), dict) else {}
    input_from = str(config.get("input_from") or "").strip()
    if not input_from:
        return state
    if input_from in {"current", "$current"}:
        mapped = state.get("current")
    elif input_from in {"goal", "$goal"}:
        mapped = state.get("goal") or state.get("input_text")
    else:
        mapped = outputs.get(input_from)
    input_path = str(config.get("input_path") or "").strip()
    if input_path:
        mapped = _path_value(mapped, input_path)
    if mapped in (None, ""):
        return state
    text = _compact_output(mapped)
    return {**state, "goal": text, "input_text": text, "current": text}


def _path_value(value: Any, path: str) -> Any:
    current = value
    for part in [item for item in path.split(".") if item]:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
    return current


def _output_key(node: dict[str, Any]) -> str:
    config = node.get("config", {}) if isinstance(node.get("config"), dict) else {}
    return str(config.get("output_key") or node["id"])


def _confirmation_is_approved(state: WorkflowState, node: dict[str, Any]) -> bool:
    approvals = state.get("human_approvals")
    if isinstance(approvals, dict):
        return approvals.get(node["id"]) in {True, "approved", "approve"}
    return False


def _confirmation_packet(node: dict[str, Any], state: WorkflowState, outputs: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "pending",
        "node_id": node["id"],
        "node_name": _node_name(node),
        "question": f"Approve running node `{_node_name(node)}`?",
        "options": ["approve", "reject", "revise"],
        "summary": _supervisor_notes(outputs) or [state.get("current") or state.get("goal") or ""],
    }


def _reachable_nodes(entry: str, edges: list[dict[str, Any]]) -> set[str]:
    if not entry:
        return set()
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        outgoing[str(edge.get("source"))].append(str(edge.get("target")))
    seen = {entry}
    queue: deque[str] = deque([entry])
    while queue:
        node_id = queue.popleft()
        for target in outgoing.get(node_id, []):
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return seen


def _has_cycle(edges: list[dict[str, Any]]) -> bool:
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        outgoing[str(edge.get("source"))].append(str(edge.get("target")))
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        for target in outgoing.get(node_id, []):
            if visit(target):
                return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False

    return any(visit(node_id) for node_id in list(outgoing))


def _fan_out_counts(edges: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for edge in edges:
        counts[str(edge.get("source"))] += 1
    return counts


def _execute_node(node: dict[str, Any], state: WorkflowState, outputs: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    node_type = node.get("type", "unknown")
    config = node.get("config", {})
    goal = state.get("goal") or state.get("input_text") or state.get("current") or ""
    project_path = state.get("project_path")
    max_files = int(config.get("max_files") or state.get("max_files") or 200)

    if node_type == "planner":
        plan = [
            "Read the task goal and choose an execution path.",
            "Run Agent, Tool, and Knowledge nodes according to canvas edges.",
            "Merge node outputs into a final report.",
        ]
        return {"plan": plan, "goal": goal}, {"agent_output": _agent_output(node, "planner", "\n".join(plan))}

    if node_type == "agent":
        return _run_agent_node(node, config, goal, project_path, max_files)

    if node_type == "rag":
        collection = str(config.get("collection") or "default")
        limit = int(config.get("top_k") or 5)
        results = rag_store.query(collection, goal, limit)
        text = f"Knowledge collection {collection} returned {len(results)} result(s)."
        return {"collection": collection, "results": results}, {
            "agent_output": _agent_output(node, "rag", text),
            "suggestions": [text],
        }

    if node_type == "mcp_tool":
        return _run_tool_node(node, config, project_path, max_files)

    if node_type == "skill":
        return _run_skill_node(node, config, goal, project_path)

    if node_type == "supervisor":
        notes = _supervisor_notes(outputs)
        return {"notes": notes}, {"agent_output": _agent_output(node, "supervisor", "\n".join(notes))}

    if node_type == "human_review":
        if _confirmation_is_approved(state, node):
            approved = {
                "status": "approved",
                "node_id": node["id"],
                "node_name": _node_name(node),
                "comment": state.get("review_comment"),
            }
            return approved, {"agent_output": _agent_output(node, "human_reviewer", "Human review approved.")}
        packet = {
            "status": "pending",
            "node_id": node["id"],
            "node_name": _node_name(node),
            "question": "Approve archiving the current Workflow result?",
            "options": ["approve", "reject", "revise"],
            "summary": _supervisor_notes(outputs),
        }
        return packet, {"human_review_packet": packet}

    if node_type == "reporter":
        report = _build_final_report({**state, "outputs": outputs})
        return report, {"agent_output": _agent_output(node, "reporter", report)}

    text = f"{_node_name(node)} completed: {state.get('current') or goal}"
    return text, {"agent_output": _agent_output(node, node_type, text)}


def _run_agent_node(
    node: dict[str, Any],
    config: dict[str, Any],
    goal: str,
    project_path: str | None,
    max_files: int,
) -> tuple[Any, dict[str, Any]]:
    agent_type = str(config.get("agent_type") or config.get("agent") or "project_analyzer")
    if agent_type == "project_analyzer":
        if not project_path:
            raise ValueError("project_path is required for project_analyzer")
        result = project_analyzer_graph.invoke({"project_path": project_path, "max_files": max_files})
        text = result.get("report_markdown") or f"Project analysis completed: {result.get('quality_score', 'N/A')}"
    elif agent_type == "code_reviewer":
        if not project_path:
            raise ValueError("project_path is required for code_reviewer")
        result = code_review_graph.invoke({"project_path": project_path, "max_files": max_files})["result"]
        text = result.get("report_markdown") or f"Code review completed: {result.get('score', 'N/A')}"
    elif agent_type == "file_reviewer":
        if not project_path:
            raise ValueError("project_path is required for file_reviewer")
        file_path = str(config.get("file_path") or config.get("target_file") or "")
        if not file_path:
            raise ValueError("file_path is required for file_reviewer")
        result = review_single_file(
            project_path,
            file_path,
            int(config.get("max_chars") or 20000),
        )
        text = result.get("report_markdown") or f"File review completed: {result.get('file_path')}"
    elif agent_type == "rag_processor":
        if not project_path:
            raise ValueError("project_path is required for rag_processor")
        result = rag_process_graph.invoke({"project_path": project_path, "max_files": max_files})["result"]
        text = result.get("report_markdown") or f"RAG processing completed: {len(result.get('chunks', []))} chunks."
        should_ingest = bool(config.get("ingest", True))
        collection = str(config.get("collection") or "project-memory")
        if should_ingest:
            saved = rag_store.ingest(collection, result.get("documents", []), result.get("chunks", []))
            result = {
                **result,
                "ingest": {
                    "enabled": True,
                    "collection": collection,
                    **saved,
                },
            }
            text = (
                f"{text}\n\n"
                "## RAG 入库结果\n\n"
                f"- collection: `{collection}`\n"
                f"- documents: {saved['document_count']}\n"
                f"- chunks: {saved['chunk_count']}"
            )
        else:
            result = {**result, "ingest": {"enabled": False, "collection": collection}}
    elif agent_type == "learning_coach":
        result = learning_coach_graph.invoke(
            {
                "topic": config.get("topic") or goal or "LangGraph",
                "level": config.get("level") or "beginner",
                "days": int(config.get("days") or 7),
            }
        )["result"]
        text = result.get("report_markdown") or "Learning plan generated."
    else:
        result = {"message": f"Unknown Agent: {agent_type}", "goal": goal}
        text = result["message"]
    focus = config.get("focus")
    output_format = config.get("output_format")
    if isinstance(result, dict) and (focus or output_format):
        result = {
            **result,
            "user_focus": focus,
            "output_format": output_format,
        }
        text = f"{text}\n\nUser focus: {focus or 'not specified'}\nOutput format: {output_format or 'default'}"
    extra: dict[str, Any] = {"agent_output": _agent_output(node, agent_type, text)}
    if agent_type == "rag_processor":
        ingest = result.get("ingest") if isinstance(result, dict) else None
        if isinstance(ingest, dict) and ingest.get("enabled"):
            extra["suggestions"] = [
                f"RAG chunks ingested into `{ingest.get('collection')}`: {ingest.get('chunk_count', 0)} chunk(s)."
            ]
    return result, extra


def _run_tool_node(
    node: dict[str, Any],
    config: dict[str, Any],
    project_path: str | None,
    max_files: int,
) -> tuple[Any, dict[str, Any]]:
    tool_name = str(config.get("tool_name") or "filesystem.list")
    root_path = str(config.get("root_path") or project_path or ".")
    arguments = {
        "root_path": root_path,
        "repo_path": root_path,
        "max_files": int(config.get("max_files") or max_files),
        "file_path": str(config.get("file_path") or "README.md"),
        "max_chars": int(config.get("max_chars") or 4000),
        "limit": int(config.get("limit") or 10),
        **(config.get("arguments") if isinstance(config.get("arguments"), dict) else {}),
    }
    result = mcp_provider.call_tool(
        tool_name,
        arguments,
        server_id=str(config.get("server_id") or "") or None,
        agent_code=str(config.get("agent_code") or "workflow_runner"),
    )
    tool_call = {"node_id": node["id"], "tool_name": tool_name, "status": "completed", "result": result}
    return result, {"tool_call": tool_call, "agent_output": _agent_output(node, tool_name, f"Tool {tool_name} completed.")}


def _run_skill_node(
    node: dict[str, Any],
    config: dict[str, Any],
    goal: str,
    project_path: str | None,
) -> tuple[Any, dict[str, Any]]:
    import json as _json
    from app.skills.executor import execute_skill_by_type, _safe_int

    skill_code = str(config.get("skill_code") or "")
    if not skill_code:
        return {"error": "skill_code is required"}, {"agent_output": _agent_output(node, "skill", "Skill node: no skill_code configured.")}

    skill_row = task_store.get_skill(skill_code)
    if not skill_row:
        return {"error": f"Skill not found: {skill_code}"}, {"agent_output": _agent_output(node, "skill", f"Skill {skill_code} not found in database.")}

    skill_input = config.get("skill_input") or {}
    if isinstance(skill_input, str):
        try:
            skill_input = _json.loads(skill_input) if skill_input.strip() else {}
        except Exception:
            skill_input = {}

    result = execute_skill_by_type(
        skill_code=skill_code,
        execution=skill_row.get("execution", {}),
        input_data=skill_input,
        goal=goal,
        project_path=project_path,
        max_files=_safe_int(config.get("max_files"), 200),
    )

    if "error" in result:
        return result, {"agent_output": _agent_output(node, "skill", f"Skill {skill_code} failed: {result['error']}")}

    exec_type = skill_row.get("execution", {}).get("type", "prompt")
    text = f"Skill {skill_code} ({exec_type}) completed."
    return {"skill_code": skill_code, "result": result}, {"agent_output": _agent_output(node, "skill", text)}


def _event(
    state: WorkflowState,
    node: dict[str, Any],
    status: str,
    content: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_id = state.get("task_id") or "workflow_preview"
    return {
        "task_id": task_id,
        "event_id": f"evt_{task_id}_{node['id']}_{uuid4().hex[:8]}",
        "type": "workflow_node",
        "node": node["id"],
        "agent": node.get("type", "workflow"),
        "status": status,
        "content": content,
        "timestamp": utc_now_iso(),
        "data": {
            "node_id": node["id"],
            "node_type": node.get("type"),
            "node_name": _node_name(node),
            **(data or {}),
        },
    }


def _agent_output(node: dict[str, Any], agent: str, content: str) -> dict[str, str]:
    return {"node_id": node["id"], "node_name": _node_name(node), "agent": agent, "content": content}


def _node_name(node: dict[str, Any]) -> str:
    return str(node.get("name") or node["id"])


def _compact_output(output: Any) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        if "report_markdown" in output:
            return str(output["report_markdown"])
        if "message" in output:
            return str(output["message"])
        if "plan" in output:
            return "\n".join(f"- {item}" for item in output["plan"])
        if "notes" in output:
            return "\n".join(f"- {item}" for item in output["notes"])
    return str(output)


def _supervisor_notes(outputs: dict[str, Any]) -> list[str]:
    notes = [f"Merged {len(outputs)} node output(s)."]
    for node_id, output in outputs.items():
        text = _compact_output(output).replace("\n", " ")
        notes.append(f"{node_id}: {text[:120]}")
    return notes


def _build_final_report(state: dict[str, Any]) -> str:
    goal = state.get("goal") or state.get("input_text") or "Untitled task"
    outputs = state.get("outputs", {})
    agent_outputs = state.get("agent_outputs", [])
    tool_calls = state.get("tool_calls", [])
    suggestions = state.get("suggestions", [])

    lines = [
        "# Workflow Execution Report",
        "",
        "## Goal",
        "",
        str(goal),
        "",
        "## Agent Outputs",
        "",
    ]
    if agent_outputs:
        for item in agent_outputs:
            first_line = str(item.get("content", "")).splitlines()[0][:180]
            lines.append(f"- **{item.get('node_name')} / {item.get('agent')}**: {first_line}")
    else:
        lines.append("- No Agent output.")

    lines.extend(["", "## Tool Calls", ""])
    if tool_calls:
        for item in tool_calls:
            lines.append(f"- **{item.get('tool_name')}**: {item.get('status')}")
    else:
        lines.append("- No tool call.")

    lines.extend(["", "## Node Result Summary", ""])
    for node_id, output in outputs.items():
        lines.append(f"- **{node_id}**: {_compact_output(output).splitlines()[0][:180]}")

    detailed_reports = [
        (node_id, str(output["report_markdown"]))
        for node_id, output in outputs.items()
        if isinstance(output, dict) and output.get("report_markdown")
    ]
    if detailed_reports:
        lines.extend(["", "## Detailed Agent Reports", ""])
        for node_id, report in detailed_reports:
            lines.extend([f"### {node_id}", "", report, ""])

    lines.extend(["", "## Suggestions", ""])
    if suggestions:
        for suggestion in suggestions:
            lines.append(f"- {suggestion}")
    else:
        lines.extend(
            [
                "- Save high-frequency workflows as reusable templates.",
                "- Add human review to high-risk nodes.",
                "- Upgrade RAG nodes to vector retrieval and MCP nodes to a real MCP client later.",
            ]
        )
    return "\n".join(lines)


def _governance_summary(result: dict[str, Any]) -> dict[str, Any]:
    records = _collect_suggestion_records(result)
    suggestions = [str(item) for item in result.get("suggestions", [])]
    outputs = result.get("outputs", {})
    review_required = bool(result.get("human_review_packet")) or any(record.get("review_required") for record in records)
    risk_level = _highest_risk([record.get("risk_level") for record in records])
    if risk_level == "low" and _report_mentions_risk(outputs):
        risk_level = "medium"
    next_actions = _collect_next_actions(records, suggestions)
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


def _collect_suggestion_records(result: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for output in result.get("outputs", {}).values():
        if isinstance(output, dict):
            records.extend(item for item in output.get("suggestion_records", []) if isinstance(item, dict))
    return records


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
    return actions[:6] or [
        "Review the generated report.",
        "Save useful conclusions to project-memory.",
        "Add regression tests for the highest-risk workflow.",
    ]


def _report_mentions_risk(outputs: dict[str, Any]) -> bool:
    text = "\n".join(str(output.get("report_markdown", output)) for output in outputs.values() if isinstance(output, dict))
    lowered = text.lower()
    return any(keyword in lowered for keyword in ["risk", "风险", "security", "critical", "high"])


def _build_mermaid(nodes: list[dict[str, Any]], edges: list[dict[str, str]]) -> str:
    normalized_nodes = _normalize_nodes(nodes)
    lines = ["flowchart LR"]
    for node in normalized_nodes:
        lines.append(f"  {node['id']}[{_node_name(node)}]")
    for edge in edges:
        label = ""
        if edge.get("condition") and edge.get("condition") != "always":
            suffix = f": {edge.get('value')}" if edge.get("value") not in (None, "") else ""
            label = f"|{edge.get('condition')}{suffix}|"
        lines.append(f"  {edge['source']} -->{label} {edge['target']}")
    if not edges:
        for index, node in enumerate(normalized_nodes[:-1]):
            lines.append(f"  {node['id']} --> {normalized_nodes[index + 1]['id']}")
    return "\n".join(lines)
