from __future__ import annotations

from typing import Annotated, Any, TypedDict
from uuid import uuid4
import json

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from app.graphs.collaboration_policy import AGENTS, create_plan, quality_gate, evidence_records, evidence_markdown

from app.graphs.project_analyzer_graph import project_analyzer_graph
from app.harness.events import utc_now_iso
from app.persistence.sqlite_store import task_store
from app.skills.base import SkillContext
from app.skills.registry import skill_registry
from app.providers.llm_provider import llm_provider


def merge_events(left, right):
    return list({item["event_id"]: item for item in [*left, *right]}.values())


def merge_agents(left, right):
    return list(dict.fromkeys([*left, *right]))


def merge_rounds(left, right):
    return {**left, **right}


class StudioState(TypedDict, total=False):
    project_path: str
    max_files: int
    topic: str
    level: str
    days: int
    goal: str
    workflow_name: str
    input_text: str
    nodes: list[dict[str, Any]]
    require_human_review: bool
    task_id: str
    events: Annotated[list[dict[str, Any]], merge_events]
    plan: list[str]
    execution_plan: dict[str, Any]
    planning: dict[str, Any]
    completed_agents: Annotated[list[str], merge_agents]
    worker_rounds: Annotated[dict[str, int], merge_rounds]
    parallel_workers: bool
    repair_options: dict[str, Any]
    repair_round: int
    repair_result: dict[str, Any]
    repair_history: list[dict[str, Any]]
    original_project_path: str
    budget: dict[str, Any]
    quality_gate: dict[str, Any]
    review_decision: str
    review_comment: str
    report_generation: dict[str, Any]
    business_scenario: str
    durable_run_id: str
    project_analysis: dict[str, Any]
    code_review: dict[str, Any]
    rag_result: dict[str, Any]
    supervisor_notes: list[str]
    human_review_packet: dict[str, Any]
    final_report: str
    result: dict[str, Any]


def _code_review_node(state: StudioState) -> StudioState:
    context = SkillContext(agent_code="code_reviewer")
    return {**state, "result": skill_registry.execute("code.review", context, state)}


def _rag_process_node(state: StudioState) -> StudioState:
    context = SkillContext(agent_code="rag_processor")
    return {**state, "result": skill_registry.execute("rag.chunk", context, state)}


def _learning_node(state: StudioState) -> StudioState:
    context = SkillContext(agent_code="learning_coach")
    return {**state, "result": skill_registry.execute("learning.plan", context, state)}


def _workflow_node(state: StudioState) -> StudioState:
    context = SkillContext(agent_code="workflow_runner")
    return {**state, "result": skill_registry.execute("workflow.run", context, state)}


def _single_node_graph(name: str, node):
    graph = StateGraph(StudioState)
    graph.add_node(name, node)
    graph.set_entry_point(name)
    graph.add_edge(name, END)
    return graph.compile()


code_review_graph = _single_node_graph("code_review", _code_review_node)
rag_process_graph = _single_node_graph("rag_process", _rag_process_node)
learning_coach_graph = _single_node_graph("learning_coach", _learning_node)
workflow_runner_graph = _single_node_graph("workflow_runner", _workflow_node)


def _append_event(state: StudioState, node: str, agent: str, content: str, status: str = "completed") -> list[dict[str, Any]]:
    events = list(state.get("events", []))
    events.append(
        {
            "task_id": state.get("task_id"),
            "event_id": f"evt_{uuid4().hex}",
            "node": node,
            "agent": agent,
            "type": "agent_step",
            "status": status,
            "content": content,
            "timestamp": utc_now_iso(),
            "data": {},
        }
    )
    event = events[-1]
    if event.get("task_id"):
        event["data"] = {"node_id": node, "node_type": "collaboration", "node_name": node.replace("_", " ").title()}
        task_store.append_event(event)
        event["persisted"] = True
    return events


def _planner_node(state: StudioState) -> StudioState:
    plan, metadata = create_plan(state["goal"], state.get("project_path"), state.get("business_scenario", ""))
    if state.get("repair_options", {}).get("enabled") and not any(step["agent"] == "code_reviewer" for step in plan["steps"]):
        plan["steps"].append({"agent": "code_reviewer", "objective": "为修复闭环提供代码证据"})
    return {
        **state, "execution_plan": plan, "planning": metadata, "completed_agents": [],
        "plan": [f"{step['agent']}: {step['objective']}" for step in plan["steps"]],
        "events": _append_event(state, "planner", "planner", "可执行计划已校验：" + ", ".join(step["agent"] for step in plan["steps"])),
    }


