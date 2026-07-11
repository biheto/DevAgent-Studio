from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.agents.marketplace_tools import check_permission, list_tools
from app.benchmark_runner import (
    run_collaboration_benchmark,
    run_llm_benchmark,
    run_mcp_benchmark,
    run_rag_benchmark,
    run_workflow_benchmark,
)
from app.graphs.project_analyzer_graph import project_analyzer_graph
from app.graphs.studio_graphs import (
    code_review_graph,
    collaboration_graph,
    learning_coach_graph,
    rag_process_graph,
)
from app.graphs.collaboration_runner import run_collaboration_task
from app.graphs.workflow_compiler import (
    resume_task_workflow,
    run_compiled_workflow,
    run_task_workflow,
    validate_workflow_definition,
)
from app.harness.events import utc_now_iso
from app.harness.policy import tool_policy
from app.harness.runtime import harness_runtime
from app.persistence.rag_store import rag_store
from app.persistence.sqlite_store import task_store
from app.providers.llm_provider import llm_provider
from app.providers.mcp_provider import mcp_provider
from app.schemas.project import ProjectAnalyzeRequest, ProjectAnalyzeResponse
from app.schemas.studio import (
    BenchmarkRunRequest,
    BenchmarkRunResponse,
    CodeReviewRequest,
    CodeReviewResponse,
    CollaborationRequest,
    CollaborationResponse,
    HumanReviewRequest,
    HumanReviewResponse,
    KnowledgeNoteRequest,
    KnowledgeNoteResponse,
    SkillDefinitionRequest,
    SkillDefinitionResponse,
    SkillExecuteRequest,
    SkillInstallRequest,
    SkillToggleRequest,
    LearningCoachRequest,
    LearningCoachResponse,
    LearningChatRequest,
    LearningChatResponse,
    LearningPlanCreateRequest,
    LearningPlanResponse,
    LearningPlanStatusRequest,
    McpFileListRequest,
    McpFileReadRequest,
    McpGitRequest,
    McpServerConfigRequest,
    McpServerConfigResponse,
    McpToolApprovalRequest,
    McpToolCallRequest,
    McpToolToggleRequest,
    RagIngestRequest,
    RagIngestResponse,
    RagProcessRequest,
    RagProcessResponse,
    RagQueryRequest,
    RagQueryResponse,
    ReviewActionRequest,
    ReviewActionResponse,
    TaskRunRequest,
    TaskQuestionRequest,
    TaskQuestionResponse,
    TaskRunResponse,
    ToolPermissionRequest,
    ToolPermissionResponse,
    WorkflowRunRequest,
    WorkflowRunResponse,
    WorkflowSaveRequest,
    WorkflowSaveResponse,
    WorkflowValidateRequest,
    WorkflowValidateResponse,
)

router = APIRouter(prefix="/api/v1", tags=["DevAgent Studio"])


@router.post("/projects/analyze", response_model=ProjectAnalyzeResponse, tags=["Project Analyzer"])
def analyze_project(request: ProjectAnalyzeRequest) -> ProjectAnalyzeResponse:
    try:
        result = project_analyzer_graph.invoke(request.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    scan = result["scan"]
    return ProjectAnalyzeResponse(
        project_path=scan["root"],
        project_name=scan["project_name"],
        file_count=len(scan["files"]),
        directory_count=len(scan["directories"]),
        tech_stack=result.get("tech_stack", []),
        key_files=scan.get("key_files", []),
        modules=result.get("modules", []),
        risks=result.get("risks", []),
        suggestions=result.get("suggestions", []),
        quality_score=result.get("quality_score", 0),
        report_markdown=result.get("report_markdown", ""),
    )


@router.post("/projects/analyze/stream", tags=["Project Analyzer"])
async def analyze_project_stream(request: ProjectAnalyzeRequest) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event in project_analyzer_graph.astream_events(request.model_dump(), version="v2"):
                payload = {
                    "type": "graph_event",
                    "event": event.get("event", "unknown"),
                    "node": event.get("name", "graph"),
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'content': str(exc)}, ensure_ascii=False)}\n\n"
        finally:
            yield "data: {\"type\": \"complete\", \"completed\": true}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/code/review", response_model=CodeReviewResponse, tags=["Code Review Agent"])
def review_code(request: CodeReviewRequest) -> CodeReviewResponse:
    try:
        result = code_review_graph.invoke(request.model_dump())["result"]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    scan = result["scan"]
    return CodeReviewResponse(
        project_path=scan["root"],
        reviewed_files=len(scan["files"]),
        findings=result["findings"],
        risks=result["risks"],
        suggestions=result["suggestions"],
        suggestion_records=result.get("suggestion_records", []),
        score=result["score"],
        report_markdown=result["report_markdown"],
    )


@router.post("/rag/process", response_model=RagProcessResponse, tags=["RAG Knowledge Agent"])
def process_rag(request: RagProcessRequest) -> RagProcessResponse:
    try:
        result = rag_process_graph.invoke(request.model_dump())["result"]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    scan = result["scan"]
    return RagProcessResponse(
        project_path=scan["root"],
        document_count=len(result["documents"]),
        chunk_count=len(result["chunks"]),
        keywords=result["keywords"],
        faq=result["faq"],
        report_markdown=result["report_markdown"],
    )


@router.post("/rag/ingest", response_model=RagIngestResponse, tags=["RAG Knowledge Agent"])
def ingest_rag(request: RagIngestRequest) -> RagIngestResponse:
    try:
        result = rag_process_graph.invoke(request.model_dump())["result"]
        saved = rag_store.ingest(request.collection, result["documents"], result["chunks"])
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RagIngestResponse(
        collection=request.collection,
        document_count=saved["document_count"],
        chunk_count=saved["chunk_count"],
        keywords=result["keywords"],
    )


@router.post("/rag/query", response_model=RagQueryResponse, tags=["RAG Knowledge Agent"])
def query_rag(request: RagQueryRequest) -> RagQueryResponse:
    results = rag_store.query(request.collection, request.question, request.limit)
    return RagQueryResponse(collection=request.collection, question=request.question, results=results)


@router.get("/rag/documents", tags=["RAG Knowledge Agent"])
def list_rag_documents(collection: str | None = None) -> dict[str, object]:
    return {"documents": rag_store.list_documents(collection)}


@router.get("/rag/status", tags=["RAG Knowledge Agent"])
def get_rag_status() -> dict[str, object]:
    status = rag_store.status() if hasattr(rag_store, "status") else {"kind": "unknown"}
    return {"store": status}


@router.post("/learning/coach/plan", response_model=LearningCoachResponse, tags=["Learning Coach Agent"])
def create_learning_plan(request: LearningCoachRequest) -> LearningCoachResponse:
    result = learning_coach_graph.invoke(request.model_dump())["result"]
    return LearningCoachResponse(
        topic=request.topic,
        level=request.level,
        days=request.days,
        plan=result["plan"],
        quiz=result["quiz"],
        report_markdown=result["report_markdown"],
    )


