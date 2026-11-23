"""Validated planning and deterministic review decisions for the collaboration graph."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.providers.llm_provider import llm_provider

AGENTS = ("project_analyzer", "code_reviewer", "rag_processor")


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: Literal["project_analyzer", "code_reviewer", "rag_processor"]
    objective: str = Field(min_length=1, max_length=300)


class CollaborationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[PlanStep] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique_agents(self):
        if len({step.agent for step in self.steps}) != len(self.steps):
            raise ValueError("Each agent can run at most once in a plan")
        return self


def fallback_plan(goal: str, scenario: str = "") -> dict:
    text = goal.lower()
    selected = []
    if any(word in text for word in ("架构", "结构", "入门", "了解", "architecture", "onboard", "structure")):
        selected.append("project_analyzer")
    if any(word in text for word in ("审查", "风险", "安全", "review", "risk", "security", "bug")):
        selected.append("code_reviewer")
    if any(word in text for word in ("知识", "文档", "切片", "rag", "knowledge", "document")):
        selected.append("rag_processor")
    if scenario in {"onboarding", "governance"} or not selected:
        selected = list(AGENTS)
    objectives = {"project_analyzer": "分析项目结构、技术栈与模块边界",
                  "code_reviewer": "检查代码风险并保留文件与行号证据",
                  "rag_processor": "提取项目知识材料与切片，标明尚未入库"}
    return {"steps": [{"agent": agent, "objective": objectives[agent]} for agent in selected]}


def create_plan(goal: str, project_path: str | None, scenario: str = "") -> tuple[dict, dict]:
    fallback = fallback_plan(goal, scenario)
    response = llm_provider.generate_with_status(
        "你是协作任务规划器。只输出 JSON，结构为 {\"steps\":[{\"agent\":\"project_analyzer\",\"objective\":\"任务目标\"}]}。"
        "只能选择 project_analyzer、code_reviewer、rag_processor，每个最多一次，共 1–3 步。"
        "按任务需要选择和排序；不要输出命令、路径参数、权限、工具或其他字段。",
        f"目标：{goal}\n项目：{project_path}\n业务场景：{scenario or 'custom'}",
        json.dumps(fallback, ensure_ascii=False), agent="planner", prompt_version="collaboration_plan.v1",
    )
    metadata = {"source": "fallback" if response.get("fallback_used") else "llm",
                "trace_id": response.get("trace_id"), "fallback_reason": response.get("error_message")}
    try:
        text = response["text"].strip()
        if text.startswith("```json") and text.endswith("```"):
            text = text[7:-3].strip()
        plan = CollaborationPlan.model_validate_json(text).model_dump()
        # These business scenarios promise all three kinds of evidence.
        if scenario in {"onboarding", "governance"} and {s["agent"] for s in plan["steps"]} != set(AGENTS):
            raise ValueError("Business scenario requires architecture, code review and knowledge evidence")
    except (ValueError, TypeError, KeyError) as exc:
        plan = fallback
        metadata.update(source="fallback", fallback_reason=f"Invalid executable plan: {str(exc)[:300]}")
    return plan, metadata


def quality_gate(state: dict) -> dict:
    review = state.get("code_review") or {}
    findings = review.get("findings") or []
    records = review.get("suggestion_records") or []
    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    levels = [str(item.get("severity") or item.get("risk_level") or "low").lower()
              for item in [*findings, *records] if isinstance(item, dict)]
    risk = max(levels, key=lambda value: order.get(value, 0), default="low")
    if risk not in order:
        risk = "low"
    reasons = []
    score = review.get("score")
    if isinstance(score, (int, float)) and score < 70:
        if order[risk] < order["high"]:
            risk = "high"
        reasons.append("代码审查评分低于 70")
    if risk in {"high", "critical"}:
        reasons.append(f"发现 {risk} 级代码风险")
    if any(item.get("review_required") for item in records):
        reasons.append("治理建议要求人工确认")
    if state.get("require_human_review", True):
        reasons.append("任务配置要求人工审核")
    executed = state.get("completed_agents", [])
    gaps = []
    if "code_reviewer" not in executed:
        risk = "unknown"
        gaps.append("未执行代码审查，无法判定代码风险")
    analysis = state.get("project_analysis") or {}
    if "project_analyzer" in executed and not analysis.get("scan", {}).get("files"):
        gaps.append("项目扫描未提供有效文件数量，请检查路径和扫描范围")
    return {"risk_level": risk, "review_required": bool(reasons), "reasons": reasons,
            "coverage_gaps": gaps, "executed_agents": executed}


def evidence_records(state: dict) -> list[dict]:
    findings = (state.get("code_review") or {}).get("findings", [])
    # Preserve the most consequential evidence first, keeping report context bounded.
    severity = {"critical": 3, "high": 2, "medium": 1, "low": 0}
    ordered = sorted(findings, key=lambda item: severity.get(str(item.get("severity", "low")), 0), reverse=True)
    return [{"evidence_id": f"finding-{index + 1}", "file": item.get("file") or item.get("path"),
             **{key: item.get(key) for key in ("line", "severity", "category")},
             "message": str(item.get("message") or "")[:500]}
            for index, item in enumerate(ordered[:30])]


def evidence_markdown(state: dict) -> str:
    gate = state.get("quality_gate") or quality_gate(state)
    lines = ["## 执行范围与证据", "", "- 实际执行：" + ", ".join(state.get("completed_agents", [])),
             f"- 规则风险等级：{gate['risk_level']}"]
    lines.extend(f"- 范围限制：{gap}" for gap in gate["coverage_gaps"])
    records = evidence_records(state)
    for item in records:
        lines.append(f"- [{item['evidence_id']}] {item['severity']} · `{item['file']}:{item['line']}` — {item['message']}")
    if not records:
        lines.append("- 当前执行范围内没有可列出的代码问题证据；这不代表已覆盖整个项目。")
    if len((state.get("code_review") or {}).get("findings", [])) > len(records):
        lines.append("- 此处仅列出风险最高的 30 项，完整结果保留在任务检查点。")
    return "\n".join(lines)
