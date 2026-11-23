from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agents.code_review_tools import review_project
from app.agents.learning_tools import build_learning_plan
from app.agents.project_tools import (
    analyze_modules,
    extract_api_hints,
    generate_findings,
    generate_report,
    identify_tech_stack,
    quality_score,
    scan_project,
)
from app.agents.rag_tools import process_knowledge
from app.agents.workflow_tools import run_workflow
from app.providers.mcp_provider import mcp_provider
from app.skills.base import SkillContext


@dataclass
class BuiltinSkill:
    code: str
    name: str
    description: str
    category: str
    execution_type: str
    permissions: list[str]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    source_plugin: str = "official-devagent-skills"
    default_input: dict[str, Any] = field(default_factory=dict)

    def execute(self, context: SkillContext, input_data: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class CodeReviewSkill(BuiltinSkill):
    def __init__(self) -> None:
        super().__init__(
            code="code.review",
            name="Code Review",
            description="Review project code with deterministic rules and governance suggestions.",
            category="code",
            execution_type="agent",
            permissions=["filesystem.read", "llm.call"],
            input_schema={"project_path": "string", "max_files": "number"},
            output_schema={"score": "number", "findings": "array", "report_markdown": "string"},
            default_input={"project_path": ".", "max_files": 120},
        )

    def execute(self, context: SkillContext, input_data: dict[str, Any]) -> dict[str, Any]:
        return review_project(str(input_data["project_path"]), int(input_data.get("max_files") or 120))


class RagChunkSkill(BuiltinSkill):
    def __init__(self) -> None:
        super().__init__(
            code="rag.chunk",
            name="RAG Chunking",
            description="Extract documents, chunks, keywords and FAQ candidates for project knowledge processing.",
            category="rag",
            execution_type="agent",
            permissions=["filesystem.read", "rag.write"],
            input_schema={"project_path": "string", "max_files": "number"},
            output_schema={"document_count": "number", "chunk_count": "number", "keywords": "array"},
            default_input={"project_path": ".", "max_files": 120},
        )

    def execute(self, context: SkillContext, input_data: dict[str, Any]) -> dict[str, Any]:
        result = process_knowledge(str(input_data["project_path"]), int(input_data.get("max_files") or 120))
        return {
            **result,
            "document_count": len(result.get("documents", [])),
            "chunk_count": len(result.get("chunks", [])),
        }


class LearningCoachSkill(BuiltinSkill):
    def __init__(self) -> None:
        super().__init__(
            code="learning.coach",
            name="Learning Coach",
            description="Generate staged learning plans and coaching checkpoints for a topic or project.",
            category="learning",
            execution_type="agent",
            permissions=["llm.call"],
            input_schema={"topic": "string", "level": "string", "days": "number", "goal": "string"},
            output_schema={"plan": "array", "quiz": "array", "report_markdown": "string"},
            default_input={"topic": "DevAgent Studio", "level": "beginner", "days": 5},
        )

    def execute(self, context: SkillContext, input_data: dict[str, Any]) -> dict[str, Any]:
        return build_learning_plan(
            str(input_data.get("topic") or "DevAgent Studio"),
            str(input_data.get("level") or "beginner"),
            int(input_data.get("days") or 5),
            input_data.get("goal"),
        )


class McpFilesystemSkill(BuiltinSkill):
    def __init__(self) -> None:
        super().__init__(
            code="mcp.filesystem",
            name="MCP Filesystem",
            description="Call local or real MCP-shaped filesystem tools for listing and reading project files.",
            category="mcp",
            execution_type="mcp_tool",
            permissions=["filesystem.read", "mcp.call"],
            input_schema={"root_path": "string", "tool_name": "string", "file_path": "string", "max_files": "number"},
            output_schema={"provider": "string", "files": "array", "content": "string"},
            default_input={"root_path": ".", "tool_name": "filesystem.list", "max_files": 80},
        )

    def execute(self, context: SkillContext, input_data: dict[str, Any]) -> dict[str, Any]:
        root_path = str(input_data.get("root_path") or input_data.get("project_path") or ".")
        tool_name = str(input_data.get("tool_name") or "filesystem.list")
        if tool_name in {"filesystem.read", "read_file", "read_text_file"}:
            return mcp_provider.read_file(root_path, str(input_data.get("file_path") or "README.md"), int(input_data.get("max_chars") or 4000))
        if tool_name in {"filesystem.tree", "directory_tree"}:
            return mcp_provider.call_tool("directory_tree", {"root_path": root_path, "path": input_data.get("path", "."), "max_depth": input_data.get("max_depth", 2)})
        return mcp_provider.list_files(root_path, int(input_data.get("max_files") or 80))


class GitAnalysisSkill(BuiltinSkill):
    def __init__(self) -> None:
        super().__init__(
            code="git.analysis",
            name="Git Analysis",
            description="Read git status and recent commits to summarize repository activity and change risk.",
            category="git",
            execution_type="mcp_tool",
            permissions=["git.read", "filesystem.read"],
            input_schema={"repo_path": "string", "limit": "number"},
            output_schema={"status": "array", "commits": "array", "summary": "string"},
            default_input={"repo_path": ".", "limit": 8},
        )

    def execute(self, context: SkillContext, input_data: dict[str, Any]) -> dict[str, Any]:
        repo_path = str(input_data.get("repo_path") or input_data.get("project_path") or ".")
        status = mcp_provider.git_status(repo_path)
        log = mcp_provider.git_log(repo_path, int(input_data.get("limit") or 8))
        dirty_count = len(status.get("stdout", []))
        commit_count = len(log.get("commits", []))
        return {
            "repo_path": repo_path,
            "status": status,
            "log": log,
            "summary": f"Git analysis completed: {dirty_count} changed file(s), {commit_count} recent commit(s).",
        }


class SecurityScanSkill(BuiltinSkill):
    def __init__(self) -> None:
        super().__init__(
            code="security.scan",
            name="Security Scan",
            description="Scan deterministic security findings such as hard-coded secrets and risky SQL construction.",
            category="security",
            execution_type="rule",
            permissions=["filesystem.read"],
            input_schema={"project_path": "string", "max_files": "number"},
            output_schema={"findings": "array", "risk_level": "string", "report_markdown": "string"},
            default_input={"project_path": ".", "max_files": 160},
        )

    def execute(self, context: SkillContext, input_data: dict[str, Any]) -> dict[str, Any]:
        review = review_project(str(input_data["project_path"]), int(input_data.get("max_files") or 160))
        findings = [item for item in review.get("findings", []) if item.get("category") == "security"]
        risk_level = "critical" if any(item.get("severity") == "critical" for item in findings) else "high" if findings else "low"
        lines = [
            "# Security Scan Report",
            "",
            f"- Findings: {len(findings)}",
            f"- Risk level: {risk_level}",
            "",
            "## Findings",
        ]
        lines.extend(f"- `{item.get('file')}:{item.get('line')}` {item.get('message')}" for item in findings[:20])
        if not findings:
            lines.append("- No deterministic security finding was detected.")
        return {"findings": findings, "risk_level": risk_level, "report_markdown": "\n".join(lines)}


class TestcaseGenerateSkill(BuiltinSkill):
    def __init__(self) -> None:
        super().__init__(
            code="testcase.generate",
            name="Test Case Generator",
            description="Generate focused API, workflow, RAG, MCP and fallback test suggestions from project structure.",
            category="testing",
            execution_type="rule",
            permissions=["filesystem.read"],
            input_schema={"project_path": "string", "max_files": "number", "focus": "string"},
            output_schema={"test_cases": "array", "report_markdown": "string"},
            default_input={"project_path": ".", "max_files": 120, "focus": "workflow"},
        )

    def execute(self, context: SkillContext, input_data: dict[str, Any]) -> dict[str, Any]:
        scan = scan_project(str(input_data["project_path"]), int(input_data.get("max_files") or 120))
        stack = identify_tech_stack(scan)
        modules = analyze_modules(scan)
        focus = str(input_data.get("focus") or "workflow")
        cases = [
            {"case_id": "api_smoke", "target": "FastAPI routes", "assertion": "core API returns 2xx or structured 4xx"},
            {"case_id": "workflow_compile", "target": "Workflow compiler", "assertion": "nodes and edges validate before execution"},
            {"case_id": "harness_state", "target": "Harness Runtime", "assertion": "task status and timeline events are persisted"},
            {"case_id": "rag_hit", "target": "RAG retrieval", "assertion": "known project question returns relevant source chunks"},
            {"case_id": "mcp_contract", "target": "MCP tools", "assertion": "tool approval, call result and log are consistent"},
        ]
        report = [
            "# Test Case Suggestions",
            "",
            f"- Focus: {focus}",
            f"- Tech stack: {', '.join(stack) or 'unknown'}",
            f"- Modules: {', '.join(modules[:8]) or 'unknown'}",
            "",
            "## Recommended Cases",
            *[f"- **{case['case_id']}**: {case['target']} - {case['assertion']}" for case in cases],
        ]
        return {"project_name": scan["project_name"], "test_cases": cases, "report_markdown": "\n".join(report)}


class ArchitectureReportSkill(BuiltinSkill):
    def __init__(self) -> None:
        super().__init__(
            code="architecture.report",
            name="Architecture Report",
            description="Build a project architecture report with modules, tech stack, risks and governance suggestions.",
            category="architecture",
            execution_type="agent",
            permissions=["filesystem.read", "llm.call"],
            input_schema={"project_path": "string", "max_files": "number"},
            output_schema={"tech_stack": "array", "modules": "array", "report_markdown": "string"},
            default_input={"project_path": ".", "max_files": 160},
        )

    def execute(self, context: SkillContext, input_data: dict[str, Any]) -> dict[str, Any]:
        scan = scan_project(str(input_data["project_path"]), int(input_data.get("max_files") or 160))
        stack = identify_tech_stack(scan)
        modules = analyze_modules(scan)
        api_hints = extract_api_hints(scan)
        risks, suggestions = generate_findings(scan, stack, modules)
        score = quality_score(scan, stack, risks)
        return {
            "scan": scan,
            "tech_stack": stack,
            "modules": modules,
            "api_hints": api_hints,
            "risks": risks,
            "suggestions": suggestions,
            "quality_score": score,
            "report_markdown": generate_report(scan, stack, modules, api_hints, risks, suggestions, score),
        }


class WorkflowRunSkill(BuiltinSkill):
    def __init__(self) -> None:
        super().__init__(
            code="workflow.run",
            name="Workflow Runner",
            description="Execute up to 20 workflow nodes with the real graph compiler. Human review requires the persisted task API.",
            category="workflow",
            execution_type="workflow",
            permissions=["workflow.run"],
            input_schema={"workflow_name": "string", "input_text": "string", "nodes": "array", "edges": "array", "project_path": "string", "max_files": "number"},
            output_schema={"events": "array", "output": "string"},
            default_input={"workflow_name": "skill_preview", "input_text": "Run skill workflow", "nodes": []},
        )

    def execute(self, context: SkillContext, input_data: dict[str, Any]) -> dict[str, Any]:
        return run_workflow(
            str(input_data.get("workflow_name") or "skill_preview"),
            str(input_data.get("input_text") or ""),
            list(input_data.get("nodes") or []),
            edges=list(input_data.get("edges") or []),
            project_path=input_data.get("project_path"),
            max_files=int(input_data.get("max_files") or 100),
        )


def builtin_plugin() -> dict[str, Any]:
    return {
        "plugin_id": "official-devagent-skills",
        "name": "Official DevAgent Skills",
        "version": "1.0.0",
        "source_type": "builtin",
        "source_url": "",
        "author": "DevAgent Studio",
        "description": "Built-in runtime skills for code review, RAG, learning, MCP, Git, security, tests and architecture reports.",
        "enabled": True,
    }


def builtin_skills() -> list[BuiltinSkill]:
    return [
        CodeReviewSkill(),
        RagChunkSkill(),
        LearningCoachSkill(),
        McpFilesystemSkill(),
        GitAnalysisSkill(),
        SecurityScanSkill(),
        TestcaseGenerateSkill(),
        ArchitectureReportSkill(),
        WorkflowRunSkill(),
    ]
