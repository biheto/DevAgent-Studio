import {
  AgentEvent,
  AgentOutput,
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
  NodeStatus,
  RagDocument,
  RagGoldCase,
  RagResult,
  ResumeSnapshot,
  SkillApproval,
  SkillExecutionLog,
  SkillPlugin,
  SkillRecord,
  SkillTestResult,
  SkillVersionSnapshot,
  SuggestionRecord,
  TaskResultPayload,
  TaskSummary,
  ToolCall,
  WorkflowEdge,
  WorkflowNode,
  WorkflowRecord,
  WorkflowValidation,
} from '../types';

export function extractMentorView(report: string) {
  const mentor = extractMarkdownSection(report, 'LLM 架构理解') || extractMarkdownSection(report, '架构');
  if (mentor) return mentor;
  return [
    '# 架构导师视角',
    '',
    '当前报告里还没有单独的架构导师段落。',
    '',
    '- 运行 Project Analyzer 或 Collab 模式后，如果 LLM 可用，会在这里提取 `LLM 架构理解`。',
    '- 如果暂时走 fallback，也可以先从最终报告里的 Agent Outputs 和 Detailed Agent Reports 理解结构。',
  ].join('\n');
}

export function extractMarkdownSection(report: string, title: string) {
  if (!report) return '';
  const lines = report.split('\n');
  const start = lines.findIndex((line) => line.toLowerCase().includes(title.toLowerCase()));
  if (start < 0) return '';
  const section = [lines[start]];
  for (const line of lines.slice(start + 1)) {
    if (line.startsWith('## ') && section.length > 1) break;
    section.push(line);
  }
  return section.join('\n').trim();
}

export function deriveModules(files: string[]) {
  const modules = new Set<string>();
  for (const file of files) {
    const parts = file.split('/').filter(Boolean);
    if (parts.length > 1) modules.add(parts[0]);
  }
  return [...modules].sort((a, b) => a.localeCompare(b));
}

export function joinProjectPath(root: string, child: string) {
  const normalizedRoot = root.replace(/[\\/]+$/, '');
  return `${normalizedRoot}/${child}`;
}

export function buildFocusedFileWorkflow(filePath: string): WorkflowNode[] {
  return [
    { id: 'plan_focus', type: 'planner', name: 'Focus Planner', x: 64, y: 92, config: {} },
    {
      id: 'review_focus_file',
      type: 'agent',
      name: 'File Review Agent',
      x: 292,
      y: 92,
      config: { agent_type: 'file_reviewer', file_path: filePath, max_chars: 20000 },
    },
    { id: 'report_focus_file', type: 'reporter', name: 'Focus Reporter', x: 520, y: 92, config: {} },
  ];
}

export function normalizeNodes(nodes: WorkflowNode[]): WorkflowNode[] {
  return (nodes ?? []).map((node, index) => ({
    ...node,
    x: Number.isFinite(node.x) ? node.x : 64 + index * 228,
    y: Number.isFinite(node.y) ? node.y : 92,
    config: node.config ?? {},
  }));
}

export function edgeKeyFor(edge: WorkflowEdge) {
  return `${edge.source}->${edge.target}:${edge.condition ?? 'always'}:${edge.value ?? ''}:${edge.source_path ?? ''}`;
}

export function buildLocalMermaid(nodes: WorkflowNode[], edges: WorkflowEdge[]) {
  const lines = ['flowchart LR', ...nodes.map((node) => `  ${node.id}[${node.name}]`)];
  for (const edge of edges) {
    const label = edge.condition && edge.condition !== 'always' ? `|${edge.condition}${edge.value ? `: ${edge.value}` : ''}|` : '';
    lines.push(`  ${edge.source} -->${label} ${edge.target}`);
  }
  return lines.join('\n');
}

export function firstLine(value?: string) {
  return (value ?? '').split('\n')[0].slice(0, 140);
}

export function deriveToolCalls(events: AgentEvent[]): ToolCall[] {
  return events
    .filter((event) => event.status === 'completed' && event.data?.node_type === 'mcp_tool')
    .map((event) => ({
      node_id: String(event.data?.node_id ?? event.node ?? ''),
      tool_name: String(event.data?.node_name ?? event.agent ?? 'tool'),
      status: event.status,
      result: event.data?.output,
    }));
}

export function deriveAgentOutputs(events: AgentEvent[]): AgentOutput[] {
  return events
    .filter((event) => event.status === 'completed' && event.data?.node_type !== 'mcp_tool')
    .filter((event) => Boolean(event.content))
    .map((event) => ({
      node_id: String(event.data?.node_id ?? event.node ?? ''),
      node_name: String(event.data?.node_name ?? event.node ?? event.type ?? 'node'),
      agent: String(event.agent ?? event.data?.node_type ?? 'agent'),
      content: event.content,
    }));
}

export function formatToolCall(item: ToolCall) {
  const detail = summarizeValue(item.result);
  return `${item.tool_name ?? 'tool'}: ${item.status ?? 'done'}${detail ? ` - ${detail}` : ''}`;
}

export function formatAgentOutput(item: AgentOutput) {
  return `${item.node_name ?? item.agent ?? 'Agent'}: ${firstLine(item.content)}`;
}

export function extractTaskResultArtifact(artifacts: Array<{ artifact_type: string; name: string; content?: unknown }>): Partial<TaskResultPayload> {
  const graphResult = [...artifacts]
    .reverse()
    .find((artifact) => artifact.artifact_type === 'graph_result' && artifact.name === 'result');
  if (graphResult?.content && typeof graphResult.content === 'object') {
    return graphResult.content as Partial<TaskResultPayload>;
  }
  const governance = [...artifacts].reverse().find((artifact) => artifact.artifact_type === 'governance');
  if (governance?.content && typeof governance.content === 'object') {
    const content = governance.content as Partial<TaskResultPayload>;
    return {
      governance: content.governance,
      risk_level: content.risk_level ?? content.governance?.risk_level,
      review_required: content.review_required ?? content.governance?.review_required,
      next_actions: content.next_actions ?? content.governance?.next_actions,
      suggestion_records: content.suggestion_records,
      suggestions: content.suggestions,
    };
  }
  return {};
}

