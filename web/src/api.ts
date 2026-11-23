import { consumeTaskEventStream } from './event-stream';
import {
  AgentEvent,
  AskResponse,
  BenchmarkCase,
  BenchmarkRun,
  BenchmarkType,
  BusinessScenarioCode,
  ExecutionMode,
  LearningChatResponse,
  LearningPlanRecord,
  LlmPromptAbTestResult,
  LlmPromptPayload,
  LlmPromptVersion,
  LlmTrace,
  LlmUsageDashboard,
  MarketplaceCatalogItem,
  MarketplaceInstall,
  MarketplacePreview,
  McpRegisteredTool,
  McpServerConfig,
  McpStatus,
  McpToolCallLog,
  MemoryRecord,
  RagDocument,
  RagGoldCase,
  RagResult,
  SkillApproval,
  SkillExecutionLog,
  SkillPlugin,
  SkillRecord,
  SkillTestResult,
  SkillVersionSnapshot,
  TaskDetail,
  TaskSummary,
  TaskPage,
  WorkflowEdge,
  WorkflowNode,
  WorkflowRecord,
  WorkflowValidation,
} from './types';

const API_BASE = '';

export async function runBusinessScenarioStream(
  payload: {
    business_scenario: BusinessScenarioCode;
    project_path: string;
    goal: string;
    max_files: number;
    require_human_review: boolean;
    pr_base?: string;
    pr_head?: string;
    pr_url?: string;
    post_comment?: boolean;
  },
  onEvent: (event: AgentEvent | Record<string, unknown>) => void,
) {
  const response = await fetch(`${API_BASE}/api/v1/business-scenarios/run/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  await consumeTaskEventStream(response, onEvent);
}

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
  await consumeTaskEventStream(response, onEvent);
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
  await consumeTaskEventStream(response, onEvent);
}

export async function searchTasks(
  options: { q?: string; status?: string; limit?: number; offset?: number } = {},
  signal?: AbortSignal,
): Promise<TaskPage> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(options)) {
    if (value !== undefined && value !== '') params.set(key, String(value));
  }
  const response = await fetch(`${API_BASE}/api/v1/tasks?${params}`, {signal});
  if (!response.ok) throw await apiError(response, '任务列表读取失败');
  return response.json();
}

export async function listTasks(): Promise<TaskSummary[]> {
  return (await searchTasks({limit: 20})).tasks;
}

export async function downloadTaskReport(taskId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/v1/tasks/${encodeURIComponent(taskId)}/report/download`);
  if (!response.ok) throw await apiError(response, '报告下载失败');
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `devagent-${taskId.replace(/[^a-zA-Z0-9_-]/g, '_')}.md`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
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

export async function listRagGoldCases(collection = '', includeDisabled = true): Promise<RagGoldCase[]> {
  const params = new URLSearchParams({ include_disabled: String(includeDisabled) });
  if (collection) params.set('collection', collection);
  const response = await fetch(`${API_BASE}/api/v1/rag/gold-cases?${params.toString()}`);
  if (!response.ok) throw await apiError(response, 'RAG Gold Set list failed');
  const data = await response.json();
  return data.cases ?? [];
}

export async function saveRagGoldCase(payload: Partial<RagGoldCase> & { question: string; collection: string }): Promise<RagGoldCase> {
  const response = await fetch(`${API_BASE}/api/v1/rag/gold-cases`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await apiError(response, 'RAG Gold Set save failed');
  const data = await response.json();
  return data.case;
}

export async function deleteRagGoldCase(caseId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/v1/rag/gold-cases/${encodeURIComponent(caseId)}`, { method: 'DELETE' });
  if (!response.ok) throw await apiError(response, 'RAG Gold Set delete failed');
}

export async function extractMemoryCandidates(payload: {
  text: string;
  scope?: string;
  scope_id?: string;
  source_type?: string;
  source_ref?: string;
}): Promise<MemoryRecord[]> {
  const response = await fetch(`${API_BASE}/api/v1/memories/extract`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await apiError(response, 'Memory extraction failed');
  return response.json();
}

export async function listMemories(status = ''): Promise<MemoryRecord[]> {
  const suffix = status ? `?status=${encodeURIComponent(status)}` : '';
  const response = await fetch(`${API_BASE}/api/v1/memories${suffix}`);
  if (!response.ok) throw await apiError(response, 'Memory list failed');
  return response.json();
}

export async function confirmMemory(memoryId: string): Promise<MemoryRecord> {
  const response = await fetch(`${API_BASE}/api/v1/memories/${memoryId}/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  if (!response.ok) throw await apiError(response, 'Memory confirmation failed');
  return response.json();
}

export async function rejectMemory(memoryId: string): Promise<MemoryRecord> {
  const response = await fetch(`${API_BASE}/api/v1/memories/${memoryId}/reject`, { method: 'POST' });
  if (!response.ok) throw await apiError(response, 'Memory rejection failed');
  return response.json();
}

export async function deleteMemory(memoryId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/v1/memories/${memoryId}`, { method: 'DELETE' });
  if (!response.ok) throw await apiError(response, 'Memory deletion failed');
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

export async function listSkillPlugins(): Promise<SkillPlugin[]> {
  const response = await fetch(`${API_BASE}/api/v1/skills/plugins`);
  if (!response.ok) throw await apiError(response, 'Skill plugins failed');
  const data = await response.json();
  return data.plugins ?? [];
}

export async function listSkills(category = ''): Promise<SkillRecord[]> {
  const suffix = category ? `?category=${encodeURIComponent(category)}` : '';
  const response = await fetch(`${API_BASE}/api/v1/skills${suffix}`);
  if (!response.ok) throw await apiError(response, 'Skills failed');
  const data = await response.json();
  return data.skills ?? [];
}

export async function listSkillApprovals(agentCode = ''): Promise<SkillApproval[]> {
  const suffix = agentCode ? `?agent_code=${encodeURIComponent(agentCode)}` : '';
  const response = await fetch(`${API_BASE}/api/v1/skills/approvals${suffix}`);
  if (!response.ok) throw await apiError(response, 'Skill approvals failed');
  const data = await response.json();
  return data.approvals ?? [];
}

export async function listSkillExecutionLogs(limit = 100, skillCode = ''): Promise<SkillExecutionLog[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (skillCode) params.set('skill_code', skillCode);
  const response = await fetch(`${API_BASE}/api/v1/skills/execution-logs?${params.toString()}`);
  if (!response.ok) throw await apiError(response, 'Skill logs failed');
  const data = await response.json();
  return data.logs ?? [];
}

export async function listSkillVersions(skillCode: string): Promise<SkillVersionSnapshot[]> {
  const response = await fetch(`${API_BASE}/api/v1/skills/${encodeURIComponent(skillCode)}/versions`);
  if (!response.ok) throw await apiError(response, 'Skill versions failed');
  const data = await response.json();
  return data.versions ?? [];
}

export async function rollbackSkillVersion(skillCode: string, version: string): Promise<SkillRecord> {
  const response = await fetch(`${API_BASE}/api/v1/skills/${encodeURIComponent(skillCode)}/rollback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version }),
  });
  if (!response.ok) throw await apiError(response, 'Skill rollback failed');
  const data = await response.json();
  return data.skill;
}