@router.post("/tasks/{task_id}/learning-plan", response_model=LearningPlanResponse, tags=["Learning Coach Agent"])
def create_task_learning_plan(task_id: str, request: LearningPlanCreateRequest) -> LearningPlanResponse:
    task = task_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    goal = request.goal or str(task.get("goal") or "")
    result = learning_coach_graph.invoke(
        {
            "topic": request.topic,
            "level": request.level,
            "days": request.days,
            "goal": goal,
        }
    )["result"]
    plan_id = f"lp_{uuid4().hex}"
    saved = task_store.save_learning_plan(
        plan_id=plan_id,
        task_id=task_id,
        topic=request.topic,
        level=request.level,
        plan=result["plan"],
        quiz=result["quiz"],
        report_markdown=result["report_markdown"],
    )
    task_store.append_event(
        {
            "event_id": f"evt_{uuid4().hex}",
            "task_id": task_id,
            "type": "learning_plan",
            "node": "learning_coach",
            "agent": "learning_coach",
            "status": "active",
            "content": f"Learning plan created: {request.topic}",
            "data": {
                "plan_id": plan_id,
                "topic": request.topic,
                "level": request.level,
                "days": request.days,
                "comment": request.comment,
            },
        }
    )
    task_store.save_artifact(task_id, "learning_plan", request.topic, saved)
    rag_store.add_note("project-memory", f"learning/{plan_id}", result["report_markdown"])
    return LearningPlanResponse(plan=saved)


@router.get("/learning/plans", tags=["Learning Coach Agent"])
def list_learning_plans(task_id: str | None = None) -> dict[str, object]:
    return {"plans": task_store.list_learning_plans(task_id)}


@router.patch("/learning/plans/{plan_id}", response_model=LearningPlanResponse, tags=["Learning Coach Agent"])
def update_learning_plan(plan_id: str, request: LearningPlanStatusRequest) -> LearningPlanResponse:
    if request.status not in {"active", "completed", "paused"}:
        raise HTTPException(status_code=400, detail="status must be active, completed, or paused")
    updated = task_store.update_learning_plan_status(plan_id, request.status)
    if not updated:
        raise HTTPException(status_code=404, detail="Learning plan not found")
    task_store.append_event(
        {
            "event_id": f"evt_{uuid4().hex}",
            "task_id": updated["task_id"],
            "type": "learning_plan_status",
            "node": "learning_coach",
            "agent": "learning_coach",
            "status": request.status,
            "content": f"Learning plan status updated to {request.status}: {updated['topic']}",
            "data": {"plan_id": plan_id, "status": request.status},
        }
    )
    return LearningPlanResponse(plan=updated)


@router.get("/mcp/tools", tags=["MCP Tool Marketplace"])
def get_tools() -> dict[str, object]:
    return {
        "provider": mcp_provider.status(),
        "tools": list_tools(),
        "registered_tools": mcp_provider.real.list_tools(),
    }


@router.get("/mcp/status", tags=["MCP Tool Marketplace"])
def get_mcp_status() -> dict[str, object]:
    return mcp_provider.status()


@router.get("/mcp/servers", tags=["MCP Tool Marketplace"])
def list_mcp_servers() -> dict[str, object]:
    return {"servers": mcp_provider.real.list_servers()}


@router.post("/mcp/servers", response_model=McpServerConfigResponse, tags=["MCP Tool Marketplace"])
def save_mcp_server(request: McpServerConfigRequest) -> McpServerConfigResponse:
    server = mcp_provider.real.save_server(request.model_dump())
    return McpServerConfigResponse(server=server)


@router.post("/mcp/servers/{server_id}/enabled", tags=["MCP Tool Marketplace"])
def set_mcp_server_enabled(server_id: str, request: McpToolToggleRequest) -> dict[str, object]:
    server = mcp_provider.real.set_server_enabled(server_id, request.enabled)
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return {"server": server}