export function extractResumeSnapshots(artifacts: Array<{ artifact_type: string; name: string; content?: unknown }>): ResumeSnapshot[] {
  return artifacts
    .filter((artifact) => artifact.artifact_type === 'workflow_resume' && artifact.content && typeof artifact.content === 'object')
    .map((artifact) => artifact.content as ResumeSnapshot);
}

export function deriveResumeSnapshots(events: AgentEvent[]): ResumeSnapshot[] {
  const records: ResumeSnapshot[] = [];
  for (let index = 0; index < events.length; index += 1) {
    const event = events[index];
    if (event.node !== 'workflow_resume' || event.status !== 'running') continue;
    const endIndex = events.findIndex((item, offset) => offset > index && item.node === 'workflow_resume' && item.status !== 'running');
    const afterEvents = events.slice(index + 1, endIndex > -1 ? endIndex : undefined);
    records.push({
      task_id: event.task_id,
      resumed_from: String(event.data?.paused_node_id ?? ''),
      action: 'approved',
      status: endIndex > -1 ? events[endIndex].status : 'running',
      before_state: {},
      after_events: afterEvents,
      created_at: event.timestamp,
    });
  }
  return records;
}

export function summarizeResumeState(state?: Record<string, unknown>): string[] {
  if (!state) return [];
  const outputs = state.outputs && typeof state.outputs === 'object' ? Object.keys(state.outputs as Record<string, unknown>) : [];
  const suggestions = Array.isArray(state.suggestions) ? state.suggestions.length : 0;
  const toolCalls = Array.isArray(state.tool_calls) ? state.tool_calls.length : 0;
  const agentOutputs = Array.isArray(state.agent_outputs) ? state.agent_outputs.length : 0;
  return [
    state.workflow_name ? `workflow: ${String(state.workflow_name)}` : '',
    state.goal ? `goal: ${String(state.goal).slice(0, 90)}` : '',
    state.current ? `current: ${summarizeValue(state.current)}` : '',
    outputs.length ? `outputs: ${outputs.join(', ')}` : 'outputs: empty',
    `tool_calls: ${toolCalls}`,
    `agent_outputs: ${agentOutputs}`,
    `suggestions: ${suggestions}`,
    state.review_retries && typeof state.review_retries === 'object'
      ? `review_retries: ${JSON.stringify(state.review_retries).slice(0, 90)}`
      : '',
  ].filter(Boolean);
}

export function summarizeValue(value: unknown) {
  if (!value || typeof value !== 'object') return value ? String(value).slice(0, 120) : '';
  const data = value as Record<string, unknown>;
  if (Array.isArray(data.files)) return `${data.files.length} files`;
  if (Array.isArray(data.commits)) return `${data.commits.length} commits`;
  if (Array.isArray(data.results)) return `${data.results.length} results`;
  if (typeof data.content === 'string') return firstLine(data.content);
  if (typeof data.root === 'string') return data.root;
  return JSON.stringify(data).slice(0, 120);
}

export function parseMarkdown(text: string): Array<{ type: 'h1' | 'h2' | 'h3' | 'p' | 'list'; text: string; items?: string[] }> {
  const blocks: Array<{ type: 'h1' | 'h2' | 'h3' | 'p' | 'list'; text: string; items?: string[] }> = [];
  let listItems: string[] = [];
  const flushList = () => {
    if (listItems.length) {
      blocks.push({ type: 'list', text: '', items: listItems });
      listItems = [];
    }
  };

  for (const rawLine of text.split('\n')) {
    const line = rawLine.trim();
    if (!line) {
      flushList();
      continue;
    }
    if (line.startsWith('- ')) {
      listItems.push(line.slice(2));
      continue;
    }
    flushList();
    if (line.startsWith('### ')) blocks.push({ type: 'h3', text: line.slice(4) });
    else if (line.startsWith('## ')) blocks.push({ type: 'h2', text: line.slice(3) });
    else if (line.startsWith('# ')) blocks.push({ type: 'h1', text: line.slice(2) });
    else blocks.push({ type: 'p', text: line });
  }
  flushList();
  return blocks;
}

export function parseMermaid(source: string) {
  const labels = new Map<string, string>();
  const edgeList: Array<{ source: string; target: string }> = [];
  for (const line of source.split('\n')) {
    const trimmed = line.trim();
    const nodeMatch = trimmed.match(/^([A-Za-z0-9_-]+)\[(.+)]$/);
    if (nodeMatch) labels.set(nodeMatch[1], nodeMatch[2]);
    const edgeMatch = trimmed.match(/^([A-Za-z0-9_-]+)\s*-->\s*([A-Za-z0-9_-]+)/);
    if (edgeMatch) edgeList.push({ source: edgeMatch[1], target: edgeMatch[2] });
  }
  for (const edge of edgeList) {
    if (!labels.has(edge.source)) labels.set(edge.source, edge.source);
    if (!labels.has(edge.target)) labels.set(edge.target, edge.target);
  }
  const nodes = Array.from(labels.entries()).map(([id, label], index) => ({
    id,
    label,
    x: 48 + index * 190,
    y: 78,
    width: 132,
    height: 52,
  }));
  return {
    nodes,
    edges: edgeList,
    width: Math.max(420, 96 + nodes.length * 190),
    height: 220,
  };
}
