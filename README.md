# DevAgent Studio

[English](#english) | [中文](#中文)

<a id="english"></a>

DevAgent Studio is an open-source multi-agent workbench for **software project understanding and engineering governance**. It helps engineering teams understand unfamiliar systems faster, identify change risks before code is merged, and continuously manage architecture drift and technical debt.

The product is organized around three practical workflows:

- **PR Change Risk Review**: inspect a pull request, trace its likely impact, identify risks and test gaps, and produce an auditable review decision.
- **Project Onboarding**: turn an unfamiliar repository into an architecture tour, module guide, learning plan, and interactive project coach for new team members.
- **Architecture and Technical Debt Governance**: periodically detect coupling, dependency risk, architecture drift, and deviations from project standards before they become expensive problems.

These workflows share the same governed runtime and project knowledge, so review findings, architecture decisions, and team conventions can be reused instead of being lost in separate conversations.

It is deliberately not a code-writing IDE. Its focus is making software delivery work easier to understand, audit, evaluate, and improve.

![DevAgent Studio architecture](docs/assets/architecture.png)

## Why DevAgent Studio

- **Multi-agent workflows, not isolated prompts**: Planner, Project Analyzer, Code Reviewer, RAG Processor, Supervisor, and Reporter are composed through LangGraph.
- **Governed runtime**: Harness Runtime provides task context, event timelines, artifacts, review state, persistence, deterministic policy checks, and resume support.
- **Visual workflows that execute**: a drag-and-drop canvas is compiled into executable LangGraph workflows instead of serving as a static diagram.
- **Observable LLM operations**: prompt versions, model configuration, traces, token and cost data, fallback records, and A/B comparison are available from the UI.
- **Extensible Skills with guardrails**: Skills are versioned, permission-scoped, testable, dependency-aware, and usable from both the console and a workflow.
- **Plugin Marketplace**: install resource packs from built-in catalogs, local paths, URLs, GitHub-style sources, or an external `SKILL.md` file.
- **Safe third-party code execution**: Code Skills can run in a constrained Docker sandbox with no network, read-only mounts, resource limits, and audit logs.
- **Governed RAG that can be evaluated**: project knowledge now supports incremental indexing, document versions, ACL filtering, hybrid retrieval, optional LLM rerank, and editable chunk-level Gold Sets.
- **Switchable Colorful and Clear UI**: the React workbench can switch between a colorful theme with yellow/green/blue/pink environmental light pillars and a clear SVG-refraction liquid-glass theme with pointer ripples, hover lift, and connected mode transitions. The selected theme is persisted locally.

## Business Workflows

DevAgent Studio is designed to turn project intelligence into repeatable engineering actions, not just generate one-off summaries.

| Workflow | Input | Result | Business value |
| --- | --- | --- | --- |
| PR Change Risk Review | Repository, branch, or pull request diff | Impact scope, call-chain risks, findings, test gaps, and review decision | Finds regression risk before merge and gives reviewers evidence they can audit. |
| Project Onboarding | Repository and a developer's learning goal | Architecture map, module guide, learning plan, and follow-up coaching | Reduces the time required for a new engineer to become productive. |
| Architecture and Technical Debt Governance | Repository snapshots, project rules, and historical reports | Drift findings, dependency risks, technical-debt priorities, and governance actions | Makes architecture quality visible and supports continuous improvement. |

The three workflows use a shared project context, governed RAG knowledge, long-term memory, Skills, MCP tools, and human review. A team can start with one workflow and add the others without creating a separate AI system.

See the detailed input/output examples in [Business Scenarios](docs/BUSINESS_SCENARIOS.md).

### Runnable business workflows

The Run page now has three first-class business scenario entries: **Project Onboarding**, **PR Change Risk Review**, and **Architecture Governance**. Each one creates a persisted Harness task, emits timeline events, produces a structured report, and can enter the existing human-review/resume flow. The same scenarios are available through `/api/v1/business-scenarios/run` and its streaming variant. See the runnable payloads under [`examples/business`](examples/business/).

The PR workflow accepts a GitHub PR URL and can post its governance report back to the PR conversation. A signed GitHub Pull Request webhook triggers the same persisted workflow on `opened`, `reopened`, and `synchronize`. Configure `GITHUB_TOKEN` and `GITHUB_WEBHOOK_SECRET` in `.env`; setup details are in [Business Scenarios](docs/BUSINESS_SCENARIOS.md).

## Product Preview / 页面预览

The screenshots below show the Clear theme with per-surface SVG displacement maps, refracted grid detail, reflective rims, layered shadows, and liquid button interactions. Use the header switch to move between the Colorful and Clear themes at any time.

| Run workbench / 运行 | Visual workflow / 编排 |
| --- | --- |
| ![Run workbench](docs/assets/run-preview.png) | ![Visual workflow](docs/assets/workflow-preview.png) |

| Reports / 报告 | Interactive chat / 追问 |
| --- | --- |
| ![Reports](docs/assets/report-preview.png) | ![Interactive chat](docs/assets/chat-preview.png) |

| Task history / 历史 | LLM governance / LLM |
| --- | --- |
| ![Task history](docs/assets/history-preview.png) | ![LLM console](docs/assets/llm-console-preview.png) |

| MCP console / MCP | Skills console / Skills |
| --- | --- |
| ![MCP console](docs/assets/mcp-console-preview.png) | ![Skills console](docs/assets/skill-preview.png) |

| Plugin Marketplace / Market | Benchmark dashboard / Bench |
| --- | --- |
| ![Plugin Marketplace](docs/assets/market-preview.png) | ![Benchmark dashboard](docs/assets/benchmark-dashboard-preview.png) |

## Architecture

```text
User / React workbench
        |
        v
FastAPI APIs ---- Marketplace ---- Skills Console ---- MCP Console
        |
        v
Harness Runtime
  context | policy | events | artifacts | human review | persistence
        |
        +-----------------------+
        |                       |
        v                       v
LangGraph workflows          Skill Runtime
planner / reviewer /         prompt skills / code skills /
RAG / supervisor / reporter  dependency and permission checks
        |                       |
        +-----------+-----------+
                    v
      LLM / MCP / RAG / SQLite / pgvector / Docker sandbox
```

### Typical execution flow

```text
Task request
  -> FastAPI creates a task
  -> Harness Runtime creates context and emits events
  -> LangGraph invokes agents, Skills, LLMs, RAG, or MCP tools
  -> policy and human-review checks gate sensitive operations
  -> traces, artifacts, logs, and state are persisted
  -> Reporter produces a governance report
```

## Core Capabilities

| Area | What it provides |
| --- | --- |
| Project analysis | Structure scanning, technology identification, module summary, risks, and governance suggestions. |
| Code review | Hybrid rule, call-chain, and LLM semantic review with findings and test recommendations. |
| RAG knowledge | Governed project knowledge base with incremental indexing, document versions, document ACL, hybrid BM25/vector retrieval, optional LLM rerank, SQLite default storage, and pgvector extension. |
| Long-term memory | LLM/rule candidate extraction, explicit confirmation, conflict replacement, quality scoring, retention review, and scoped access boundaries. |
| Learning coach | Project-oriented learning plans and interactive follow-up questions. |
| Collaboration | Planner, analyzer, reviewer, RAG, supervisor, and reporter run as a traceable collaboration graph. |
| Workflow | Drag, connect, configure, validate, save, and execute workflow JSON compiled to LangGraph. |
| Human review | Node-level approval/rejection, checkpoint/resume, retry, and recovery visualization. |
| LLM governance | Per-agent model configuration, call trace, prompt versions, token/cost data, fallback display, and A/B tests. |
| MCP management | Server registration, stdio tool discovery, enable/disable, approval, test invocation, and call logs. |
| Benchmark | LLM, RAG, Workflow, MCP, and multi-agent evaluation with success rate, P95 latency, chunk Gold Set Recall@K, Precision@K, MRR, completeness, token, and cost metrics. |

## RAG Governance and Evaluation

The RAG layer is designed as a governed project knowledge base rather than a simple chunk search demo.

| Capability | What changed |
| --- | --- |
| Incremental indexing | Documents are hashed by content. Unchanged files are skipped; changed files create a new version. |
| Versioned documents | Current retrieval only uses the active version, while old versions remain auditable with `valid_to`. |
| Hybrid retrieval | SQLite uses BM25 plus token semantic overlap. pgvector uses vector candidates plus BM25 and RRF fusion. |
| Optional rerank | `DEV_AGENT_RAG_RERANKER=llm` enables an LLM reranker with JSON-only ordering and fallback to RRF. |
| Document ACL | Documents can be restricted by principal, and queries/listing filter by actor identity. |
| Chunk Gold Set | Benchmark cases can store expected chunk IDs and calculate Chunk Hit, Recall@K, Precision@K, and MRR. |
| Gold Set UI | The Benchmark page can create, edit, delete, and load RAG Gold Set cases into benchmark runs. |

### Document Chunking

RAG ingestion uses type-aware chunking with `tiktoken` (`cl100k_base`) token limits and overlap. Markdown preserves heading paths; Python uses AST class/method boundaries; Java and TypeScript/TSX use declaration boundaries; other text falls back to recursive separators. Retrieved child chunks link to parent context, and non-code text uses a local token-hash sentence-boundary heuristic. Chunk metadata is stored in `rag_chunk.metadata_json`; vectors are stored in `rag_chunk.embedding vector(n)` in PostgreSQL/pgvector. Without a working embedding API key, vectors use the local hash fallback.

The chunking Gold Set in `tests/data/rag_chunking_gold.json` covers Markdown, Java, Python, and deployment text. Its current Recall@3 is 1.00. Run the real PostgreSQL integration test with `$env:DEV_AGENT_TEST_PGVECTOR='1'; .\.venv\Scripts\python.exe -m pytest tests\test_rag_postgres_integration.py -q`; the test creates and cleans up a random collection. Latest verification: integration test passed and the full suite passed (14 tests).

This turns RAG from "can retrieve something" into "can be versioned, permissioned, measured, and improved."

## Governed Long-term Memory

Conversation history is not written directly into the knowledge base. Durable memory follows a governed pipeline:

```text
User message
  -> LLM Memory Extractor (or deterministic rule fallback)
  -> candidate memory + sensitive-content filter
  -> quality score + conflict detection + retention policy
  -> explicit user approval
  -> scoped RAG ingestion
  -> retrieval, review, expiry, or deletion audit trail
```

- **LLM-first extraction with fallback**: `memory_extractor` emits structured candidates for durable preferences, project facts, and team policies. Without an LLM, explicit-preference rules keep the feature usable offline.
- **Confirmation before ingestion**: candidates never enter RAG until the user confirms them. Rejection and deletion prevent unwanted persistence.
- **Conflict-aware updates**: confirming a conflicting value marks the prior record as `superseded`, while preserving history.
- **Quality and retention**: each record has a quality score and reasons. Stable project/team rules and language/security preferences persist; general preferences use `review_90d` and become `expired` instead of being silently deleted.
- **Memory decay, not blind accumulation**: stale or low-durability memories leave the retrieval path after their review window. They remain auditable as `expired` until explicitly refreshed or deleted, preventing old preferences and obsolete decisions from continuously biasing agents.
- **Scoped access boundaries**: user, project, and team memories are separated. The local API accepts `X-DevAgent-Actor` and `X-DevAgent-Role`; project writes require `editor/admin`, while team confirmation and deletion require `admin`.

```env
DEV_AGENT_MEMORY_EXTRACTOR=llm
DEV_AGENT_LLM_MODEL_MEMORY_EXTRACTOR=gpt-4o-mini
```

Set `DEV_AGENT_MEMORY_EXTRACTOR=rule` to disable LLM extraction. Its traces appear in the LLM Console under `memory_extractor`.

## Governed Skill Plugin System

A Skill is a reusable capability such as code review, RAG processing, learning coaching, security scanning, or workflow execution. A Skill can be tested in the Skills console or added to a visual workflow.

### Skill governance

| Capability | Purpose |
| --- | --- |
| Contract validation | Validates `input_schema`, `output_schema`, `permissions`, and `execution_type` during package preview and install. |
| Permission levels | Classifies access as `safe`, `project-read`, `llm`, `workflow-write`, `network`, or `filesystem`, and calculates risk. |
| Strict approvals | Approval is scoped by `skill_code + agent_code`; testing and workflow execution are independently approved. |
| Version management | Keeps Skill snapshots for upgrade comparison and rollback. |
| Dependencies | Declares MCP tools, RAG collections, prompt versions, and model requirements before execution. |
| Built-in tests | Allows packages to provide test cases and lets users run them after installation. |
| Trust metadata | Records source URL, author, manifest signature verification, install count, and local validation state. |
| Workflow mapping | Maps outputs from earlier workflow nodes into a Skill node input. |

### Prompt Skill and Code Skill

An external `SKILL.md` is imported as a **Prompt Skill** when there is no `plugin.json`. The system reads its instruction text and uses it as an LLM prompt. It never executes third-party code.

A **Code Skill** contains an executable entry point, for example:

```text
plugin/
  plugin.json
  skills/
    security_scan.py
```

```json
{
  "code": "security.scan",
  "execution_type": "python",
  "entrypoint": "skills/security_scan.py:run",
  "permissions": ["project-read"]
}
```

### Docker sandbox for Code Skills

Code Skills can use a Docker sandbox. The runtime starts a temporary container and removes it when execution ends. The sandbox applies:

- `--network none`: no outbound network access.
- `--read-only`: immutable container root filesystem.
- read-only mount for the Skill package.
- memory, CPU, PID, and execution-time limits.
- dropped Linux capabilities and `no-new-privileges`.
- invocation result and failure logging.

This makes third-party extensions practical without treating them as trusted local code. Docker isolation is a defense layer, not a substitute for reviewing plugin source and permissions.

## Plugin Marketplace

The Marketplace installs and tracks resource packages. Supported package types include `skill_pack`, `rag_pack`, `mcp_pack`, `prompt_pack`, `workflow_pack`, and `benchmark_pack`.

Supported sources:

- Built-in catalog packages.
- Local folders or a local `plugin.json`.
- URL and GitHub-style package sources.
- External `SKILL.md` files, automatically converted to a safe Prompt Skill.

After installation, the UI shows installed resources, source and trust details, available Skills, approval actions, test calls, workflow insertion, and uninstall status.

### Strict approval model

```text
approval key = skill_code + agent_code
```

The two common execution identities are:

| Agent code | Meaning |
| --- | --- |
| `skill_console` | Manual test call from the Skills page. |
| `workflow_runner` | Automatic call from a visual workflow. |

Approving `skill_console` does not approve `workflow_runner`. A Skill must be explicitly approved for the context in which it will run.

## Quick Start

### Requirements

- Python 3.11+ (Python 3.13 is supported by the current project setup)
- Node.js 18+
- Docker Desktop, optional for pgvector and Docker Code Skill sandboxing

### Install

```powershell
git clone https://github.com/biheto/DevAgent-Studio.git
cd DevAgent-Studio

python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[llm,vector,dev]"

cd web
npm install
npm run build
cd ..

copy .env.example .env
```

### Start the application

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8100
```

Open `http://127.0.0.1:8100/` for the workbench and `http://127.0.0.1:8100/docs` for the API documentation.

### Optional services

Run pgvector:

```powershell
docker compose -f docker-compose.pgvector.yml up -d
```

Configure Docker Code Skill sandboxing in `.env`:

```env
DEV_AGENT_SKILL_SANDBOX=docker
DEV_AGENT_SKILL_SANDBOX_IMAGE=python:3.13-slim
DEV_AGENT_SKILL_SANDBOX_MEMORY=256m
DEV_AGENT_SKILL_SANDBOX_CPUS=0.5
DEV_AGENT_SKILL_SANDBOX_PIDS_LIMIT=64
DEV_AGENT_SKILL_SANDBOX_FALLBACK=false
```

`subprocess` is the default sandbox mode for local development. `docker` requires Docker Desktop to be running. The sandbox status can be checked at `GET /api/v1/skills/sandbox/status`.

### LLM configuration

The application works with deterministic fallback responses when no key is configured. Set an API key for real LLM calls:

```env
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=
DEV_AGENT_LLM_MODEL=gpt-4o-mini

# Optional per-agent overrides
DEV_AGENT_LLM_MODEL_PLANNER=gpt-4o-mini
DEV_AGENT_LLM_MODEL_REPORTER=gpt-4o-mini
DEV_AGENT_LLM_MODEL_CODE_REVIEWER=gpt-4o-mini
```

## Development Checks

```powershell
# Backend compilation
.\.venv\Scripts\python.exe -m compileall -q app

# Frontend production build
cd web
npm run build
cd ..

# Governed long-term memory tests
.\.venv\Scripts\python.exe -m unittest tests.test_memory_store -v

# RAG governance tests
.\.venv\Scripts\python.exe -m unittest tests.test_rag_governance -v

# RAG chunking Gold Set and SQLite tests
.\.venv\Scripts\python.exe -m pytest tests\test_rag_chunking.py -q

# Optional real PostgreSQL + pgvector integration test
$env:DEV_AGENT_TEST_PGVECTOR='1'
.\.venv\Scripts\python.exe -m pytest tests\test_rag_postgres_integration.py -q
Remove-Item Env:DEV_AGENT_TEST_PGVECTOR -ErrorAction SilentlyContinue

# Verify the Skill sandbox configuration
.\.venv\Scripts\python.exe -c "from app.skills.sandbox import python_skill_sandbox_status; print(python_skill_sandbox_status())"
```

## Project Structure

```text
DevAgent Studio/
  app/
    agents/              # Project, review, RAG, learning, and report logic
    api/                 # FastAPI route modules
    graphs/              # LangGraph graphs and visual workflow compiler
    harness/             # Context, events, policy, artifacts, review/resume runtime
    marketplace/         # Package preview, installer, trust, and SKILL.md compatibility
    persistence/         # Task, governance, RAG, and trace persistence
    providers/           # LLM, MCP, and RAG provider interfaces
    skills/              # Registry, contracts, versions, dependencies, sandbox runtime
    benchmark_runner.py  # LLM/RAG/Workflow/MCP/collaboration benchmarks
  web/                   # React workbench
  scripts/               # MCP launchers and test servers
  docs/                  # Architecture and implementation notes
  examples/              # API request examples
  docker-compose.pgvector.yml
```

## Documentation

- [Implementation timeline](docs/IMPLEMENTATION_TIMELINE.md)
- [Workflow production notes](docs/PHASE_8_WORKFLOW_PRODUCTION.md)

## Roadmap

- Add a richer UI for Docker sandbox health and test invocation.
- Add signed external plugin publishing examples and contributor tooling.
- Expand API, workflow compiler, runtime-state, MCP contract, LLM fallback, and benchmark integration test coverage.
- Add conditional branch, parallel node, and richer input/output mapping UX for workflows.

## License and Attribution

This project is licensed under the [MIT License](LICENSE).

Copyright (c) 2026 biheto. When redistributing the project, preserve the original copyright notice and license text.

```text
DevAgent Studio by biheto
https://github.com/biheto/DevAgent-Studio
```

---

<a id="中文"></a>

# DevAgent Studio 中文说明

DevAgent Studio 是一个面向**软件项目理解与研发治理**的开源多 Agent 工作台。它帮助研发团队更快看懂陌生系统、在代码合并前发现变更风险，并持续治理架构漂移与技术债。

产品围绕三个实际业务工作流展开：

- **PR 变更风险审查**：分析 Pull Request 的影响范围、调用链风险和测试缺口，辅助审核并生成可审计的决策报告。
- **项目入职与架构学习**：把陌生仓库转化为架构导览、模块说明、学习计划和交互式项目陪练，帮助新人更快进入项目。
- **架构与技术债治理**：定期发现模块耦合、依赖风险、架构漂移和规范偏离，形成可执行的治理建议。

三个场景共享同一套项目上下文和治理能力，因此审核结论、架构决策和团队规范可以持续复用，而不是散落在不同的对话中。

它不是代码编写 IDE，核心目标是让研发过程更容易理解、审计、评估和持续改进。

## 项目亮点

- **多 Agent 协作而非单次 Prompt**：通过 LangGraph 编排 Planner、项目分析、代码审查、RAG、监督和报告节点。
- **Harness Runtime 运行时治理**：统一任务上下文、事件时间线、产物、策略、人工审核、持久化与恢复执行。
- **真正可执行的可视化 Workflow**：前端拖拽画布会编译成 LangGraph 工作流执行，而不只是展示图。
- **LLM 可观测与可治理**：可查看 Prompt 版本、调用 Trace、token/cost、fallback、A/B Test，以及按 Agent 配置模型。
- **安全可治理的 Skill 插件体系**：Skill 有契约校验、权限分级、严格审批、版本快照、依赖检测、测试用例和执行日志。
- **插件市场与外部兼容**：支持内置包、本地路径、URL、GitHub 风格来源，以及外部 `SKILL.md` 自动转换。
- **Docker 代码型 Skill 沙箱**：第三方代码可以在禁网、只读、限时限资源的临时容器中运行。
- **受控长期记忆**：对话先经 LLM/规则提取为候选记忆，再通过质量评分、冲突检测、人工确认、生命周期策略和 scope 权限边界沉淀到 RAG。
- **遗忘衰减而非无限累积**：稳定规则长期保留；普通偏好进入 90 天复核，到期后标记为 `expired` 并退出检索，但保留审计记录，可由用户刷新、更新或删除，避免陈旧偏好和过时决策持续干扰 Agent。
- **RAG 治理与可评测**：知识库支持增量索引、文档版本化、ACL 权限过滤、BM25/向量混合检索、可选 LLM Rerank 和 Chunk Gold Set。
- **缤纷 / 清透双主题 UI**：顶部可在带黄绿蓝粉彩色环境光柱的“缤纷”主题与 SVG 折射水玻璃“清透”主题之间切换；清透模式支持边缘折射、悬浮阴影、点击涟漪和模式按钮液态连贯切换，并在浏览器本地保存用户选择。

## 业务工作流

DevAgent Studio 的目标不是只生成一次性的项目摘要，而是把项目理解转化为可以重复执行、审核和追踪的研发动作。

| 工作流 | 输入 | 输出 | 业务价值 |
| --- | --- | --- | --- |
| PR 变更风险审查 | 仓库、分支或 Pull Request Diff | 影响范围、调用链风险、问题发现、测试缺口和审核结论 | 在合并前发现回归风险，为审核者提供可追溯的证据。 |
| 项目入职与架构学习 | 项目仓库和开发者学习目标 | 架构导览、模块手册、学习计划和追问陪练 | 缩短新人熟悉项目的时间，减少重复讲解成本。 |
| 架构与技术债治理 | 项目快照、团队规范和历史报告 | 架构漂移、依赖风险、技术债优先级和治理动作 | 让架构质量可见，并支持持续改进而不是问题爆发后再处理。 |

三个工作流共用项目上下文、受控 RAG、长期记忆、Skill、MCP 工具和人工审核。团队可以先启用一个场景，再逐步扩展到其他研发治理流程。

详细的输入、处理链路和输出示例见 [业务场景说明](docs/BUSINESS_SCENARIOS.md)。

## 核心能力

| 模块 | 能力 |
| --- | --- |
| 项目分析 | 扫描目录、识别技术栈、归纳模块职责、风险和治理建议。 |
| 代码审查 | 结合规则、调用链与 LLM 语义审查，输出问题和测试建议。 |
| RAG 知识加工 | 受治理的项目知识库，支持增量索引、文档版本化、文档级 ACL、BM25/向量混合检索、可选 LLM Rerank；默认 SQLite，可扩展 pgvector。 |
| 长期记忆 | LLM/规则候选提取、确认入库、冲突替换、质量评分、90 天复核、过期审计与用户/项目/团队隔离。 |
| 学习陪练 | 基于项目上下文生成学习计划和追问。 |
| 多 Agent 协作 | Planner、Analyzer、Reviewer、RAG、Supervisor、Reporter 组成协作图。 |
| Workflow | 拖拽、连线、配置、校验、保存并执行 Workflow JSON。 |
| 人工审核 | 支持节点级通过/拒绝、checkpoint/resume、重试与恢复事件展示。 |
| MCP | 支持 Server 配置、工具发现、启停、审批、测试调用和日志追踪。 |
| Benchmark | 覆盖 LLM、RAG、Workflow、MCP、多 Agent 协作的指标评估，RAG 支持 Chunk Gold Set、Recall@K、Precision@K 和 MRR。 |

## RAG 治理与评测

RAG 不再只是“切片后能检索”，而是按项目知识库治理的方式设计：

| 能力 | 说明 |
| --- | --- |
| 增量索引 | 基于文档内容 hash 判断变化，未变化文件跳过，变化文件生成新版本。 |
| 文档版本化 | 默认只检索当前版本，旧版本保留 `valid_to`，便于追溯。 |
| 混合检索 | SQLite 使用 BM25 + token 语义相似度；pgvector 使用向量候选 + BM25 + RRF 融合排序。 |
| 可选 Rerank | 设置 `DEV_AGENT_RAG_RERANKER=llm` 后启用 LLM Reranker，并在失败时回退到 RRF。 |
| 文档级 ACL | 支持按 principal 限制文档可见性，查询和文档列表都会按 actor 过滤。 |
| Chunk Gold Set | Benchmark 可维护期望 chunk，计算 Chunk Hit、Recall@K、Precision@K 和 MRR。 |
| Gold Set UI | Benchmark 页面支持新增、删除、加载 Gold Set，并直接用于 RAG Benchmark。 |

### 文档切分

RAG 入库采用按类型切分，并用 `tiktoken`（`cl100k_base`）统一限制 token 数和设置 overlap：Markdown 保留标题路径；Python 按 AST 类/方法边界切分；Java、TypeScript/TSX 按声明边界切分；其他文本递归兜底。child 块关联 parent 上下文；非代码文本以本地 token-hash 句间相似度启发式标记语义边界。切片元数据存于 `rag_chunk.metadata_json`，向量存于 PostgreSQL/pgvector 的 `rag_chunk.embedding vector(n)`。未配置可用 embedding API 时使用本地 hash fallback。

切分 Gold Set 位于 `tests/data/rag_chunking_gold.json`，覆盖 Markdown、Java、Python 和部署文本，当前 Recall@3 为 1.00。真实 PostgreSQL 集成测试命令：`$env:DEV_AGENT_TEST_PGVECTOR='1'; .\.venv\Scripts\python.exe -m pytest tests\test_rag_postgres_integration.py -q`。测试使用随机 collection 并在结束时清理。最近验证：集成测试通过，全量测试 14 项通过。

这让知识库从“能查”升级为“可版本化、可授权、可评测、可持续优化”。

## Skill 插件体系

Skill 是可复用能力，例如代码审查、RAG 加工、学习陪练、安全扫描或 Workflow 执行。它可以在 Skills 页面单独测试，也可以加入可视化 Workflow。

已实现的治理能力：

- **契约校验**：安装和预览时校验 `input_schema`、`output_schema`、`permissions`、`execution_type`，避免格式错误的包进入运行时。
- **权限风险分级**：使用 `safe`、`project-read`、`llm`、`workflow-write`、`network`、`filesystem` 标识访问能力和风险级别。
- **严格审批**：审批键为 `skill_code + agent_code`。手动测试和工作流执行分别审批，互不放行。
- **版本与回滚**：安装升级会保留版本快照，可对比和回滚。
- **依赖声明**：Skill 可声明依赖的 MCP 工具、RAG collection、Prompt 版本和 LLM 模型，运行前会检查缺失项。
- **测试用例**：插件包可携带测试用例，安装后可一键运行并记录结果。
- **可信来源**：记录来源 URL、作者、manifest SHA-256 签名校验、安装次数和本地校验状态。
- **Workflow 输入输出映射**：前序节点输出可映射到 Skill 节点输入，让 Skill 参与复杂工作流。

### Prompt Skill 与代码型 Skill

如果外部来源没有 `plugin.json`，但包含 `SKILL.md`，系统会将其识别为 **Prompt Skill**：只读取其中的指令文本并交给 LLM，不执行第三方代码。

**代码型 Skill** 则带有可执行入口，例如 Python 文件。它能力更强，但必须通过权限审批和沙箱限制后执行。

### Docker 沙箱

当 `.env` 中设置 `DEV_AGENT_SKILL_SANDBOX=docker` 后，代码型 Skill 会在临时 Docker 容器中执行，并使用以下限制：

- 禁止网络访问。
- 容器根文件系统只读，Skill 包只读挂载。
- 限制内存、CPU、进程数和执行超时。
- 移除 Linux capabilities，禁止提升权限。
- 执行结束自动删除容器，同时保留调用结果和失败日志。

Docker 沙箱是隔离层，不代表插件天然可信。安装前仍应检查来源、manifest、权限和代码内容。

## 插件市场

Marketplace 支持安装和管理 `skill_pack`、`rag_pack`、`mcp_pack`、`prompt_pack`、`workflow_pack`、`benchmark_pack` 等资源包。

支持的来源：内置资源包、本地目录或 `plugin.json`、URL/GitHub 风格地址，以及外部 `SKILL.md`。安装后可查看已安装资源、来源与信任信息、Skill 列表、权限审批、测试调用、添加到 Workflow 和卸载状态。

### 审批如何隔离

```text
审批键 = skill_code + agent_code
```

| Agent code | 使用场景 |
| --- | --- |
| `skill_console` | 在 Skills 页面手动点击测试调用。 |
| `workflow_runner` | 在可视化 Workflow 中自动执行。 |

因此，批准 `skill_console` 不等于批准 `workflow_runner`。Skill 必须在实际运行身份下单独获得批准。

## 快速启动

环境要求：Python 3.11+（当前项目支持 Python 3.13）、Node.js 18+；如需 pgvector 或 Docker 沙箱，还需要 Docker Desktop。

```powershell
git clone https://github.com/biheto/DevAgent-Studio.git
cd DevAgent-Studio

python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[llm,vector,dev]"

cd web
npm install
npm run build
cd ..

copy .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8100
```

打开 `http://127.0.0.1:8100/` 使用工作台，打开 `http://127.0.0.1:8100/docs` 查看 API 文档。

可选配置：

```env
# LLM
OPENAI_API_KEY=your_api_key
DEV_AGENT_LLM_MODEL=gpt-4o-mini

# 长期记忆：llm 为 LLM 提取，rule 为仅规则提取
DEV_AGENT_MEMORY_EXTRACTOR=llm
DEV_AGENT_LLM_MODEL_MEMORY_EXTRACTOR=gpt-4o-mini

# pgvector RAG
DEV_AGENT_RAG_STORE=pgvector
PGVECTOR_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/dev_agent_studio
DEV_AGENT_RAG_RERANKER=off
# DEV_AGENT_RAG_RERANKER=llm

# Docker 代码型 Skill 沙箱
DEV_AGENT_SKILL_SANDBOX=docker
DEV_AGENT_SKILL_SANDBOX_IMAGE=python:3.13-slim
DEV_AGENT_SKILL_SANDBOX_MEMORY=256m
DEV_AGENT_SKILL_SANDBOX_CPUS=0.5
DEV_AGENT_SKILL_SANDBOX_PIDS_LIMIT=64
DEV_AGENT_SKILL_SANDBOX_FALLBACK=false
```

启动 pgvector：

```powershell
docker compose -f docker-compose.pgvector.yml up -d
```

可通过 `GET /api/v1/skills/sandbox/status` 查看 Skill 沙箱配置状态。

## 开发验证

```powershell
.\.venv\Scripts\python.exe -m compileall -q app

.\.venv\Scripts\python.exe -m unittest tests.test_memory_store tests.test_rag_governance -v

.\.venv\Scripts\python.exe -m pytest tests\test_rag_chunking.py -q

$env:DEV_AGENT_TEST_PGVECTOR='1'
.\.venv\Scripts\python.exe -m pytest tests\test_rag_postgres_integration.py -q
Remove-Item Env:DEV_AGENT_TEST_PGVECTOR -ErrorAction SilentlyContinue

cd web
npm run build
cd ..

.\.venv\Scripts\python.exe -c "from app.skills.sandbox import python_skill_sandbox_status; print(python_skill_sandbox_status())"
```

## 项目结构

```text
DevAgent Studio/
  app/
    agents/              # 项目分析、代码审查、RAG、学习、报告逻辑
    api/                 # FastAPI 路由
    graphs/              # LangGraph 图与 Workflow 编译器
    harness/             # 上下文、事件、策略、产物、审核/恢复运行时
    marketplace/         # 资源包预览、安装、可信信息、SKILL.md 兼容层
    persistence/         # 任务、治理、RAG、Trace 持久化
    providers/           # LLM、MCP、RAG Provider
    skills/              # Registry、契约、版本、依赖、沙箱运行时
  web/                   # React 工作台
  scripts/               # MCP 启动和测试脚本
  docs/                  # 设计和实现说明
```

## 后续计划

- 增加 Docker 沙箱状态和测试调用的前端可视化。
- 增加外部签名插件发布示例和贡献工具。
- 补齐 API、Workflow 编译、Harness 状态、MCP 契约、LLM fallback 和 Benchmark 集成自动化测试。
- 增强 Workflow 条件分支、并行节点和输入输出映射交互。

## License / 引用

项目使用 [MIT License](LICENSE)。分发或修改时请保留原版权和许可证文本。

```text
DevAgent Studio by biheto
https://github.com/biheto/DevAgent-Studio
```