@router.post("/mcp/servers/{server_id}/discover", tags=["MCP Tool Marketplace"])
def discover_mcp_server_tools(server_id: str) -> dict[str, object]:
    try:
        return mcp_provider.real.discover_tools(server_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/mcp/registered-tools", tags=["MCP Tool Marketplace"])
def list_registered_mcp_tools(server_id: str | None = None, agent_code: str = "workflow_runner") -> dict[str, object]:
    tools = []
    for tool in mcp_provider.real.list_tools(server_id):
        approval = mcp_provider.real.check_approval(agent_code, tool["server_id"], tool["name"])
        stored_approval = task_store.get_mcp_tool_approval(agent_code, tool["server_id"], tool["name"])
        tools.append(
            {
                **tool,
                "approval_agent_code": agent_code,
                "approval_allowed": approval["allowed"],
                "approval_reason": approval["reason"],
                "approval_updated_at": stored_approval.get("updated_at") if stored_approval else None,
                "approval_recorded": stored_approval is not None,
            }
        )
    return {"tools": tools}


@router.post("/mcp/registered-tools/{server_id}/{tool_name}/enabled", tags=["MCP Tool Marketplace"])
def set_registered_mcp_tool_enabled(server_id: str, tool_name: str, request: McpToolToggleRequest) -> dict[str, object]:
    tool = mcp_provider.real.set_tool_enabled(server_id, tool_name, request.enabled)
    if not tool:
        raise HTTPException(status_code=404, detail="MCP tool not found")
    return {"tool": tool}


@router.post("/mcp/tools/approval", tags=["MCP Tool Marketplace"])
def set_mcp_tool_approval(request: McpToolApprovalRequest) -> dict[str, object]:
    return {"approval": mcp_provider.real.set_approval(request.agent_code, request.server_id, request.tool_name, request.allowed, request.reason)}


@router.post("/mcp/tools/call", tags=["MCP Tool Marketplace"])
def call_mcp_tool(request: McpToolCallRequest) -> dict[str, object]:
    try:
        return mcp_provider.call_tool(
            request.tool_name,
            request.arguments,
            server_id=request.server_id,
            agent_code=request.agent_code,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/mcp/tool-call-logs", tags=["MCP Tool Marketplace"])
def list_mcp_tool_call_logs(limit: int = 100, server_id: str | None = None) -> dict[str, object]:
    return {"logs": mcp_provider.real.list_call_logs(limit=limit, server_id=server_id)}


@router.post("/benchmarks/mcp/run", response_model=BenchmarkRunResponse, tags=["Benchmark"])
def run_mcp_benchmark_api(request: BenchmarkRunRequest) -> BenchmarkRunResponse:
    try:
        run = run_mcp_benchmark(request.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BenchmarkRunResponse(run=run)


@router.post("/benchmarks/llm/run", response_model=BenchmarkRunResponse, tags=["Benchmark"])
def run_llm_benchmark_api(request: BenchmarkRunRequest) -> BenchmarkRunResponse:
    try:
        run = run_llm_benchmark(request.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BenchmarkRunResponse(run=run)


@router.post("/benchmarks/rag/run", response_model=BenchmarkRunResponse, tags=["Benchmark"])
def run_rag_benchmark_api(request: BenchmarkRunRequest) -> BenchmarkRunResponse:
    try:
        run = run_rag_benchmark(request.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BenchmarkRunResponse(run=run)


@router.post("/benchmarks/workflow/run", response_model=BenchmarkRunResponse, tags=["Benchmark"])
def run_workflow_benchmark_api(request: BenchmarkRunRequest) -> BenchmarkRunResponse:
    try:
        run = run_workflow_benchmark(request.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BenchmarkRunResponse(run=run)


@router.post("/benchmarks/collaboration/run", response_model=BenchmarkRunResponse, tags=["Benchmark"])
def run_collaboration_benchmark_api(request: BenchmarkRunRequest) -> BenchmarkRunResponse:
    try:
        run = run_collaboration_benchmark(request.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BenchmarkRunResponse(run=run)


@router.get("/benchmarks", tags=["Benchmark"])
def list_benchmarks(limit: int = 50, benchmark_type: str | None = None) -> dict[str, object]:
    return {"runs": task_store.list_benchmark_runs(limit=limit, benchmark_type=benchmark_type)}


@router.get("/benchmarks/{run_id}", tags=["Benchmark"])
def get_benchmark(run_id: str) -> dict[str, object]:
    run = task_store.get_benchmark_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Benchmark run not found")
    return {"run": run}


@router.get("/llm/status", tags=["LLM Provider"])
def get_llm_status() -> dict[str, object]:
    return llm_provider.status()


@router.get("/llm/traces", tags=["LLM Provider"])
def list_llm_traces(limit: int = 50, agent: str | None = None) -> dict[str, object]:
    return {"traces": task_store.list_llm_traces(limit=limit, agent=agent)}


@router.get("/llm/prompts", tags=["LLM Provider"])
def list_llm_prompts(agent: str | None = None) -> dict[str, object]:
    return {"prompts": llm_provider.list_prompt_versions(agent)}


@router.post("/llm/prompts", tags=["LLM Provider"])
def save_llm_prompt(payload: dict[str, object]) -> dict[str, object]:
    agent = str(payload.get("agent") or "").strip()
    prompt_version = str(payload.get("prompt_version") or "").strip()
    title = str(payload.get("title") or "").strip()
    if not agent or not prompt_version or not title:
        raise HTTPException(status_code=400, detail="agent, prompt_version and title are required")
    prompt = llm_provider.save_prompt_version(
        {
            "agent": agent,
            "prompt_family": str(payload.get("prompt_family") or "").strip() or None,
            "prompt_version": prompt_version,
            "title": title,
            "description": str(payload.get("description") or "").strip(),
            "system_suffix": str(payload.get("system_suffix") or "").strip(),
            "is_active": bool(payload.get("is_active")),
        }
    )
    if payload.get("is_active"):
        prompt = llm_provider.set_active_prompt_version(agent, prompt_version) or prompt
    return {"prompt": prompt}


@router.post("/llm/prompts/active", tags=["LLM Provider"])
def set_active_llm_prompt(payload: dict[str, str]) -> dict[str, object]:
    agent = payload.get("agent")
    prompt_version = payload.get("prompt_version")
    if not agent or not prompt_version:
        raise HTTPException(status_code=400, detail="agent and prompt_version are required")
    prompt = llm_provider.set_active_prompt_version(agent, prompt_version)
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt version not found")
    return {"prompt": prompt, "active_prompts": llm_provider.active_prompt_map()}


@router.post("/llm/prompts/ab-test", tags=["LLM Provider"])
def run_llm_prompt_ab_test(payload: dict[str, object]) -> dict[str, object]:
    agent = str(payload.get("agent") or "").strip()
    prompt_a = str(payload.get("prompt_a") or "").strip()
    prompt_b = str(payload.get("prompt_b") or "").strip()
    user_prompt = str(payload.get("user_prompt") or "").strip()
    if not agent or not prompt_a or not prompt_b or not user_prompt:
        raise HTTPException(status_code=400, detail="agent, prompt_a, prompt_b and user_prompt are required")
    result = llm_provider.run_prompt_ab_test(
        agent=agent,
        prompt_a=prompt_a,
        prompt_b=prompt_b,
        system_prompt=str(
            payload.get("system_prompt")
            or "你是 DevAgent Studio 的 Prompt A/B 测试执行器。请基于输入给出结构清晰、可验证、可行动的中文回答。"
        ).strip(),
        user_prompt=user_prompt,
        fallback=str(payload.get("fallback") or "LLM 未配置或调用失败，返回 fallback。").strip(),
    )
    return result


@router.get("/llm/usage", tags=["LLM Provider"])
def get_llm_usage(limit: int = 500, agent: str | None = None) -> dict[str, object]:
    return llm_provider.usage_dashboard(limit=limit, agent=agent)


@router.post("/mcp/tools/allow-check", response_model=ToolPermissionResponse, tags=["MCP Tool Marketplace"])
def allow_check(request: ToolPermissionRequest) -> ToolPermissionResponse:
    return ToolPermissionResponse(**check_permission(request.agent_code, request.tool_code))


@router.post("/mcp/filesystem/list", tags=["MCP Tool Marketplace"])
def mcp_list_files(request: McpFileListRequest) -> dict[str, object]:
    tool_policy.require("project_analyzer", "file_scan")
    return mcp_provider.list_files(request.root_path, request.max_files)


@router.post("/mcp/filesystem/read", tags=["MCP Tool Marketplace"])
def mcp_read_file(request: McpFileReadRequest) -> dict[str, object]:
    tool_policy.require("project_analyzer", "file_scan")
    return mcp_provider.read_file(request.root_path, request.file_path, request.max_chars)


@router.post("/mcp/git/status", tags=["MCP Tool Marketplace"])
def mcp_git_status(request: McpGitRequest) -> dict[str, object]:
    tool_policy.require("code_reviewer", "git_read")
    return mcp_provider.git_status(request.repo_path)


@router.post("/mcp/git/log", tags=["MCP Tool Marketplace"])
def mcp_git_log(request: McpGitRequest) -> dict[str, object]:
    tool_policy.require("code_reviewer", "git_read")
    return mcp_provider.git_log(request.repo_path, request.limit)


@router.post("/workflows/run", response_model=WorkflowRunResponse, tags=["Workflow Runner"])
def run_visual_workflow(request: WorkflowRunRequest) -> WorkflowRunResponse:
    result = run_compiled_workflow(
        request.workflow_name,
        request.input_text,
        [node.model_dump() for node in request.nodes],
        [edge.model_dump() for edge in request.edges],
    )
    return WorkflowRunResponse(**result)


@router.post("/workflows/validate", response_model=WorkflowValidateResponse, tags=["Workflow Runner"])
def validate_visual_workflow(request: WorkflowValidateRequest) -> WorkflowValidateResponse:
    result = validate_workflow_definition(
        [node.model_dump() for node in request.nodes],
        [edge.model_dump() for edge in request.edges],
    )
    return WorkflowValidateResponse(**result)


@router.get("/workflows", tags=["Workflow Runner"])
def list_workflows() -> dict[str, object]:
    return {"workflows": task_store.list_workflows()}


@router.post("/workflows", response_model=WorkflowSaveResponse, tags=["Workflow Runner"])
def create_workflow(request: WorkflowSaveRequest) -> WorkflowSaveResponse:
    workflow = task_store.save_workflow(
        request.workflow_id,
        request.name,
        request.description,
        request.nodes,
        request.edges,
    )
    return WorkflowSaveResponse(workflow=workflow)


@router.get("/workflows/{workflow_id}", tags=["Workflow Runner"])
def get_workflow(workflow_id: str) -> dict[str, object]:
    workflow = task_store.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"workflow": workflow}


@router.put("/workflows/{workflow_id}", response_model=WorkflowSaveResponse, tags=["Workflow Runner"])
def update_workflow(workflow_id: str, request: WorkflowSaveRequest) -> WorkflowSaveResponse:
    workflow = task_store.save_workflow(
        workflow_id,
        request.name,
        request.description,
        request.nodes,
        request.edges,
    )
    return WorkflowSaveResponse(workflow=workflow)


@router.post("/agents/collaborate", response_model=CollaborationResponse, tags=["Multi-Agent Collaboration"])
def run_collaboration(request: CollaborationRequest) -> CollaborationResponse:
    result = collaboration_graph.invoke(request.model_dump())["result"]
    return CollaborationResponse(**result)


@router.post("/tasks/run", response_model=TaskRunResponse, tags=["Task Runtime"])
def run_task(request: TaskRunRequest) -> TaskRunResponse:
    workflow_payload = _resolve_task_workflow(request)
    context = harness_runtime.create_context(
        goal=request.goal,
        project_path=request.project_path,
        variables={
            "max_files": request.max_files,
            "require_human_review": request.require_human_review,
            "execution_mode": request.execution_mode,
            "workflow_id": request.workflow_id,
        },
    )
    try:
        result = harness_runtime.run_graph(context, run_task_workflow, workflow_payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TaskRunResponse(**result)


@router.post("/tasks/run/stream", tags=["Task Runtime"])
async def run_task_stream(request: TaskRunRequest) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        try:
            response = run_task(request)
            for event in response.events:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            payload = {
                "type": "task_result",
                "task_id": response.task_id,
                "status": response.status,
                "final_report": response.result.get("final_report"),
                "mermaid": response.result.get("mermaid"),
                "suggestions": response.result.get("suggestions"),
                "suggestion_records": response.result.get("suggestion_records"),
                "risk_level": response.result.get("risk_level"),
                "review_required": response.result.get("review_required"),
                "next_actions": response.result.get("next_actions"),
                "governance": response.result.get("governance"),
                "tool_calls": response.result.get("tool_calls"),
                "agent_outputs": response.result.get("agent_outputs"),
                "human_review_required": response.result.get("human_review_required"),
                "planned_workflow": response.result.get("planned_workflow"),
                "validation": response.result.get("validation"),
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'content': str(exc)}, ensure_ascii=False)}\n\n"
        finally:
            yield "data: {\"type\": \"complete\", \"completed\": true}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/tasks/collaborate", response_model=TaskRunResponse, tags=["Task Runtime"])
def run_collaboration_task_api(request: TaskRunRequest) -> TaskRunResponse:
    context = harness_runtime.create_context(
        goal=request.goal,
        project_path=request.project_path,
        variables={
            "max_files": request.max_files,
            "require_human_review": request.require_human_review,
            "execution_mode": "collaboration",
        },
    )
    input_state = {
        "goal": request.goal,
        "project_path": request.project_path,
        "max_files": request.max_files,
        "require_human_review": request.require_human_review,
        "input_text": request.input_text or request.goal,
    }
    try:
        result = harness_runtime.run_graph(context, run_collaboration_task, input_state)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TaskRunResponse(**result)


@router.post("/tasks/collaborate/stream", tags=["Task Runtime"])
async def run_collaboration_task_stream(request: TaskRunRequest) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        try:
            response = run_collaboration_task_api(request)
            for event in response.events:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            payload = {
                "type": "task_result",
                "task_id": response.task_id,
                "status": response.status,
                "final_report": response.result.get("final_report"),
                "mermaid": response.result.get("mermaid"),
                "suggestions": response.result.get("suggestions"),
                "suggestion_records": response.result.get("suggestion_records"),
                "risk_level": response.result.get("risk_level"),
                "review_required": response.result.get("review_required"),
                "next_actions": response.result.get("next_actions"),
                "governance": response.result.get("governance"),
                "tool_calls": response.result.get("tool_calls"),
                "agent_outputs": response.result.get("agent_outputs"),
                "human_review_required": response.result.get("human_review_required"),
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'content': str(exc)}, ensure_ascii=False)}\n\n"
        finally:
            yield "data: {\"type\": \"complete\", \"completed\": true}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/tasks", tags=["Task Runtime"])
def list_tasks() -> dict[str, object]:
    return {"tasks": task_store.list_tasks()}


@router.get("/tasks/{task_id}", tags=["Task Runtime"])
def get_task(task_id: str) -> dict[str, object]:
    task = task_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task": task, "artifacts": task_store.get_artifacts(task_id)}


@router.get("/tasks/{task_id}/events", tags=["Task Runtime"])
def get_task_events(task_id: str) -> dict[str, object]:
    if not task_store.get_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"events": task_store.get_events(task_id)}


