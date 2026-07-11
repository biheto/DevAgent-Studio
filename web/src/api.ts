import {
  AgentEvent,
  AskResponse,
  BenchmarkCase,
  BenchmarkRun,
  BenchmarkType,
  ExecutionMode,
  LearningChatResponse,
  LearningPlanRecord,
  LlmPromptAbTestResult,
  LlmPromptPayload,
  LlmPromptVersion,
  LlmTrace,
  LlmUsageDashboard,
  McpRegisteredTool,
  McpServerConfig,
  McpStatus,
  McpToolCallLog,
  RagDocument,
  RagResult,
  SkillDefinition,
  TaskDetail,
  TaskSummary,
  WorkflowEdge,
  WorkflowNode,
  WorkflowRecord,
  WorkflowValidation,
} from './types';

const API_BASE = '';

async function apiError(response: Response, fallback: string): Promise<Error> {
  const data = await response.json().catch(() => ({}));
  return new Error(String(data.detail ?? `${fallback}: ${response.status}`));
}

export async function runTaskStream(
  payload: {
    goal: string;
    project_path: string;
    max_files: number;
    require_human_review: boolean;
    execution_mode: ExecutionMode;
    workflow_name: string;
    input_text: string;
    nodes: WorkflowNode[];
    edges: WorkflowEdge[];
  },
  onEvent: (event: AgentEvent | Record<string, unknown>) => void,
) {
  const response = await fetch(`${API_BASE}/api/v1/tasks/run/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...payload,
      nodes: payload.nodes.map(({ id, type, name, config }) => ({ id, type, name, config })),
      edges: payload.edges,
    }),
  });
  if (!response.ok || !response.body) {
    throw new Error(`任务启动失败：${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split('\n\n');
    buffer = chunks.pop() ?? '';
    for (const chunk of chunks) {
      const line = chunk.split('\n').find((item) => item.startsWith('data: '));
      if (!line) continue;
      const raw = line.slice(6).trim();
      if (raw) onEvent(JSON.parse(raw));
    }
  }
}

export async function runCollaborationTaskStream(
  payload: {
    goal: string;
    project_path: string;
    max_files: number;
    require_human_review: boolean;
    execution_mode: ExecutionMode;
    workflow_name: string;
    input_text: string;
    nodes: WorkflowNode[];
    edges: WorkflowEdge[];
  },
  onEvent: (event: AgentEvent | Record<string, unknown>) => void,
) {
  const response = await fetch(`${API_BASE}/api/v1/tasks/collaborate/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...payload,
      execution_mode: 'collaboration',
      nodes: [],
      edges: [],
    }),
  });
  if (!response.ok || !response.body) {
    throw new Error(`Collaboration task failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split('\n\n');
    buffer = chunks.pop() ?? '';
    for (const chunk of chunks) {
      const line = chunk.split('\n').find((item) => item.startsWith('data: '));
      if (!line) continue;
      const raw = line.slice(6).trim();
      if (raw) onEvent(JSON.parse(raw));
    }
  }
}

export async function listTasks(): Promise<TaskSummary[]> {
  const response = await fetch(`${API_BASE}/api/v1/tasks`);
  if (!response.ok) throw new Error('任务列表读取失败');
  const data = await response.json();
  return data.tasks ?? [];
}

export async function getTaskDetail(taskId: string): Promise<TaskDetail> {
  const response = await fetch(`${API_BASE}/api/v1/tasks/${taskId}`);
  if (!response.ok) throw new Error(`Task detail failed: ${response.status}`);
  return response.json();
}

export async function getTaskReport(taskId: string): Promise<string> {
  const response = await fetch(`${API_BASE}/api/v1/tasks/${taskId}/report`);
  if (!response.ok) throw new Error('报告读取失败');
  const data = await response.json();
  return data.final_report ?? '';
}

export async function getTaskEvents(taskId: string): Promise<AgentEvent[]> {
  const response = await fetch(`${API_BASE}/api/v1/tasks/${taskId}/events`);
  if (!response.ok) throw new Error('事件读取失败');
  const data = await response.json();
  return data.events ?? [];
}

export async function listLlmTraces(limit = 50, agent = ''): Promise<LlmTrace[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (agent) params.set('agent', agent);
  const response = await fetch(`${API_BASE}/api/v1/llm/traces?${params.toString()}`);
  if (!response.ok) throw new Error(`LLM traces failed: ${response.status}`);
  const data = await response.json();
  return data.traces ?? [];
}

export async function listLlmPrompts(agent = ''): Promise<LlmPromptVersion[]> {
  const suffix = agent ? `?agent=${encodeURIComponent(agent)}` : '';
  const response = await fetch(`${API_BASE}/api/v1/llm/prompts${suffix}`);
  if (!response.ok) throw new Error(`LLM prompts failed: ${response.status}`);
  const data = await response.json();
  return data.prompts ?? [];
}

export async function saveLlmPrompt(payload: LlmPromptPayload): Promise<LlmPromptVersion> {
  const response = await fetch(`${API_BASE}/api/v1/llm/prompts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`Save prompt failed: ${response.status}`);
  const data = await response.json();
  return data.prompt;
}

export async function setActiveLlmPrompt(agent: string, promptVersion: string): Promise<LlmPromptVersion> {
  const response = await fetch(`${API_BASE}/api/v1/llm/prompts/active`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agent, prompt_version: promptVersion }),
  });
  if (!response.ok) throw new Error(`Set active prompt failed: ${response.status}`);
  const data = await response.json();
  return data.prompt;
}

export async function runLlmPromptAbTest(payload: {
  agent: string;
  prompt_a: string;
  prompt_b: string;
  system_prompt: string;
  user_prompt: string;
  fallback: string;
}): Promise<LlmPromptAbTestResult> {
  const response = await fetch(`${API_BASE}/api/v1/llm/prompts/ab-test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`Prompt A/B test failed: ${response.status}`);
  return response.json();
}

export async function getLlmUsage(limit = 500, agent = ''): Promise<LlmUsageDashboard> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (agent) params.set('agent', agent);
  const response = await fetch(`${API_BASE}/api/v1/llm/usage?${params.toString()}`);
  if (!response.ok) throw new Error(`LLM usage failed: ${response.status}`);
  return response.json();
}