def _run_worker(state: StudioState, agent: str, result_key: str, execute) -> StudioState:
    state = {**state, "events": _append_event(state, agent, agent, "开始执行", "running")}
    try:
        result = execute()
    except Exception as exc:
        _append_event(state, agent, agent, str(exc), "failed")
        raise
    return {result_key: result, "completed_agents": [agent], "worker_rounds": {agent: state.get("repair_round", 0)},
            "events": _append_event(state, agent, agent, "执行完成，结果已交给 Supervisor")}


def _project_analyzer_subgraph_node(state: StudioState) -> StudioState:
    return _run_worker(state, "project_analyzer", "project_analysis", lambda: project_analyzer_graph.invoke({
        "project_path": state["project_path"], "max_files": state.get("max_files", 500),
    }))


def _code_reviewer_subgraph_node(state: StudioState) -> StudioState:
    return _run_worker(state, "code_reviewer", "code_review", lambda: code_review_graph.invoke({
        "project_path": state["project_path"], "max_files": state.get("max_files", 500),
    })["result"])


def _rag_processor_subgraph_node(state: StudioState) -> StudioState:
    return _run_worker(state, "rag_processor", "rag_result", lambda: rag_process_graph.invoke({
        "project_path": state["project_path"], "max_files": state.get("max_files", 300),
    })["result"])


def _dispatch_workers(state: StudioState):
    pending = [step["agent"] for step in state["execution_plan"]["steps"]
               if state.get("worker_rounds", {}).get(step["agent"], 0 if step["agent"] in state.get("completed_agents", []) else -1) != state.get("repair_round", 0)]
    return (pending if state.get("parallel_workers") else pending[:1]) or ["supervisor"]


def _next_worker(state: StudioState):
    return "supervisor" if state.get("parallel_workers") else _dispatch_workers(state)[0]


def _repair_node(state: StudioState):
    from app.agents.repair_loop import attempt_repair
    result = attempt_repair(state)
    update = {"repair_result": result, "repair_round": state.get("repair_round", 0) + 1,
              "quality_gate": {**state["quality_gate"], "review_required": True,
                               "reasons": [*state["quality_gate"]["reasons"], "修复尝试需要人工核对"]},
              "repair_history": [*state.get("repair_history", []), result],
              "events": _append_event(state, "repair", "repairer", f"修复第 {result['round']} 轮：{result['status']}")}
    if result.get("workspace"):
        update["project_path"] = result["workspace"]
    return update


def _replan_node(state: StudioState):
    feedback = json.dumps({"repair": {key: state["repair_result"].get(key) for key in ("status", "reason", "validation")},
                           "risk": state.get("quality_gate")}, ensure_ascii=False)
    plan, metadata = create_plan(state["goal"] + "\n根据修复及验证反馈重新规划复查：" + feedback, state["project_path"], state.get("business_scenario", ""))
    if not any(step["agent"] == "code_reviewer" for step in plan["steps"]):
        plan["steps"].append({"agent": "code_reviewer", "objective": "复查修复后的代码风险"})
    for agent in state.get("completed_agents", []):
        if not any(step["agent"] == agent for step in plan["steps"]):
            plan["steps"].append({"agent": agent, "objective": "刷新候选副本的项目证据"})
    return {"execution_plan": plan, "planning": metadata, "plan": [f"{s['agent']}: {s['objective']}" for s in plan["steps"]],
            "events": _append_event(state, "replanner", "planner", "依据验证反馈重新规划，开始复查候选修复")}


def _supervisor_node(state: StudioState) -> StudioState:
    gate = quality_gate(state)
    fallback_notes = [*gate["reasons"], *gate["coverage_gaps"]]
    fallback_notes.append("已完成的 Agent：" + ", ".join(state.get("completed_agents", [])))
    if "rag_processor" in state.get("completed_agents", []):
        fallback_notes.append("知识切片已生成，尚未自动写入知识库。")
    notes = _llm_supervisor_notes(state.get("project_analysis", {}), state.get("code_review", {}), state.get("rag_result", {}), fallback_notes)
    # Model commentary may add context, but cannot remove deterministic review reasons.
    notes = list(dict.fromkeys([*fallback_notes, *notes]))
    return {**state, "quality_gate": gate, "supervisor_notes": notes,
            "events": _append_event(state, "supervisor", "supervisor", f"质量检查完成：{gate['risk_level']}；人工审核：{gate['review_required']}")}


def _llm_supervisor_notes(
    project_analysis: dict[str, Any],
    code_review: dict[str, Any],
    rag_result: dict[str, Any],
    fallback_notes: list[str],
) -> list[str]:
    fallback = "\n".join(f"- {item}" for item in fallback_notes)
    text = llm_provider.generate(
        "你是 DevAgent Studio 的研发治理 Supervisor。请基于各 Agent 事实判断风险优先级、是否需要人工审核和下一步动作。",
        (
            "请输出 3-6 条中文短句 bullet。不要编造事实，不要写长篇报告。\n"
            f"项目分析：{_compact_project_analysis(project_analysis)}\n"
            f"代码审查：{_compact_code_review(code_review)}\n"
            f"RAG 结果：{_compact_rag_result(rag_result)}"
        ),
        fallback,
        agent="supervisor",
        prompt_version="supervisor.v1",
    )
    notes = [line.strip("- ").strip() for line in text.splitlines() if line.strip()]
    return notes or fallback_notes