@router.get("/tasks/{task_id}/report", tags=["Task Runtime"])
def get_task_report(task_id: str) -> dict[str, object]:
    task = task_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task_id": task_id, "final_report": task.get("final_report")}


@router.post("/tasks/{task_id}/ask", response_model=TaskQuestionResponse, tags=["Task Runtime"])
def ask_task(task_id: str, request: TaskQuestionRequest) -> TaskQuestionResponse:
    task = task_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    events = task_store.get_events(task_id)
    report = task.get("final_report") or ""
    sources = rag_store.query(request.collection, request.question, 5)
    answer = _answer_from_task_context(request.question, report, events, sources)
    return TaskQuestionResponse(
        task_id=task_id,
        question=request.question,
        answer=answer["text"],
        answer_source=answer["answer_source"],
        sources=sources,
    )


@router.get("/tasks/{task_id}/events/{event_id}", tags=["Task Runtime"])
def get_task_event_detail(task_id: str, event_id: str) -> dict[str, object]:
    if not task_store.get_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    event = next((item for item in task_store.get_events(task_id) if item.get("event_id") == event_id), None)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"event": event, "detail": _event_detail(event)}


@router.post("/tasks/{task_id}/review-action", response_model=ReviewActionResponse, tags=["Task Runtime"])
def apply_review_action(task_id: str, request: ReviewActionRequest) -> ReviewActionResponse:
    task = task_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    action = request.action
    message = _review_action_message(action, request.payload)
    status = "waiting_review" if action in {"rerun_analysis", "focus_module", "save_knowledge", "learning_task"} else task["status"]
    if action == "save_knowledge":
        content = request.comment or task.get("final_report") or message
        rag_store.add_note("project-memory", f"review/{task_id}", content)
        message = "Review note saved into project-memory knowledge collection."
    task_store.record_review_action(task_id, action, request.comment)
    task_store.update_task(task_id, status)
    task_store.append_event(
        {
            "event_id": f"evt_{uuid4().hex}",
            "task_id": task_id,
            "type": "review_action",
            "node": "human_review",
            "agent": "human_reviewer",
            "status": status,
            "content": message,
            "data": {"action": action, "comment": request.comment, "payload": request.payload},
        }
    )
    return ReviewActionResponse(task_id=task_id, status=status, action=action, message=message)


@router.post("/knowledge/notes", response_model=KnowledgeNoteResponse, tags=["RAG Knowledge Agent"])
def add_knowledge_note(request: KnowledgeNoteRequest) -> KnowledgeNoteResponse:
    saved = rag_store.add_note(request.collection, request.path, request.content)
    return KnowledgeNoteResponse(**saved)


@router.post("/learning/coach/chat", response_model=LearningChatResponse, tags=["Learning Coach Agent"])
def chat_learning_coach(request: LearningChatRequest) -> LearningChatResponse:
    task = task_store.get_task(request.task_id) if request.task_id else None
    stage = _learning_stage_context(request)
    reply = _learning_reply(request, task, stage)
    next_questions = _learning_next_questions(request, task, stage)
    return LearningChatResponse(
        reply=reply["text"],
        next_questions=next_questions["questions"],
        answer_source="llm" if reply["answer_source"] == "llm" or next_questions["answer_source"] == "llm" else "fallback",
        day=stage.get("day"),
        theme=stage.get("theme"),
    )


@router.post("/tasks/{task_id}/approve", response_model=HumanReviewResponse, tags=["Task Runtime"])
def approve_task(task_id: str, request: HumanReviewRequest) -> HumanReviewResponse:
    checkpoint = _latest_resume_checkpoint(task_id)
    if checkpoint:
        return _resume_after_human_review(task_id, checkpoint, "approved", request.comment)
    task = task_store.get_task(task_id)
    if task and task.get("status") == "waiting_review" and _is_visual_workflow_task(task_id):
        raise HTTPException(status_code=409, detail="Workflow is waiting for review but no resume checkpoint was found.")
    return _record_human_review(task_id, "approved", "completed", request.comment)


