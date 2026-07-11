export type ExecutionMode = 'agent' | 'workflow' | 'tool' | 'knowledge' | 'collaboration' | 'planner';

export type AgentEvent = {
  event_id?: string;
  task_id?: string;
  type?: string;
  node?: string | null;
  agent?: string | null;
  status?: string;
  content?: string;
  timestamp?: string;
  data?: Record<string, unknown>;
};

export type TaskSummary = {
  task_id: string;
  goal: string;
  project_path?: string;
  status: string;
  created_at: string;
  updated_at: string;
  final_report?: string;
};

export type TaskArtifact = {
  artifact_type: string;
  name: string;
  content?: unknown;
  created_at: string;
};

export type TaskDetail = {
  task: TaskSummary;
  artifacts: TaskArtifact[];
};

export type NodeStatus = 'idle' | 'running' | 'completed' | 'failed' | 'waiting_review';

export type WorkflowNode = {
  id: string;
  type: string;
  name: string;
  x: number;
  y: number;
  config: Record<string, unknown>;
};

export type WorkflowEdge = {
  source: string;
  target: string;
  condition?: 'always' | 'on_status' | 'contains' | 'truthy_output' | string;
  value?: string | null;
  source_path?: string | null;
};

export type WorkflowValidation = {
  valid: boolean;
  errors: string[];
  warnings: string[];
  node_count: number;
  edge_count: number;
  parallel_sources: string[];
};

export type WorkflowRecord = {
  workflow_id: string;
  name: string;
  description?: string | null;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  created_at: string;
  updated_at: string;
};

export type TaskResultPayload = {
  type: 'task_result';
  task_id: string;
  status: string;
  final_report?: string;
  mermaid?: string;
  suggestions?: string[];
  suggestion_records?: SuggestionRecord[];
  risk_level?: string;
  review_required?: boolean;
  next_actions?: string[];
  governance?: GovernanceSummary;
  tool_calls?: ToolCall[];
  agent_outputs?: AgentOutput[];
  human_review_required?: boolean;
  planned_workflow?: {
    nodes: WorkflowNode[];
    edges: WorkflowEdge[];
  };
  validation?: WorkflowValidation;
};

export type ResumeSnapshot = {
  task_id?: string;
  resumed_from?: string;
  action?: string;
  comment?: string | null;
  status?: string;
  before_state?: Record<string, unknown>;
  after_events?: AgentEvent[];
  created_at?: string;
};

export type SuggestionRecord = {
  id?: string;
  finding?: Record<string, unknown> | null;
  risk_level?: string;
  action?: string;
  test_case?: string;
  review_required?: boolean;
  next_actions?: string[];
};

export type GovernanceSummary = {
  risk_level?: string;
  review_required?: boolean;
  next_actions?: string[];
  suggestion_record_count?: number;
};

export type ToolCall = {
  node_id?: string;
  tool_name?: string;
  status?: string;
  result?: unknown;
};

export type AgentOutput = {
  node_id?: string;
  node_name?: string;
  agent?: string;
  content?: string;
};

export type RagDocument = {
  collection: string;
  path: string;
  size?: number;
  created_at?: string;
};

export type RagResult = {
  chunk_id?: string;
  path?: string;
  score?: number;
  content?: string;
};

export type AskResponse = {
  task_id: string;
  question: string;
  answer: string;
  answer_source?: 'llm' | 'fallback' | string;
  sources: RagResult[];
};

export type LearningChatResponse = {
  reply: string;
  next_questions: string[];
  answer_source?: 'llm' | 'fallback' | string;
  day?: number | null;
  theme?: string | null;
};

export type LearningPlanRecord = {
  plan_id: string;
  task_id: string;
  topic: string;
  level: string;
  status: 'active' | 'completed' | 'paused';
  plan: Array<{
    day?: number;
    theme?: string;
    tasks?: string[];
    output?: string;
  }>;
  quiz: Array<Record<string, string>>;
  report_markdown: string;
  created_at: string;
  updated_at: string;
};

export type LlmTrace = {
  trace_id: string;
  agent: string;
  prompt_version: string;
  model?: string | null;
  input?: Record<string, unknown>;
  output_text?: string;
  fallback_used: boolean;
  error_message?: string | null;
  latency_ms: number;
  token_usage?: Record<string, unknown>;
  created_at: string;
};

