from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.graphs.project_analyzer_graph import project_analyzer_graph
from app.harness.events import utc_now_iso
from app.persistence.sqlite_store import task_store
from app.skills.base import SkillContext
from app.skills.registry import skill_registry
from app.providers.llm_provider import llm_provider


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
    events: list[dict[str, Any]]
    plan: list[str]
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
            "event_id": f"evt_{state.get('task_id', 'adhoc')}_{node}_{len(events) + 1}",
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
    fallback_plan = [
        "解析用户目标并确定需要执行的 Agent",
        "调用项目分析子图生成项目画像",
        "调用代码审查子图识别代码风险",
        "调用 RAG 知识加工子图沉淀资料",
        "由 Supervisor 汇总质量意见",
        "根据策略决定是否进入人工审核",
        "生成最终协作报告",
    ]
    plan = llm_provider.plan_steps(
        state["goal"],
        {"project_path": state.get("project_path"), "max_files": state.get("max_files")},
        fallback_plan,
    )
    return {
        **state,
        "plan": plan,
        "events": _append_event(state, "planner", "planner", "任务规划完成"),
    }


def _project_analyzer_subgraph_node(state: StudioState) -> StudioState:
    result = project_analyzer_graph.invoke(
        {
            "project_path": state["project_path"],
            "max_files": state.get("max_files", 500),
        }
    )
    return {
        **state,
        "project_analysis": result,
        "events": _append_event(state, "project_analyzer", "project_analyzer", "项目分析子图执行完成"),
    }


def _code_reviewer_subgraph_node(state: StudioState) -> StudioState:
    result = code_review_graph.invoke(
        {
            "project_path": state["project_path"],
            "max_files": state.get("max_files", 500),
        }
    )["result"]
    return {
        **state,
        "code_review": result,
        "events": _append_event(state, "code_reviewer", "code_reviewer", "代码审查子图执行完成"),
    }


def _rag_processor_subgraph_node(state: StudioState) -> StudioState:
    result = rag_process_graph.invoke(
        {
            "project_path": state["project_path"],
            "max_files": state.get("max_files", 300),
        }
    )["result"]
    return {
        **state,
        "rag_result": result,
        "events": _append_event(state, "rag_processor", "rag_processor", "RAG 知识加工子图执行完成"),
    }


def _supervisor_node(state: StudioState) -> StudioState:
    fallback_notes = []
    code_review = state.get("code_review", {})
    project_analysis = state.get("project_analysis", {})
    rag_result = state.get("rag_result", {})

    if code_review.get("score", 100) < 70:
        fallback_notes.append("代码审查评分低于 70，需要优先处理高风险问题。")
    else:
        fallback_notes.append("代码审查未发现阻断级问题，可进入报告阶段。")

    if not project_analysis.get("tech_stack"):
        fallback_notes.append("项目技术栈识别不足，建议补充 README 或构建配置。")
    else:
        fallback_notes.append("项目画像信息充足，可支撑后续报告。")

    if len(rag_result.get("chunks", [])) == 0:
        fallback_notes.append("RAG 未生成有效切片，知识沉淀效果有限。")
    else:
        fallback_notes.append("RAG 已生成知识切片，可进入向量库增强阶段。")

    notes = _llm_supervisor_notes(project_analysis, code_review, rag_result, fallback_notes)

    return {
        **state,
        "supervisor_notes": notes,
        "events": _append_event(state, "supervisor", "supervisor", "质量监督完成"),
    }


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
    if state.get("require_human_review", True):
        return "review"
    return "report"


def _human_review_node(state: StudioState) -> StudioState:
    packet = {
        "status": "pending",
        "question": "是否允许基于本次多 Agent 结果继续生成正式报告？",
        "options": ["approve", "reject", "revise"],
        "summary": state.get("supervisor_notes", []),
    }
    return {
        **state,
        "human_review_packet": packet,
        "events": _append_event(state, "human_review", "human_reviewer", "已生成人工审核包", "waiting_review"),
    }