@router.post("/tasks/{task_id}/reject", response_model=HumanReviewResponse, tags=["Task Runtime"])
def reject_task(task_id: str, request: HumanReviewRequest) -> HumanReviewResponse:
    checkpoint = _latest_resume_checkpoint(task_id)
    if checkpoint and _retry_pre_run_confirmation(task_id, checkpoint, "rejected", request.comment):
        return HumanReviewResponse(task_id=task_id, status="waiting_review", action="rejected", comment=request.comment)
    return _record_human_review(task_id, "rejected", "rejected", request.comment)


@router.post("/tasks/{task_id}/revise", response_model=HumanReviewResponse, tags=["Task Runtime"])
def revise_task(task_id: str, request: HumanReviewRequest) -> HumanReviewResponse:
    checkpoint = _latest_resume_checkpoint(task_id)
    if checkpoint and _retry_pre_run_confirmation(task_id, checkpoint, "revised", request.comment):
        return HumanReviewResponse(task_id=task_id, status="waiting_review", action="revised", comment=request.comment)
    return _record_human_review(task_id, "revised", "waiting_review", request.comment)


def _resolve_task_workflow(request: TaskRunRequest) -> dict[str, object]:
    workflow_name = request.workflow_name or f"{request.execution_mode}_workflow"
    nodes = [node.model_dump() for node in request.nodes]
    edges = [edge.model_dump() for edge in request.edges]
    planned_workflow: dict[str, object] | None = None

    if request.workflow_id:
        workflow = task_store.get_workflow(request.workflow_id)
        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")
        workflow_name = workflow["name"]
        nodes = workflow["nodes"]
        edges = workflow["edges"]

    if request.execution_mode == "planner":
        workflow_name = "planner_generated_workflow"
        nodes, edges = _planned_nodes_for_goal(request.goal, request.require_human_review)
        planned_workflow = {"nodes": nodes, "edges": edges}
    elif not nodes:
        nodes, edges = _default_nodes_for_mode(request.execution_mode)

    return {
        "goal": request.goal,
        "project_path": request.project_path,
        "max_files": request.max_files,
        "require_human_review": request.require_human_review,
        "workflow_id": request.workflow_id,
        "workflow_name": workflow_name,
        "input_text": request.input_text or request.goal,
        "nodes": nodes,
        "edges": edges,
        "planned_workflow": planned_workflow,
    }


def _default_nodes_for_mode(mode: str) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    if mode == "agent":
        nodes: list[dict[str, object]] = [
            {"id": "plan", "type": "planner", "name": "任务规划", "config": {}},
            {"id": "agent", "type": "agent", "name": "项目分析 Agent", "config": {"agent_type": "project_analyzer"}},
            {"id": "report", "type": "reporter", "name": "报告生成", "config": {}},
        ]
    elif mode == "tool":
        nodes = [
            {"id": "plan", "type": "planner", "name": "任务规划", "config": {}},
            {"id": "tool", "type": "mcp_tool", "name": "文件工具", "config": {"tool_name": "filesystem.list"}},
            {"id": "report", "type": "reporter", "name": "报告生成", "config": {}},
        ]
    elif mode == "knowledge":
        nodes = [
            {"id": "plan", "type": "planner", "name": "任务规划", "config": {}},
            {"id": "knowledge", "type": "rag", "name": "知识检索", "config": {"collection": "default", "top_k": 5}},
            {"id": "report", "type": "reporter", "name": "报告生成", "config": {}},
        ]
    else:
        nodes = [
            {"id": "plan", "type": "planner", "name": "任务规划", "config": {}},
            {"id": "analyze", "type": "agent", "name": "项目分析", "config": {"agent_type": "project_analyzer"}},
            {"id": "review", "type": "human_review", "name": "人工审核", "config": {}},
            {"id": "report", "type": "reporter", "name": "报告生成", "config": {}},
        ]
    edges = [
        {"source": str(nodes[index]["id"]), "target": str(nodes[index + 1]["id"])}
        for index in range(len(nodes) - 1)
    ]
    return nodes, edges


def _planned_nodes_for_goal(goal: str, require_review: bool) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    text = goal.lower()
    nodes: list[dict[str, object]] = [
        {"id": "plan", "type": "planner", "name": "Planner", "x": 64, "y": 92, "config": {}},
        {
            "id": "analyze",
            "type": "agent",
            "name": "Project Analyzer",
            "x": 292,
            "y": 92,
            "config": {"agent_type": "project_analyzer"},
        },
    ]

    if any(keyword in text for keyword in ["review", "audit", "risk", "security", "bug", "refactor", "重构", "审查", "风险", "安全"]):
        nodes.append(
            {
                "id": "code_review",
                "type": "agent",
                "name": "Code Review",
                "x": 520,
                "y": 92,
                "config": {"agent_type": "code_reviewer"},
            }
        )

    if any(keyword in text for keyword in ["rag", "knowledge", "doc", "document", "学习", "知识", "文档", "检索"]):
        nodes.append(
            {
                "id": "rag_process",
                "type": "agent",
                "name": "RAG Processor",
                "x": 748,
                "y": 92,
                "config": {"agent_type": "rag_processor"},
            }
        )

    if any(keyword in text for keyword in ["git", "file", "filesystem", "tool", "status", "文件", "工具"]):
        nodes.append(
            {
                "id": "tool",
                "type": "mcp_tool",
                "name": "Filesystem Tool",
                "x": 976,
                "y": 92,
                "config": {"tool_name": "filesystem.list"},
            }
        )

    nodes.append({"id": "supervisor", "type": "supervisor", "name": "Supervisor", "x": 1204, "y": 92, "config": {}})
    if require_review:
        nodes.append(
            {
                "id": "human_review",
                "type": "human_review",
                "name": "Human Review",
                "x": 1432,
                "y": 92,
                "config": {},
            }
        )
    nodes.append({"id": "report", "type": "reporter", "name": "Reporter", "x": 1660, "y": 92, "config": {}})

    compact_nodes = []
    for index, node in enumerate(nodes):
        item = dict(node)
        item["x"] = 64 + index * 228
        compact_nodes.append(item)
    edges = [
        {"source": str(compact_nodes[index]["id"]), "target": str(compact_nodes[index + 1]["id"])}
        for index in range(len(compact_nodes) - 1)
    ]
    return compact_nodes, edges


def _record_human_review(task_id: str, action: str, status: str, comment: str | None) -> HumanReviewResponse:
    task = task_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task_store.record_review_action(task_id, action, comment)
    task_store.update_task(task_id, status)
    task_store.append_event(
        {
            "event_id": f"evt_{uuid4().hex}",
            "task_id": task_id,
            "type": "human_review",
            "node": "human_review",
            "agent": "human_reviewer",
            "status": status,
            "content": _review_content(action, comment),
            "data": {"action": action, "comment": comment},
        }
    )
    return HumanReviewResponse(task_id=task_id, status=status, action=action, comment=comment)


def _latest_resume_checkpoint(task_id: str) -> dict[str, object] | None:
    task = task_store.get_task(task_id)
    if not task or task.get("status") != "waiting_review":
        return None
    for artifact in reversed(task_store.get_artifacts(task_id)):
        content = artifact.get("content")
        if not isinstance(content, dict):
            continue
        checkpoint = content.get("resume_checkpoint")
        if isinstance(checkpoint, dict) and checkpoint.get("paused_node_id"):
            return checkpoint
        if artifact.get("artifact_type") == "workflow_checkpoint" and content.get("paused_node_id"):
            return content
    return None


