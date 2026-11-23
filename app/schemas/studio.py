from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class CodeReviewRequest(BaseModel):
    project_path: str = Field(..., description="Local project directory to review")
    max_files: int = Field(default=500, ge=1, le=5000)


class CodeReviewResponse(BaseModel):
    project_path: str
    reviewed_files: int
    findings: list[dict[str, Any]]
    risks: list[str]
    suggestions: list[str]
    suggestion_records: list[dict[str, Any]] = Field(default_factory=list)
    score: int
    report_markdown: str


class RagProcessRequest(BaseModel):
    project_path: str = Field(..., description="Directory that contains documents or source files")
    max_files: int = Field(default=300, ge=1, le=3000)


class RagProcessResponse(BaseModel):
    project_path: str
    document_count: int
    chunk_count: int
    keywords: list[str]
    faq: list[dict[str, str]]
    report_markdown: str


class RagIngestRequest(BaseModel):
    project_path: str
    collection: str = "default"
    max_files: int = Field(default=300, ge=1, le=3000)


class RagIngestResponse(BaseModel):
    collection: str
    document_count: int
    chunk_count: int
    changed_document_count: int = 0
    changed_chunk_count: int = 0
    keywords: list[str]


class RagQueryRequest(BaseModel):
    collection: str = "default"
    question: str
    limit: int = Field(default=5, ge=1, le=20)
    actor_id: str = "local-user"


class RagQueryResponse(BaseModel):
    collection: str
    question: str
    results: list[dict[str, Any]]


class RagDocumentAclRequest(BaseModel):
    collection: str
    path: str
    principals: list[str] = Field(default_factory=lambda: ["*"])


class RagGoldCaseRequest(BaseModel):
    case_id: str = ""
    collection: str = "default"
    question: str
    expected_chunk_ids: list[str] = Field(default_factory=list)
    expected_paths: list[str] = Field(default_factory=list)
    expected_keywords: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class LearningCoachRequest(BaseModel):
    topic: str = Field(..., description="Learning topic")
    level: str = Field(default="beginner", description="beginner/intermediate/advanced")
    days: int = Field(default=7, ge=1, le=60)
    goal: Optional[str] = None


class LearningCoachResponse(BaseModel):
    topic: str
    level: str
    days: int
    plan: list[dict[str, Any]]
    quiz: list[dict[str, str]]
    report_markdown: str


class LearningPlanCreateRequest(BaseModel):
    topic: str
    level: str = "beginner"
    days: int = Field(default=7, ge=1, le=60)
    goal: Optional[str] = None
    comment: Optional[str] = None


class LearningPlanRecord(BaseModel):
    plan_id: str
    task_id: str
    topic: str
    level: str
    status: str
    plan: list[dict[str, Any]]
    quiz: list[dict[str, Any]]
    report_markdown: str
    created_at: str
    updated_at: str


class LearningPlanResponse(BaseModel):
    plan: LearningPlanRecord


class LearningPlanStatusRequest(BaseModel):
    status: str


class ToolPermissionRequest(BaseModel):
    agent_code: str
    tool_code: str


class ToolPermissionResponse(BaseModel):
    agent_code: str
    tool_code: str
    allowed: bool
    reason: str


class McpFileListRequest(BaseModel):
    root_path: str
    max_files: int = Field(default=200, ge=1, le=2000)


class McpFileReadRequest(BaseModel):
    root_path: str
    file_path: str
    max_chars: int = Field(default=4000, ge=1, le=50000)


class McpGitRequest(BaseModel):
    repo_path: str
    limit: int = Field(default=10, ge=1, le=100)


class McpServerConfigRequest(BaseModel):
    server_id: str
    name: str
    transport: str = "stdio"
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: Optional[str] = None
    enabled: bool = False


class McpServerConfigResponse(BaseModel):
    server: dict[str, Any]


class McpToolToggleRequest(BaseModel):
    enabled: bool


class McpToolApprovalRequest(BaseModel):
    agent_code: str = "workflow_runner"
    server_id: str
    tool_name: str
    allowed: bool
    reason: Optional[str] = None


class McpToolCallRequest(BaseModel):
    server_id: Optional[str] = None
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    agent_code: str = "workflow_runner"


class BenchmarkCase(BaseModel):
    case_id: str
    server_id: Optional[str] = None
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class BenchmarkRunRequest(BaseModel):
    name: str = "MCP Tool Benchmark"
    agent_code: str = "benchmark_runner"
    iterations: int = Field(default=3, ge=1, le=20)
    cases: list[BenchmarkCase] = Field(default_factory=list)


class BenchmarkRunResponse(BaseModel):
    run: dict[str, Any]


class WorkflowNode(BaseModel):
    id: str
    type: str
    name: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class WorkflowEdge(BaseModel):
    source: str
    target: str
    condition: str = "always"
    value: Optional[str] = None
    source_path: Optional[str] = None