export async function setSkillEnabled(skillCode: string, enabled: boolean): Promise<SkillRecord> {
  const response = await fetch(`${API_BASE}/api/v1/skills/${encodeURIComponent(skillCode)}/enabled`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  if (!response.ok) throw await apiError(response, 'Toggle skill failed');
  const data = await response.json();
  return data.skill;
}

export async function testSkill(payload: {
  skill_code: string;
  agent_code: string;
  test?: Record<string, unknown>;
}): Promise<SkillTestResult> {
  const response = await fetch(`${API_BASE}/api/v1/skills/${encodeURIComponent(payload.skill_code)}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await apiError(response, 'Skill test failed');
  return response.json();
}

export async function uninstallSkillPlugin(pluginId: string): Promise<Record<string, unknown>> {
  const response = await fetch(`${API_BASE}/api/v1/skills/plugins/${encodeURIComponent(pluginId)}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw await apiError(response, 'Uninstall skill plugin failed');
  const data = await response.json();
  return data.uninstall ?? {};
}

export async function setSkillApproval(payload: {
  skill_code: string;
  agent_code: string;
  allowed: boolean;
  reason?: string;
}): Promise<SkillApproval> {
  const response = await fetch(`${API_BASE}/api/v1/skills/${encodeURIComponent(payload.skill_code)}/approval`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await apiError(response, 'Skill approval failed');
  const data = await response.json();
  return data.approval;
}

export async function executeSkill(payload: {
  skill_code: string;
  agent_code: string;
  input: Record<string, unknown>;
  task_id?: string;
}): Promise<{
  log_id: string;
  skill: SkillRecord;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  status: string;
  latency_ms: number;
}> {
  const response = await fetch(`${API_BASE}/api/v1/skills/${encodeURIComponent(payload.skill_code)}/execute`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await apiError(response, 'Skill execution failed');
  return response.json();
}

export async function listMarketplaceCatalog(): Promise<MarketplaceCatalogItem[]> {
  const response = await fetch(`${API_BASE}/api/v1/marketplace/catalog`);
  if (!response.ok) throw await apiError(response, 'Marketplace catalog failed');
  const data = await response.json();
  return data.items ?? [];
}

export async function listMarketplaceInstalls(limit = 80, packageType = ''): Promise<MarketplaceInstall[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (packageType) params.set('package_type', packageType);
  const response = await fetch(`${API_BASE}/api/v1/marketplace/installs?${params.toString()}`);
  if (!response.ok) throw await apiError(response, 'Marketplace installs failed');
  const data = await response.json();
  return data.installs ?? [];
}

export async function previewMarketplacePackage(sourceUrl: string): Promise<MarketplacePreview> {
  const response = await fetch(`${API_BASE}/api/v1/marketplace/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_url: sourceUrl }),
  });
  if (!response.ok) throw await apiError(response, 'Marketplace preview failed');
  return response.json();
}

export async function installMarketplacePackage(sourceUrl: string): Promise<MarketplaceInstall> {
  const response = await fetch(`${API_BASE}/api/v1/marketplace/install`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_url: sourceUrl }),
  });
  if (!response.ok) throw await apiError(response, 'Marketplace install failed');
  const data = await response.json();
  return data.install;
}

export async function uninstallMarketplacePackage(packageId: string): Promise<MarketplaceInstall> {
  const response = await fetch(`${API_BASE}/api/v1/marketplace/packages/${encodeURIComponent(packageId)}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw await apiError(response, 'Marketplace uninstall failed');
  const data = await response.json();
  return data.uninstall;
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