export async function reviewTask(taskId: string, action: 'approve' | 'reject' | 'revise', comment: string) {
  const response = await fetch(`${API_BASE}/api/v1/tasks/${taskId}/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ comment }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(String(data.detail ?? `Review action failed: ${response.status}`));
  }
  if (!response.ok) throw new Error('审核动作提交失败');
  return response.json();
}

export async function applyReviewAction(taskId: string, action: string, comment: string, payload: Record<string, unknown> = {}) {
  const response = await fetch(`${API_BASE}/api/v1/tasks/${taskId}/review-action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, comment, payload }),
  });
  if (!response.ok) throw new Error(`Review action failed: ${response.status}`);
  return response.json();
}

export async function askTask(taskId: string, question: string, collection = 'default'): Promise<AskResponse> {
  const response = await fetch(`${API_BASE}/api/v1/tasks/${taskId}/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, collection }),
  });
  if (!response.ok) throw new Error(`Question failed: ${response.status}`);
  return response.json();
}

export async function queryKnowledge(collection: string, question: string, limit = 5): Promise<RagResult[]> {
  const response = await fetch(`${API_BASE}/api/v1/rag/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ collection, question, limit }),
  });
  if (!response.ok) throw new Error(`Knowledge query failed: ${response.status}`);
  const data = await response.json();
  return data.results ?? [];
}