def _is_visual_workflow_task(task_id: str) -> bool:
    for artifact in reversed(task_store.get_artifacts(task_id)):
        content = artifact.get("content")
        if not isinstance(content, dict):
            continue
        if content.get("workflow_name") or content.get("workflow_events") or content.get("validation"):
            return True
    return False


def _retry_pre_run_confirmation(
    task_id: str,
    checkpoint: dict[str, object],
    action: str,
    comment: str | None,
) -> bool:
    paused_node_id = str(checkpoint.get("paused_node_id") or "")
    paused_node = _checkpoint_node(checkpoint, paused_node_id)
    config = paused_node.get("config") if isinstance(paused_node.get("config"), dict) else {}
    if not config.get("confirm_before_run"):
        return False
    retry_count = int(config.get("retry_count") or 0)
    if retry_count <= 0:
        return False

    state = checkpoint.get("state") if isinstance(checkpoint.get("state"), dict) else {}
    review_retries = dict(state.get("review_retries") or {})
    used = int(review_retries.get(paused_node_id) or 0)
    if used >= retry_count:
        return False

    next_used = used + 1
    review_retries[paused_node_id] = next_used
    next_checkpoint = {
        **checkpoint,
        "state": {
            **state,
            "review_retries": review_retries,
        },
        "retry_attempt": next_used,
    }
    task_store.record_review_action(task_id, action, comment)
    task_store.update_task(task_id, "waiting_review")
    task_store.save_artifact(task_id, "workflow_checkpoint", "resume", next_checkpoint)
    task_store.append_event(
        {
            "event_id": f"evt_{uuid4().hex}",
            "task_id": task_id,
            "type": "human_review",
            "node": paused_node_id,
            "agent": "human_reviewer",
            "status": action,
            "content": _review_content(action, comment),
            "data": {
                "action": action,
                "comment": comment,
                "retry_attempt": next_used,
                "max_retries": retry_count,
            },
        }
    )
    task_store.append_event(
        {
            "event_id": f"evt_{uuid4().hex}",
            "task_id": task_id,
            "type": "workflow_node",
            "node": paused_node_id,
            "agent": str(paused_node.get("type") or "workflow"),
            "status": "retrying",
            "content": f"Pre-run confirmation rejected; retry {next_used}/{retry_count} is waiting for review.",
            "data": {
                "node_id": paused_node_id,
                "node_type": paused_node.get("type"),
                "node_name": paused_node.get("name") or paused_node_id,
                "retry_attempt": next_used,
                "max_retries": retry_count,
            },
        }
    )
    return True


def _checkpoint_node(checkpoint: dict[str, object], node_id: str) -> dict[str, object]:
    for node in checkpoint.get("nodes", []):
        if isinstance(node, dict) and node.get("id") == node_id:
            return node
    return {}