def _should_review(state: StudioState) -> str:
    options = state.get("repair_options", {})
    result = state.get("repair_result", {})
    findings = state.get("code_review", {}).get("findings", [])
    if options.get("enabled") and state.get("repair_round", 0) < options.get("max_rounds", 2):
        if (findings or result.get("status") == "validation_failed") and result.get("status") not in {"no_changes", "invalid_patch", "no_editable_evidence"}:
            return "repair"
    return "review" if state["quality_gate"]["review_required"] else "report"


def _prepare_review(state: StudioState) -> StudioState:
    packet = {"status": "pending", "node_id": "human_review",
              "question": "请核对执行范围与风险证据，是否允许生成正式协作报告？",
              "options": ["approve", "reject", "revise"], "summary": state.get("supervisor_notes", []),
              "risk_level": state["quality_gate"]["risk_level"], "evidence": evidence_records(state)}
    return {**state, "human_review_packet": packet,
            "events": _append_event(state, "human_review", "human_reviewer", "已暂停，等待人工审核", "waiting_review")}


def _human_review_node(state: StudioState) -> StudioState:
    response = interrupt(state["human_review_packet"])
    action = response.get("action") if isinstance(response, dict) else response
    comment = str(response.get("comment") or "") if isinstance(response, dict) else ""
    approved = action in {"approve", "approved"}
    decision = "approved" if approved else "rejected"
    return {**state, "review_decision": decision, "review_comment": comment,
            "human_review_packet": {**state["human_review_packet"], "status": decision},
            "events": _append_event(state, "human_review", "human_reviewer",
                                    "审核通过，继续生成报告" if approved else "审核已拒绝，任务停止",
                                    "completed" if approved else "cancelled")}


def collaboration_result(state: StudioState) -> dict[str, Any]:
    summaries = {
        "project_analyzer": _compact_project_analysis(state.get("project_analysis", {})),
        "code_reviewer": _compact_code_review(state.get("code_review", {})),
        "rag_processor": _compact_rag_result(state.get("rag_result", {})),
    }
    gate = state.get("quality_gate") or quality_gate(state)
    return {
        "goal": state.get("goal", ""), "plan": state.get("plan", []),
        "execution_plan": state.get("execution_plan", {}), "planning": state.get("planning", {}),
        "completed_agents": state.get("completed_agents", []),
        "worker_results": [{"agent": agent, "result": json.dumps(summaries[agent], ensure_ascii=False)}
                           for agent in state.get("completed_agents", [])],
        "supervisor_notes": state.get("supervisor_notes", []), "quality_gate": gate,
        "human_review_required": gate["review_required"],
        "human_review_packet": state.get("human_review_packet"), "review_decision": state.get("review_decision"),
        "review_comment": state.get("review_comment", ""),
        "final_report": state.get("final_report", ""), "report_generation": state.get("report_generation", {}),
        "evidence": evidence_records(state),
        "parallel_workers": state.get("parallel_workers", False),
        "repair_result": state.get("repair_result"), "repair_history": state.get("repair_history", []),
        "project_analysis": summaries["project_analyzer"], "code_review": summaries["code_reviewer"],
        "rag_result": summaries["rag_processor"], "events": state.get("events", []),
    }


def _reporter_node(state: StudioState) -> StudioState:
    facts = collaboration_result(state)
    response = llm_provider.generate_with_status(
        "你是项目治理报告生成器。只能依据提供的执行范围和证据输出中文 Markdown。未执行的检查必须写未执行，"
        "不得把代码问题缺失说成安全保证，不得声称知识切片已经入库。引用问题时保留 evidence_id、文件和行号。",
        json.dumps({key: facts[key] for key in ("goal", "worker_results", "supervisor_notes", "quality_gate", "evidence", "review_comment")}, ensure_ascii=False),
        _build_collaboration_report(state), agent="reporter", prompt_version="collaboration_report.v1",
    )
    # Always append the canonical evidence, even when a model rewrites the narrative.
    if not isinstance(response.get("text"), str) or not response["text"].strip():
        response = {**response, "text": _build_collaboration_report(state), "answer_source": "fallback", "fallback_used": True}
    report = response["text"] + "\n\n" + evidence_markdown(state)
    if state.get("repair_result"):
        result = state["repair_result"]
        report += f"\n\n## 修复验证\n\n- 状态：{result['status']}\n- 轮次：{state.get('repair_round', 0)}\n- 结果来自候选副本，尚未应用到原项目。\n- 验证：{result.get('validation', {}).get('kind', '未执行')}"
    generation = {"source": response.get("answer_source", "fallback"), "trace_id": response.get("trace_id"),
                  "fallback_used": bool(response.get("fallback_used"))}
    updated = {**state, "final_report": report, "report_generation": generation,
               "events": _append_event(state, "reporter", "reporter", "正式报告已生成，附执行范围与证据")}
    return {**updated, "result": collaboration_result(updated)}