export async function listKnowledgeDocuments(collection = ''): Promise<RagDocument[]> {
  const suffix = collection ? `?collection=${encodeURIComponent(collection)}` : '';
  const response = await fetch(`${API_BASE}/api/v1/rag/documents${suffix}`);
  if (!response.ok) throw new Error(`Knowledge documents failed: ${response.status}`);
  const data = await response.json();
  return data.documents ?? [];
}

export async function addKnowledgeNote(collection: string, path: string, content: string) {
  const response = await fetch(`${API_BASE}/api/v1/knowledge/notes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ collection, path, content }),
  });
  if (!response.ok) throw new Error(`Knowledge note failed: ${response.status}`);
  return response.json();
}

export async function listProjectFiles(rootPath: string, maxFiles = 800): Promise<{ root: string; files: string[] }> {
  const response = await fetch(`${API_BASE}/api/v1/mcp/filesystem/list`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ root_path: rootPath, max_files: maxFiles }),
  });
  if (!response.ok) throw new Error(`File list failed: ${response.status}`);
  const data = await response.json();
  return { root: data.root ?? rootPath, files: data.files ?? [] };
}

export async function getMcpStatus(): Promise<McpStatus> {
  const response = await fetch(`${API_BASE}/api/v1/mcp/status`);
  if (!response.ok) throw await apiError(response, 'MCP status failed');
  return response.json();
}

export async function listMcpServers(): Promise<McpServerConfig[]> {
  const response = await fetch(`${API_BASE}/api/v1/mcp/servers`);
  if (!response.ok) throw await apiError(response, 'MCP servers failed');
  const data = await response.json();
  return data.servers ?? [];
}

export async function saveMcpServer(payload: {
  server_id: string;
  name: string;
  transport: string;
  command?: string;
  args: string[];
  env: Record<string, string>;
  url?: string;
  enabled: boolean;
}): Promise<McpServerConfig> {
  const response = await fetch(`${API_BASE}/api/v1/mcp/servers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await apiError(response, 'Save MCP server failed');
  const data = await response.json();
  return data.server;
}

export async function setMcpServerEnabled(serverId: string, enabled: boolean): Promise<McpServerConfig> {
  const response = await fetch(`${API_BASE}/api/v1/mcp/servers/${encodeURIComponent(serverId)}/enabled`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  if (!response.ok) throw await apiError(response, 'Toggle MCP server failed');
  const data = await response.json();
  return data.server;
}

export async function discoverMcpServer(serverId: string): Promise<{ server_id: string; status: string; tools: McpRegisteredTool[]; latency_ms: number }> {
  const response = await fetch(`${API_BASE}/api/v1/mcp/servers/${encodeURIComponent(serverId)}/discover`, { method: 'POST' });
  if (!response.ok) throw await apiError(response, 'Discover MCP tools failed');
  return response.json();
}

export async function listMcpRegisteredTools(serverId = '', agentCode = 'workflow_runner'): Promise<McpRegisteredTool[]> {
  const params = new URLSearchParams();
  if (serverId) params.set('server_id', serverId);
  if (agentCode) params.set('agent_code', agentCode);
  const suffix = params.toString() ? `?${params.toString()}` : '';
  const response = await fetch(`${API_BASE}/api/v1/mcp/registered-tools${suffix}`);
  if (!response.ok) throw await apiError(response, 'MCP tools failed');
  const data = await response.json();
  return data.tools ?? [];
}

export async function setMcpRegisteredToolEnabled(serverId: string, toolName: string, enabled: boolean): Promise<McpRegisteredTool> {
  const response = await fetch(`${API_BASE}/api/v1/mcp/registered-tools/${encodeURIComponent(serverId)}/${encodeURIComponent(toolName)}/enabled`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  if (!response.ok) throw await apiError(response, 'Toggle MCP tool failed');
  const data = await response.json();
  return data.tool;
}

export async function setMcpToolApproval(payload: {
  agent_code: string;
  server_id: string;
  tool_name: string;
  allowed: boolean;
  reason?: string;
}) {
  const response = await fetch(`${API_BASE}/api/v1/mcp/tools/approval`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await apiError(response, 'MCP approval failed');
  return response.json();
}

export async function callMcpTool(payload: {
  server_id?: string;
  tool_name: string;
  agent_code: string;
  arguments: Record<string, unknown>;
}) {
  const response = await fetch(`${API_BASE}/api/v1/mcp/tools/call`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await apiError(response, 'MCP call failed');
  return response.json();
}

export async function listMcpToolCallLogs(limit = 100, serverId = ''): Promise<McpToolCallLog[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (serverId) params.set('server_id', serverId);
  const response = await fetch(`${API_BASE}/api/v1/mcp/tool-call-logs?${params.toString()}`);
  if (!response.ok) throw await apiError(response, 'MCP logs failed');
  const data = await response.json();
  return data.logs ?? [];
}

export async function runMcpBenchmark(payload: {
  name: string;
  agent_code: string;
  iterations: number;
  cases: BenchmarkCase[];
}): Promise<BenchmarkRun> {
  const response = await fetch(`${API_BASE}/api/v1/benchmarks/mcp/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await apiError(response, 'MCP benchmark failed');
  const data = await response.json();
  return data.run;
}

export async function runBenchmark(
  benchmarkType: BenchmarkType,
  payload: {
    name: string;
    agent_code: string;
    iterations: number;
    cases: BenchmarkCase[];
  },
): Promise<BenchmarkRun> {
  const response = await fetch(`${API_BASE}/api/v1/benchmarks/${benchmarkType}/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await apiError(response, `${benchmarkType} benchmark failed`);
  const data = await response.json();
  return data.run;
}

export async function listBenchmarks(limit = 50, benchmarkType = ''): Promise<BenchmarkRun[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (benchmarkType) params.set('benchmark_type', benchmarkType);
  const response = await fetch(`${API_BASE}/api/v1/benchmarks?${params.toString()}`);
  if (!response.ok) throw await apiError(response, 'Benchmark list failed');
  const data = await response.json();
  return data.runs ?? [];
}

export async function getBenchmark(runId: string): Promise<BenchmarkRun> {
  const response = await fetch(`${API_BASE}/api/v1/benchmarks/${encodeURIComponent(runId)}`);
  if (!response.ok) throw await apiError(response, 'Benchmark detail failed');
  const data = await response.json();
  return data.run;
}

export async function chatLearningCoach(payload: {
  topic: string;
  level: string;
  question: string;
  answer?: string;
  task_id?: string;
  turn?: number;
}): Promise<LearningChatResponse> {
  const response = await fetch(`${API_BASE}/api/v1/learning/coach/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`Learning chat failed: ${response.status}`);
  return response.json();
}

export async function createTaskLearningPlan(
  taskId: string,
  payload: { topic: string; level: string; days: number; goal?: string; comment?: string },
): Promise<LearningPlanRecord> {
  const response = await fetch(`${API_BASE}/api/v1/tasks/${taskId}/learning-plan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`Learning plan failed: ${response.status}`);
  const data = await response.json();
  return data.plan;
}

export async function listLearningPlans(taskId?: string): Promise<LearningPlanRecord[]> {
  const suffix = taskId ? `?task_id=${encodeURIComponent(taskId)}` : '';
  const response = await fetch(`${API_BASE}/api/v1/learning/plans${suffix}`);
  if (!response.ok) throw new Error(`Learning plans failed: ${response.status}`);
  const data = await response.json();
  return data.plans ?? [];
}

export async function updateLearningPlanStatus(
  planId: string,
  status: LearningPlanRecord['status'],
): Promise<LearningPlanRecord> {
  const response = await fetch(`${API_BASE}/api/v1/learning/plans/${planId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  });
  if (!response.ok) throw new Error(`Learning plan status failed: ${response.status}`);
  const data = await response.json();
  return data.plan;
}

export async function listWorkflows(): Promise<WorkflowRecord[]> {
  const response = await fetch(`${API_BASE}/api/v1/workflows`);
  if (!response.ok) throw new Error('工作流列表读取失败');
  const data = await response.json();
  return data.workflows ?? [];
}

export async function saveWorkflow(payload: {
  workflow_id?: string;
  name: string;
  description?: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}): Promise<WorkflowRecord> {
  const response = await fetch(`${API_BASE}/api/v1/workflows`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error('工作流保存失败');
  const data = await response.json();
  return data.workflow;
}

export async function updateWorkflow(
  workflowId: string,
  payload: {
    name: string;
    description?: string;
    nodes: WorkflowNode[];
    edges: WorkflowEdge[];
  },
): Promise<WorkflowRecord> {
  const response = await fetch(`${API_BASE}/api/v1/workflows/${workflowId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error('工作流更新失败');
  const data = await response.json();
  return data.workflow;
}

export async function validateWorkflow(payload: {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}): Promise<WorkflowValidation> {
  const response = await fetch(`${API_BASE}/api/v1/workflows/validate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      nodes: payload.nodes.map(({ id, type, name, config }) => ({ id, type, name, config })),
      edges: payload.edges,
    }),
  });
  if (!response.ok) throw new Error(`Workflow validation failed: ${response.status}`);
  return response.json();
}

export async function listSkills(): Promise<SkillDefinition[]> {
  const response = await fetch(`${API_BASE}/api/v1/skills`);
  if (!response.ok) throw new Error('Failed to list skills');
  return response.json();
}

export async function getSkill(code: string): Promise<SkillDefinition> {
  const response = await fetch(`${API_BASE}/api/v1/skills/${code}`);
  if (!response.ok) throw new Error(`Skill not found: ${code}`);
  return response.json();
}

export async function createSkill(payload: {
  code: string;
  name: string;
  description?: string;
  version?: string;
  author?: string;
  parameters?: SkillDefinition['parameters'];
  execution?: SkillDefinition['execution'];
}): Promise<SkillDefinition> {
  const response = await fetch(`${API_BASE}/api/v1/skills`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error('Failed to create skill');
  return response.json();
}

export async function deleteSkill(code: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/v1/skills/${code}`, { method: 'DELETE' });
  if (!response.ok) throw new Error(`Failed to delete skill: ${code}`);
}

export async function toggleSkill(code: string, enabled: boolean): Promise<SkillDefinition> {
  const response = await fetch(`${API_BASE}/api/v1/skills/${code}/enabled`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  if (!response.ok) throw new Error(`Failed to toggle skill: ${code}`);
  return response.json();
}

export async function executeSkill(code: string, input_data: Record<string, unknown>): Promise<unknown> {
  const response = await fetch(`${API_BASE}/api/v1/skills/${code}/execute`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ input_data }),
  });
  if (!response.ok) throw new Error(`Failed to execute skill: ${code}`);
  return response.json();
}

export async function importSkill(skillJson: Record<string, unknown>): Promise<SkillDefinition> {
  const response = await fetch(`${API_BASE}/api/v1/skills/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(skillJson),
  });
  if (!response.ok) throw new Error('Failed to import skill');
  return response.json();
}

export async function exportSkills(): Promise<SkillDefinition[]> {
  const response = await fetch(`${API_BASE}/api/v1/skills/export/all`);
  if (!response.ok) throw new Error('Failed to export skills');
  return response.json();
}

export async function installSkill(url: string): Promise<SkillDefinition[]> {
  const response = await fetch(`${API_BASE}/api/v1/skills/install`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || 'Failed to install skill');
  }
  return response.json();
}

export async function uninstallSkill(code: string): Promise<{ status: string; code: string }> {
  const response = await fetch(`${API_BASE}/api/v1/skills/${code}/uninstall`, { method: 'DELETE' });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || 'Failed to uninstall skill');
  }
  return response.json();
}