class WorkflowRunRequest(BaseModel):
    workflow_name: str = "custom_workflow"
    input_text: str = ""
    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)


class WorkflowValidateRequest(BaseModel):
    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)


class WorkflowValidateResponse(BaseModel):
    valid: bool
    errors: list[str]
    warnings: list[str]
    node_count: int
    edge_count: int
    parallel_sources: list[str] = Field(default_factory=list)


class WorkflowRunResponse(BaseModel):
    workflow_name: str
    events: list[dict[str, Any]]
    output: str


class WorkflowSaveRequest(BaseModel):
    workflow_id: str = Field(default_factory=lambda: f"wf_{uuid4().hex[:12]}")
    name: str = "Untitled Workflow"
    description: Optional[str] = None
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowRecord(BaseModel):
    workflow_id: str
    name: str
    description: Optional[str] = None
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    created_at: str
    updated_at: str


class WorkflowSaveResponse(BaseModel):
    workflow: WorkflowRecord


class HumanReviewRequest(BaseModel):
    comment: Optional[str] = None


class HumanReviewResponse(BaseModel):
    task_id: str
    status: str
    action: str
    comment: Optional[str] = None


class TaskQuestionRequest(BaseModel):
    question: str
    collection: str = "default"


class TaskQuestionResponse(BaseModel):
    task_id: str
    question: str
    answer: str
    answer_source: str = "fallback"
    sources: list[dict[str, Any]]


class ReviewActionRequest(BaseModel):
    action: str
    comment: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ReviewActionResponse(BaseModel):
    task_id: str
    status: str
    action: str
    message: str


class KnowledgeNoteRequest(BaseModel):
    collection: str = "default"
    path: str = "manual-note"
    content: str


class KnowledgeNoteResponse(BaseModel):
    collection: str
    chunk_id: str
    path: str


class MemoryExtractRequest(BaseModel):
    text: str = Field(min_length=1, max_length=600)
    scope: str = "user"
    scope_id: str = "local-user"
    source_type: str = "conversation"
    source_ref: Optional[str] = None


class MemoryRecordResponse(BaseModel):
    memory_id: str
    scope: str
    scope_id: str
    memory_type: str
    memory_key: str
    content: str
    confidence: float
    status: str
    source_type: str
    source_ref: Optional[str] = None
    extraction_source: str = "rule_fallback"
    quality_score: float = 0
    quality_reasons: str = "[]"
    retention_policy: str = "review_90d"
    expires_at: Optional[str] = None
    conflict_with: Optional[str] = None
    rag_path: Optional[str] = None
    created_at: str
    updated_at: str
    confirmed_at: Optional[str] = None
    duplicate: bool = False


class MemoryConfirmRequest(BaseModel):
    collection: Optional[str] = None


class LearningChatRequest(BaseModel):
    topic: str = "DevAgent Studio"
    level: str = "beginner"
    question: str
    answer: Optional[str] = None
    task_id: Optional[str] = None
    turn: int = 0
    day: Optional[int] = None
    theme: Optional[str] = None


class LearningChatResponse(BaseModel):
    reply: str
    next_questions: list[str]
    answer_source: str = "fallback"
    day: Optional[int] = None
    theme: Optional[str] = None


class CollaborationRequest(BaseModel):
    goal: str
    project_path: Optional[str] = None
    require_human_review: bool = True


class CollaborationResponse(BaseModel):
    task_id: str | None = None
    status: str = "completed"
    execution_plan: dict[str, Any] = Field(default_factory=dict)
    draft_report: str = ""
    quality_gate: dict[str, Any] = Field(default_factory=dict)
    goal: str
    plan: list[str]
    worker_results: list[dict[str, str]]
    supervisor_notes: list[str]
    human_review_required: bool
    human_review_packet: Optional[dict[str, Any]]
    final_report: str


class TaskRunRequest(BaseModel):
    goal: str
    project_path: Optional[str] = None
    max_files: int = Field(default=500, ge=1, le=5000)
    require_human_review: bool = True
    execution_mode: str = Field(default="workflow", description="workflow/agent/tool/knowledge")
    workflow_id: Optional[str] = None
    workflow_name: Optional[str] = None
    input_text: Optional[str] = None
    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)


class TaskRunResponse(BaseModel):
    task_id: str
    status: str
    events: list[dict[str, Any]]
    result: dict[str, Any]


class BusinessScenarioRequest(BaseModel):
    business_scenario: str = Field(description="onboarding/pr_review/governance")
    project_path: Optional[str] = None
    goal: str = ""
    max_files: int = Field(default=500, ge=1, le=5000)
    require_human_review: bool = True
    pr_base: Optional[str] = None
    pr_head: Optional[str] = None
    pr_url: Optional[str] = None
    post_comment: bool = False