def _build_collaboration_report(state: StudioState) -> str:
    analysis = state.get("project_analysis", {})
    review = state.get("code_review", {})
    rag = state.get("rag_result", {})
    notes = "\n".join(f"- {item}" for item in state.get("supervisor_notes", []))
    stack = ", ".join(analysis.get("tech_stack", [])) or "暂未识别"
    completed = state.get("completed_agents", [])
    analysis_summary = (
        f"- 项目名称：{analysis.get('scan', {}).get('project_name', '未知')}\n"
        f"- 技术栈：{stack}\n- 质量评分：{analysis.get('quality_score', 'N/A')}"
        if "project_analyzer" in completed else "未执行项目结构分析。"
    )
    review_summary = (
        f"- 审查评分：{review.get('score', 'N/A')}\n- 问题数量：{len(review.get('findings', []))}"
        if "code_reviewer" in completed else "未执行代码审查，无法判定代码风险。"
    )
    rag_summary = (
        f"- 文档数量：{len(rag.get('documents', []))}\n- 切片数量：{len(rag.get('chunks', []))}\n"
        "- 切片尚未自动写入知识库。"
        if "rag_processor" in completed else "未执行 RAG 知识加工。"
    )
    return f"""# 多 Agent 协作报告

## 目标

{state['goal']}

## 项目分析摘要

{analysis_summary}

## 代码审查摘要

{review_summary}

## RAG 知识加工摘要

{rag_summary}

## Supervisor 意见

{notes}
"""


def _compact_project_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_name": analysis.get("scan", {}).get("project_name"),
        "tech_stack": analysis.get("tech_stack", []),
        "modules": analysis.get("modules", [])[:20],
        "file_count": len(analysis.get("scan", {}).get("files", [])),
        "quality_score": analysis.get("quality_score"),
        "risks": analysis.get("risks", []),
        "suggestions": analysis.get("suggestions", []),
    }


def _compact_code_review(review: dict[str, Any]) -> dict[str, Any]:
    return {
        "score": review.get("score"),
        "finding_count": len(review.get("findings", [])),
        "findings": evidence_records({"code_review": review}),
        "risks": review.get("risks", []),
        "suggestions": review.get("suggestions", []),
        "suggestion_records": review.get("suggestion_records", []),
    }


def _compact_rag_result(rag: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_count": len(rag.get("documents", [])),
        "chunk_count": len(rag.get("chunks", [])),
        "keywords": rag.get("keywords", [])[:10],
    }


def _build_collaboration_graph(checkpointer=None):
    graph = StateGraph(StudioState)
    graph.add_node("planner", _planner_node)
    graph.add_node("project_analyzer", _project_analyzer_subgraph_node)
    graph.add_node("code_reviewer", _code_reviewer_subgraph_node)
    graph.add_node("rag_processor", _rag_processor_subgraph_node)
    graph.add_node("supervisor", _supervisor_node)
    graph.add_node("review_gate", _prepare_review)
    graph.add_node("human_review", _human_review_node)
    graph.add_node("reporter", _reporter_node)
    graph.add_node("repair", _repair_node)
    graph.add_node("replanner", _replan_node)
    graph.set_entry_point("planner")
    destinations = {agent: agent for agent in (*AGENTS, "supervisor")}
    for node in ("planner", "replanner"):
        graph.add_conditional_edges(node, _dispatch_workers, destinations)
    for node in AGENTS:
        graph.add_conditional_edges(node, _next_worker, destinations)
    graph.add_conditional_edges("supervisor", _should_review, {"review": "review_gate", "report": "reporter", "repair": "repair"})
    graph.add_conditional_edges("repair", lambda state: "replan" if state["repair_result"].get("workspace") else "review",
                               {"replan": "replanner", "review": "review_gate"})
    graph.add_edge("review_gate", "human_review")
    graph.add_conditional_edges("human_review", lambda state: "report" if state["review_decision"] == "approved" else "stop",
                               {"report": "reporter", "stop": END})
    graph.add_edge("reporter", END)
    return graph.compile(checkpointer=checkpointer)


collaboration_graph = _build_collaboration_graph()