def _resume_after_human_review(
    task_id: str,
    checkpoint: dict[str, object],
    action: str,
    comment: str | None,
) -> HumanReviewResponse:
    task = task_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task_store.record_review_action(task_id, action, comment)
    review_event = {
        "event_id": f"evt_{uuid4().hex}",
        "task_id": task_id,
        "type": "human_review",
        "node": str(checkpoint.get("paused_node_id") or "human_review"),
        "agent": "human_reviewer",
        "status": "approved",
        "content": _review_content(action, comment),
        "data": {"action": action, "comment": comment, "resume": True},
    }
    task_store.append_event(review_event)
    task_store.update_task(task_id, "running")
    task_store.append_event(
        {
            "event_id": f"evt_{uuid4().hex}",
            "task_id": task_id,
            "type": "task",
            "node": "workflow_resume",
            "agent": "harness_runtime",
            "status": "running",
            "content": "Workflow resume started from approved checkpoint.",
            "data": {"paused_node_id": checkpoint.get("paused_node_id")},
        }
    )

    try:
        result = resume_task_workflow(checkpoint, action, comment)
    except Exception as exc:
        task_store.update_task(task_id, "failed")
        task_store.append_event(
            {
                "event_id": f"evt_{uuid4().hex}",
                "task_id": task_id,
                "type": "error",
                "node": "workflow_resume",
                "agent": "harness_runtime",
                "status": "failed",
                "content": str(exc),
                "data": {"paused_node_id": checkpoint.get("paused_node_id")},
            }
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    public_result = result.get("result", {})
    final_report = str(result.get("final_report") or public_result.get("final_report") or "")
    next_status = "waiting_review" if public_result.get("human_review_required") and public_result.get("human_review_packet") else "completed"
    resume_events = [event for event in result.get("events", []) if event.get("task_id")]
    task_store.save_artifact(
        task_id,
        "workflow_resume",
        str(checkpoint.get("paused_node_id") or "resume"),
        {
            "task_id": task_id,
            "resumed_from": checkpoint.get("paused_node_id"),
            "action": action,
            "comment": comment,
            "status": next_status,
            "before_state": checkpoint.get("state", {}),
            "after_events": resume_events,
            "created_at": utc_now_iso(),
        },
    )
    task_store.save_artifact(task_id, "graph_result", "result", public_result)
    if public_result.get("resume_checkpoint"):
        task_store.save_artifact(task_id, "workflow_checkpoint", "resume", public_result["resume_checkpoint"])
    task_store.update_task(task_id, next_status, final_report)
    for event in resume_events:
        task_store.append_event(event)
    task_store.append_event(
        {
            "event_id": f"evt_{uuid4().hex}",
            "task_id": task_id,
            "type": "task",
            "node": "workflow_resume",
            "agent": "harness_runtime",
            "status": next_status,
            "content": "Workflow resume completed." if next_status == "completed" else "Workflow paused again for human review.",
            "data": {"paused_node_id": checkpoint.get("paused_node_id")},
        }
    )
    return HumanReviewResponse(task_id=task_id, status=next_status, action=action, comment=comment)


def _answer_from_task_context(
    question: str,
    report: str,
    events: list[dict[str, object]],
    sources: list[dict[str, object]],
) -> dict[str, str]:
    fallback = _fallback_task_answer(question, report, events, sources)
    facts = {
        "question": question,
        "report_excerpt": report[:6000],
        "events": [
            {
                "type": event.get("type"),
                "node": event.get("node"),
                "agent": event.get("agent"),
                "status": event.get("status"),
                "content": event.get("content"),
            }
            for event in events[-20:]
        ],
        "sources": sources[:5],
    }
    return llm_provider.generate_with_status(
        "你是 DevAgent Studio 的项目追问助手。请只基于任务报告、事件和给定知识来源回答，不要编造未出现的事实。",
        (
            "请用中文 Markdown 回答用户问题，结构要清楚，并在信息不足时说明还需要哪个 Agent 输出。\n"
            f"上下文：{facts}"
        ),
        fallback,
        agent="task_qa",
        prompt_version="task_qa.v1",
    )


def _fallback_task_answer(
    question: str,
    report: str,
    events: list[dict[str, object]],
    sources: list[dict[str, object]],
) -> str:
    q = question.lower()
    lines = ["## 追问回答", ""]
    if any(keyword in q for keyword in ["风险", "risk", "问题", "安全"]):
        lines.append(_extract_section(report, "风险") or _extract_section(report, "Risk") or "当前报告没有明确风险段落。")
    elif any(keyword in q for keyword in ["学习", "先看", "路线", "理解"]):
        lines.append("建议先从项目入口、路由/API、核心服务、数据层、测试或配置文件依次阅读。")
        lines.append("")
        lines.append(_extract_section(report, "建议") or _extract_section(report, "Suggestions") or "")
    elif any(keyword in q for keyword in ["模块", "结构", "架构"]):
        lines.append(_extract_section(report, "项目") or _extract_section(report, "Agent Outputs") or "可以从时间线中的 Project Analyzer 节点查看结构摘要。")
    else:
        first_event = next((event for event in events if event.get("content")), None)
        lines.append("我会基于当前任务报告、执行事件和项目知识库回答。")
        if first_event:
            lines.append(f"- 任务过程线索：{first_event.get('content')}")
        if report:
            lines.append(f"- 报告摘要：{report.replace(chr(10), ' ')[:260]}")
    if sources:
        lines.extend(["", "## 知识库来源"])
        for source in sources[:3]:
            lines.append(f"- `{source.get('path')}` / {source.get('chunk_id')}: {str(source.get('content', ''))[:160]}")
    return "\n".join(line for line in lines if line is not None)


def _extract_section(report: str, keyword: str) -> str:
    if not report:
        return ""
    lines = report.splitlines()
    for index, line in enumerate(lines):
        if keyword.lower() in line.lower():
            section = []
            for item in lines[index : index + 8]:
                if section and item.startswith("## "):
                    break
                section.append(item)
            return "\n".join(section)
    return ""


def _event_detail(event: dict[str, object]) -> dict[str, object]:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    return {
        "node": event.get("node"),
        "agent": event.get("agent"),
        "status": event.get("status"),
        "summary": event.get("content"),
        "node_name": data.get("node_name") if isinstance(data, dict) else None,
        "output": data.get("output") if isinstance(data, dict) else None,
        "hint": "可以基于这个节点输出继续追问、要求重新分析某个模块，或保存为项目知识。",
    }


def _review_action_message(action: str, payload: dict[str, object]) -> str:
    labels = {
        "rerun_analysis": "Requested a deeper re-analysis checkpoint.",
        "focus_module": f"Requested focused analysis for module: {payload.get('module', 'unspecified')}.",
        "save_knowledge": "Requested saving this review into project memory.",
        "learning_task": "Requested generating a follow-up learning task.",
        "lower_risk": "Reviewer lowered risk level after manual judgment.",
    }
    return labels.get(action, f"Recorded review action: {action}.")


def _learning_stage_context(request: LearningChatRequest) -> dict[str, object]:
    if request.day or request.theme:
        return {"day": request.day, "theme": request.theme}
    if not request.task_id:
        return {}
    plans = task_store.list_learning_plans(request.task_id)
    active_plan = next((plan for plan in plans if plan.get("status") == "active"), plans[0] if plans else None)
    if not active_plan:
        return {}
    steps = active_plan.get("plan") or []
    if not steps:
        return {"plan_id": active_plan.get("plan_id"), "topic": active_plan.get("topic")}
    index = min(max(request.turn, 0), len(steps) - 1)
    step = steps[index] if isinstance(steps[index], dict) else {}
    return {
        "plan_id": active_plan.get("plan_id"),
        "topic": active_plan.get("topic"),
        "day": step.get("day"),
        "theme": step.get("theme"),
        "tasks": step.get("tasks", []),
        "output": step.get("output"),
    }


def _learning_reply(request: LearningChatRequest, task: dict[str, object] | None, stage: dict[str, object]) -> dict[str, str]:
    fallback = _fallback_learning_reply(request, task)
    if stage.get("day") or stage.get("theme"):
        fallback = f"Current learning stage: Day {stage.get('day') or '-'} / {stage.get('theme') or 'untitled'}\n\n{fallback}"
    task_context = {"task": task, "learning_stage": stage}
    request = request.model_copy(update={"question": f"{request.question}\nTask context: {task_context}"})
    return llm_provider.generate_with_status(
        "你是 DevAgent Studio 的学习陪练 Agent。请根据用户回答进行启发式追问、纠错和学习路径引导。",
        (
            "请输出中文，语气像教练，不要直接给长篇标准答案。"
            "先反馈用户回答，再给一个下一步挑战。\n"
            f"主题：{request.topic}\n"
            f"水平：{request.level}\n"
            f"轮次：{request.turn}\n"
            f"任务：{task}\n"
            f"问题：{request.question}\n"
            f"用户回答：{request.answer}"
        ),
        fallback,
        agent="learning_coach",
        prompt_version="learning_coach.reply.v1",
    )


def _fallback_learning_reply(request: LearningChatRequest, task: dict[str, object] | None) -> str:
    answer = (request.answer or "").strip()
    topic = request.topic or "当前项目"
    task_goal = str(task.get("goal")) if task else ""
    if not answer:
        base = f"我们先围绕 `{topic}` 做一次项目陪练。"
        if task:
            base += f" 当前任务是：{task_goal}。"
        return base + " 请先说说你对项目入口、核心模块和你最困惑的点，我会根据你的回答继续追问。"

    lower = answer.lower()
    feedback_parts: list[str] = []
    if len(answer) < 30:
        feedback_parts.append("你的回答还比较短，可以继续补充入口文件、核心模块、数据流和不理解的点。")
    if any(word in lower for word in ["api", "route", "fastapi", "接口", "路由"]):
        feedback_parts.append("你已经注意到接口层，下一步可以把 API 请求如何进入 Agent/Workflow 运行链路说清楚。")
    if any(word in lower for word in ["graph", "langgraph", "workflow", "节点", "图"]):
        feedback_parts.append("你抓到了图和工作流，这是理解本项目的关键。建议继续区分固定协作图和可视化画布图。")
    if any(word in lower for word in ["harness", "runtime", "任务", "事件", "历史"]):
        feedback_parts.append("你提到了 Harness/任务运行层，这说明你开始理解项目的治理价值，而不只是代码审查。")
    if any(word in lower for word in ["rag", "知识", "检索", "切片"]):
        feedback_parts.append("你注意到了知识沉淀部分。下一步可以说明 RAG 加工和项目记忆库分别解决什么问题。")
    if any(word in lower for word in ["风险", "审查", "review", "安全", "质量"]):
        feedback_parts.append("你把风险审查纳入理解范围了。建议继续说明风险如何触发人工审核或后续治理动作。")
    if not feedback_parts:
        feedback_parts.append("你的回答给出了方向，但还没有点出项目里的关键机制。建议围绕 API、Graph、Harness、RAG、人工审核选两个展开。")

    challenge = _learning_challenge(answer, task_goal, topic, request.turn)
    return "\n".join(feedback_parts) + f"\n\n下一步挑战：{challenge}"


def _learning_next_questions(request: LearningChatRequest, task: dict[str, object] | None, stage: dict[str, object]) -> dict[str, object]:
    fallback_questions = _fallback_learning_next_questions(request, task)
    task_context = {"task": task, "learning_stage": stage}
    request = request.model_copy(update={"answer": f"{request.answer}\nTask context: {task_context}"})
    result = llm_provider.generate_with_status(
        "你是 DevAgent Studio 的学习陪练 Agent。请基于当前项目任务和用户回答，生成 3 个递进式追问。",
        (
            "只输出 3 行，每行一个问题，不要编号，不要解释。\n"
            f"主题：{request.topic}\n任务：{task}\n用户回答：{request.answer}\n轮次：{request.turn}"
        ),
        "\n".join(fallback_questions),
        agent="learning_coach",
        prompt_version="learning_coach.questions.v1",
    )
    generated_questions = [line.strip("- 0123456789.、").strip() for line in result["text"].splitlines() if line.strip()]
    return {"questions": generated_questions[:3] or fallback_questions, "answer_source": result["answer_source"]}
    questions = [line.strip("- 0123456789.、").strip() for line in text.splitlines() if line.strip()]
    return questions[:3] or fallback_questions


def _fallback_learning_next_questions(request: LearningChatRequest, task: dict[str, object] | None) -> list[str]:
    answer = (request.answer or "").lower()
    topic = request.topic or "当前项目"
    if any(word in answer for word in ["api", "route", "fastapi", "接口", "路由"]):
        if request.turn % 2 == 1:
            return [
                "请指出前端请求体里最关键的 3 个字段，并说明它们如何影响后端执行路径。",
                "如果用户选择 Collab 模式，为什么不应该继续发送画布节点？",
                "你会如何验证一个任务是否真的进入了 HarnessRuntime？",
            ]
        return [
            "从前端点击运行到 FastAPI 路由，中间传了哪些关键字段？",
            "哪个接口负责普通 Workflow，哪个接口负责 Collab？",
            "如果接口失败，事件和任务状态会怎么记录？",
        ]
    if any(word in answer for word in ["graph", "workflow", "langgraph", "节点", "图"]):
        if request.turn % 2 == 1:
            return [
                "请用 state 的角度解释节点之间如何传递信息。",
                "为什么 Workflow 模式和 Planner 模式都能产生图，但来源不同？",
                "如果一个节点失败，时间线应该展示哪些信息才方便用户判断？",
            ]
        return [
            "固定 collaboration_graph 和可视化 Workflow 图有什么区别？",
            "节点之间是直接互相调用，还是通过 state 传递信息？",
            "Planner 模式生成的 Workflow 应该如何被用户确认？",
        ]
    if any(word in answer for word in ["rag", "知识", "检索", "切片"]):
        if request.turn % 2 == 1:
            return [
                "哪些内容适合手动保存为项目知识，而不是自动切片？",
                "如果知识库没有命中，用户下一步应该怎么补充上下文？",
                "项目知识库如何帮助新用户持续理解同一个项目？",
            ]
        return [
            "RAG Processor 和 Knowledge Query 的职责有什么不同？",
            "什么内容适合保存到 project-memory？",
            "回答项目问题时，为什么需要展示知识来源？",
        ]
    if any(word in answer for word in ["harness", "runtime", "事件", "历史", "任务"]):
        if request.turn % 2 == 1:
            return [
                "请区分 graph state、task status 和 SQLite 历史记录。",
                "为什么事件回放比只保存最终报告更适合研发治理？",
                "你会把哪些人工审核动作设计成必须留痕？",
            ]
        return [
            "HarnessRuntime 相比直接 graph.invoke 多解决了什么产品问题？",
            "事件回放对项目治理有什么价值？",
            "人工审核状态为什么应该进入任务生命周期？",
        ]
    seed = (sum(ord(ch) for ch in (request.answer or topic)) + request.turn) % 3
    pools = [
        [f"你能用自己的话解释 {topic} 的核心流程吗？", "这个项目最值得先读的 3 个文件是什么？", "你现在最不确定的是哪个模块？"],
        ["项目分析、代码审查、知识加工分别产出什么？", "如果你是新同学，会从哪个页面开始使用？", "这个平台和普通代码审查工具有什么差别？"],
        ["请说出一个你会保存到项目知识库的结论。", "你会在哪个节点加入人工审核？为什么？", "你希望下一步看结构、风险还是学习路线？"],
    ]
    return pools[seed]


def _learning_challenge(answer: str, task_goal: str, topic: str, turn: int) -> str:
    seed = (sum(ord(ch) for ch in answer) + len(task_goal) + len(topic) + turn) % 5
    challenges = [
        f"用 3 句话说明 `{topic}` 的入口、核心流程和最终产物。",
        "画一条从用户点击运行到最终报告生成的数据流，并标出 HarnessRuntime 的位置。",
        "选一个你提到的模块，说明它的输入、输出和风险点。",
        "把你的理解整理成一条可保存到 project-memory 的知识笔记。",
        "提出一个你还不确定的问题，然后说明你会从哪个 Agent 输出里寻找答案。",
    ]
    return challenges[seed]


def _review_content(action: str, comment: str | None) -> str:
    labels = {
        "approved": "人工审核已通过",
        "rejected": "人工审核已拒绝",
        "revised": "人工审核要求修改",
    }
    suffix = f"：{comment}" if comment else ""
    return f"{labels[action]}{suffix}"


@router.get("/skills", tags=["Skills"])
def list_skills() -> list[dict[str, Any]]:
    return task_store.list_skills()


@router.get("/skills/{code}", response_model=SkillDefinitionResponse, tags=["Skills"])
def get_skill(code: str) -> dict[str, Any]:
    skill = task_store.get_skill(code)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill not found: {code}")
    return skill


@router.post("/skills", response_model=SkillDefinitionResponse, tags=["Skills"])
def create_skill(request: SkillDefinitionRequest) -> dict[str, Any]:
    existing = task_store.get_skill(request.code)
    if existing and existing.get("source_type") == "builtin":
        raise HTTPException(status_code=400, detail="Cannot overwrite builtin skill")
    skill_data = request.model_dump()
    skill_data["source_type"] = "user"
    return task_store.save_skill(skill_data)


@router.delete("/skills/{code}", tags=["Skills"])
def delete_skill(code: str) -> dict[str, str]:
    existing = task_store.get_skill(code)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Skill not found: {code}")
    if existing.get("source_type") == "builtin":
        raise HTTPException(status_code=400, detail="Cannot delete builtin skill")
    task_store.delete_skill(code)
    return {"status": "deleted", "code": code}


@router.patch("/skills/{code}/enabled", tags=["Skills"])
def toggle_skill(code: str, request: SkillToggleRequest) -> dict[str, Any]:
    skill = task_store.update_skill_enabled(code, request.enabled)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill not found: {code}")
    return skill


@router.post("/skills/{code}/execute", tags=["Skills"])
def execute_skill(code: str, request: SkillExecuteRequest) -> dict[str, Any]:
    skill = task_store.get_skill(code)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill not found: {code}")
    if not skill.get("is_enabled"):
        raise HTTPException(status_code=400, detail=f"Skill is disabled: {code}")

    from app.skills.executor import execute_skill_by_type, _safe_int
    input_data = request.input_data or {}
    result = execute_skill_by_type(
        skill_code=code,
        execution=skill.get("execution", {}),
        input_data=input_data,
        goal=input_data.get("goal", ""),
        project_path=input_data.get("project_path"),
        max_files=_safe_int(input_data.get("max_files"), 200),
    )
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@router.post("/skills/import", tags=["Skills"])
def import_skill(skill_json: dict[str, Any]) -> dict[str, Any]:
    required_fields = ["code", "name", "execution"]
    for field in required_fields:
        if field not in skill_json:
            raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
    skill_json["source_type"] = "user"
    return task_store.save_skill(skill_json)


@router.get("/skills/export/all", tags=["Skills"])
def export_all_skills() -> list[dict[str, Any]]:
    return task_store.list_skills(source_type="user")


@router.post("/skills/install", tags=["Skills"])
def install_skill(request: SkillInstallRequest) -> list[dict[str, Any]]:
    from app.skills.installer import SkillInstaller

    try:
        installer = SkillInstaller()
        return installer.install_skill(request.url)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/skills/{code}/uninstall", tags=["Skills"])
def uninstall_skill(code: str) -> dict[str, str]:
    existing = task_store.get_skill(code)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Skill not found: {code}")
    if existing.get("source_type") == "builtin":
        raise HTTPException(status_code=400, detail="Cannot uninstall builtin skill")
    from app.skills.installer import SkillInstaller

    installer = SkillInstaller()
    installer.uninstall_skill(code)
    task_store.delete_skill(code)
    return {"status": "uninstalled", "code": code}