export type LlmPromptVersion = {
  agent: string;
  prompt_family: string;
  prompt_version: string;
  title: string;
  description?: string | null;
  system_suffix?: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type LlmPromptPayload = {
  agent: string;
  prompt_family?: string;
  prompt_version: string;
  title: string;
  description?: string;
  system_suffix?: string;
  is_active?: boolean;
};

export type LlmPromptAbResultItem = {
  prompt_version: string;
  text: string;
  answer_source: string;
  fallback_used: boolean;
  model?: string | null;
  trace_id?: string;
  latency_ms: number;
  token_usage?: Record<string, unknown>;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  quality_score: number;
  error_message?: string | null;
};

export type LlmPromptAbTestResult = {
  agent: string;
  prompt_a: LlmPromptAbResultItem;
  prompt_b: LlmPromptAbResultItem;
  comparison: {
    winner: 'A' | 'B' | 'tie' | string;
    criteria: string[];
  };
};

export type LlmUsageBucket = {
  name: string;
  calls: number;
  fallback_calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  latency_ms: number;
  avg_latency_ms: number;
  fallback_rate: number;
  estimated_cost_usd: number;
};

export type LlmUsageDashboard = {
  total: LlmUsageBucket;
  by_agent: LlmUsageBucket[];
  by_model: LlmUsageBucket[];
  by_prompt: LlmUsageBucket[];
  pricing: Record<string, { input_per_1m: number; output_per_1m: number }>;
  sample_size: number;
  currency: string;
  cost_basis: string;
};

export type McpStatus = {
  provider: string;
  server_count: number;
  enabled_servers?: number;
  tool_count: number;
};

export type McpServerConfig = {
  server_id: string;
  name: string;
  transport: string;
  command?: string | null;
  args: string[];
  env: Record<string, string>;
  url?: string | null;
  enabled: boolean;
  status: string;
  last_error?: string | null;
  created_at: string;
  updated_at: string;
};

export type McpRegisteredTool = {
  tool_id: string;
  server_id: string;
  name: string;
  description?: string | null;
  input_schema: Record<string, unknown>;
  enabled: boolean;
  status: string;
  discovered_at: string;
  updated_at: string;
  approval_agent_code?: string;
  approval_allowed?: boolean;
  approval_reason?: string | null;
  approval_updated_at?: string | null;
  approval_recorded?: boolean;
};

export type McpToolCallLog = {
  call_id: string;
  server_id?: string | null;
  tool_name: string;
  agent_code?: string | null;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  status: string;
  error_message?: string | null;
  latency_ms: number;
  created_at: string;
};

export type BenchmarkType = 'mcp' | 'llm' | 'rag' | 'workflow' | 'collaboration';

export type BenchmarkCase = {
  case_id: string;
  server_id?: string | null;
  tool_name?: string;
  arguments: Record<string, unknown>;
  enabled?: boolean;
};

export type BenchmarkResult = {
  id?: number;
  run_id: string;
  case_id: string;
  server_id?: string | null;
  tool_name?: string | null;
  iteration: number;
  status: string;
  latency_ms: number;
  error_message?: string | null;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  created_at: string;
};

export type BenchmarkSummaryCase = {
  case_id: string;
  server_id?: string | null;
  tool_name?: string | null;
  total: number;
  completed: number;
  failed: number;
  success_rate: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  min_latency_ms: number;
  max_latency_ms: number;
};

export type BenchmarkSummary = {
  total?: number;
  completed?: number;
  failed?: number;
  success_rate?: number;
  avg_latency_ms?: number;
  p95_latency_ms?: number;
  min_latency_ms?: number;
  max_latency_ms?: number;
  iterations?: number;
  case_count?: number;
  by_case?: BenchmarkSummaryCase[];
  benchmark_focus?: string;
  avg_quality_score?: number;
  fallback_calls?: number;
  fallback_rate?: number;
  total_tokens?: number;
  estimated_cost_usd?: number;
  hit_count?: number;
  hit_rate?: number;
  avg_source_quality?: number;
  avg_result_count?: number;
  workflow_success_count?: number;
  workflow_success_rate?: number;
  failed_node_count?: number;
  failed_nodes?: string[];
  avg_completed_nodes?: number;
  avg_completeness_score?: number;
  avg_risk_detection_score?: number;
  human_review_trigger_count?: number;
  human_review_trigger_rate?: number;
};

export type BenchmarkRun = {
  run_id: string;
  name: string;
  benchmark_type: string;
  status: string;
  config: {
    agent_code?: string;
    iterations?: number;
    case_count?: number;
    cases?: BenchmarkCase[];
  };
  summary: BenchmarkSummary;
  results?: BenchmarkResult[];
  started_at: string;
  finished_at?: string | null;
  created_at: string;
};

export type SkillParameter = {
  name: string;
  type: string;
  required: boolean;
  default?: string;
  description?: string;
  options?: string[];
};

export type SkillExecution = {
  type: string;
  nodes: Array<{ id: string; type: string; name: string; config?: Record<string, unknown> }>;
  edges: Array<{ source: string; target: string }>;
};

export type SkillDefinition = {
  code: string;
  name: string;
  description?: string;
  version?: string;
  author?: string;
  source_type: string;
  source_url?: string;
  skill_path?: string;
  parameters: SkillParameter[];
  execution: SkillExecution;
  is_enabled: boolean;
  created_at: string;
  updated_at: string;
};