def _reporter_node(state: StudioState) -> StudioState:
    fallback_report = _build_collaboration_report(state)
    report = llm_provider.write_report(
        state["goal"],
        {
            "project_analysis": _compact_project_analysis(state.get("project_analysis", {})),
            "code_review": _compact_code_review(state.get("code_review", {})),
            "rag_result": _compact_rag_result(state.get("rag_result", {})),
            "supervisor_notes": state.get("supervisor_notes", []),
        },
        fallback_report,
    )
    result = {
        "goal": state["goal"],
        "plan": state.get("plan", []),
        "worker_results": [
            {"agent": "project_analyzer", "result": "项目分析子图已完成"},
            {"agent": "code_reviewer", "result": "代码审查子图已完成"},
            {"agent": "rag_processor", "result": "RAG 知识加工子图已完成"},
        ],
        "supervisor_notes": state.get("supervisor_notes", []),
        "human_review_required": state.get("require_human_review", True),
        "human_review_packet": state.get("human_review_packet"),
        "final_report": report,
        "project_analysis": _compact_project_analysis(state.get("project_analysis", {})),
        "code_review": _compact_code_review(state.get("code_review", {})),
        "rag_result": _compact_rag_result(state.get("rag_result", {})),
        "events": _append_event(state, "reporter", "reporter", "最终报告生成完成"),
    }
    return {
        **state,
        "final_report": report,
        "result": result,
        "events": result["events"],
    }


def _build_collaboration_report(state: StudioState) -> str:
    analysis = state.get("project_analysis", {})
    review = state.get("code_review", {})
    rag = state.get("rag_result", {})
    notes = "\n".join(f"- {item}" for item in state.get("supervisor_notes", []))
    stack = ", ".join(analysis.get("tech_stack", [])) or "暂未识别"
    return f"""# 多 Agent 协作报告

## 目标

{state['goal']}

## 项目分析摘要

- 项目名称：{analysis.get('scan', {}).get('project_name', '未知')}
- 技术栈：{stack}
- 质量评分：{analysis.get('quality_score', 'N/A')}

## 代码审查摘要

- 审查评分：{review.get('score', 'N/A')}
- 问题数量：{len(review.get('findings', []))}

## RAG 知识加工摘要

- 文档数量：{len(rag.get('documents', []))}
- 切片数量：{len(rag.get('chunks', []))}

## Supervisor 意见

{notes}
"""


def _compact_project_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_name": analysis.get("scan", {}).get("project_name"),
        "tech_stack": analysis.get("tech_stack", []),
        "quality_score": analysis.get("quality_score"),
        "risks": analysis.get("risks", []),
        "suggestions": analysis.get("suggestions", []),
    }


def _compact_code_review(review: dict[str, Any]) -> dict[str, Any]:
    return {
        "score": review.get("score"),
        "finding_count": len(review.get("findings", [])),
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


def _build_collaboration_graph():
    graph = StateGraph(StudioState)
    graph.add_node("planner", _planner_node)
    graph.add_node("project_analyzer", _project_analyzer_subgraph_node)
    graph.add_node("code_reviewer", _code_reviewer_subgraph_node)
    graph.add_node("rag_processor", _rag_processor_subgraph_node)
    graph.add_node("supervisor", _supervisor_node)
    graph.add_node("human_review", _human_review_node)
    graph.add_node("reporter", _reporter_node)

    graph.set_entry_point("planner")
    graph.add_edge("planner", "project_analyzer")
    graph.add_edge("project_analyzer", "code_reviewer")
    graph.add_edge("code_reviewer", "rag_processor")
    graph.add_edge("rag_processor", "supervisor")
    graph.add_conditional_edges("supervisor", _should_review, {"review": "human_review", "report": "reporter"})
    graph.add_edge("human_review", "reporter")
    graph.add_edge("reporter", END)
    return graph.compile()


collaboration_graph = _build_collaboration_graph()
