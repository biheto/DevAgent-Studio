# Phase 7: pgvector RAG Store

本阶段把 RAG 知识库从 SQLite 关键词检索升级为可切换的 pgvector 向量检索。

## 已实现

- 新增 `PgVectorRagStore`
  - 表：`rag_document`
  - 表：`rag_chunk`
  - 原文和切片元数据分别存于 `rag_chunk.content`、`rag_chunk.metadata_json`
  - 向量字段：`rag_chunk.embedding vector(DEV_AGENT_EMBEDDING_DIM)`
  - 检索方式：`embedding <=> query_embedding`
- 类型化文档切分
  - Markdown 按标题和段落组织，保存 `heading_path`
  - Python 按 AST 类/方法边界切分；Java、TypeScript/TSX 按代码声明边界切分
  - 其他支持的文本格式递归按段落、换行、句子等边界兜底
  - 使用 `tiktoken` `cl100k_base` 按 token 数限制块长并添加 overlap
- Parent-child 与语义边界
  - child chunk 用于检索，通过 `parent_chunk_id` 关联较大的 parent 上下文
  - 查询结果同时提供 `content` 和 `context_content`，后者优先返回 parent 内容
  - 非代码文本用本地 token-hash 词项向量的句间相似度标记语义边界；不调用外部模型
- 切片元数据
  - 保存切分策略、标题路径或代码符号/行号、层级及 parent ID
  - SQLite 和 pgvector 均持久化元数据；已有 SQLite / PostgreSQL 表会补充 metadata 列
- 新增 Embedding Provider
  - 优先使用 `OPENAI_API_KEY` + `DEV_AGENT_EMBEDDING_MODEL`
  - 未配置或调用失败时使用 hash fallback embedding，保证本地流程可运行；fallback 可写入向量库，但不具备真实模型 embedding 的语义质量
- 新增 `/api/v1/rag/status`
  - 查看当前 RAG store 类型、embedding 来源、维度等
- 保留 SQLite fallback
  - `.env` 不配置 pgvector 时，原有关键词检索继续可用
- 新增 `docker-compose.pgvector.yml`
  - 用于本地启动 PostgreSQL + pgvector

## 配置

`.env` 示例：

```env
DEV_AGENT_RAG_STORE=pgvector
PGVECTOR_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/dev_agent_studio
DEV_AGENT_EMBEDDING_MODEL=text-embedding-3-small
DEV_AGENT_EMBEDDING_DIM=1536
```

如果希望使用真实语义 embedding，还需要配置：

```env
OPENAI_API_KEY=你的 key
# OPENAI_BASE_URL=兼容 OpenAI 的代理地址，可选
```

## 启动 pgvector

```powershell
docker compose -f docker-compose.pgvector.yml up -d
```

安装依赖：

```powershell
.\.venv\Scripts\pip.exe install -e ".[vector]"
```

启动后查看状态：

```powershell
.\.venv\Scripts\python.exe -c "from fastapi.testclient import TestClient; from app.main import app; print(TestClient(app).get('/api/v1/rag/status').json())"
```

## API 使用

入库：

```http
POST /api/v1/rag/ingest
```

查询：

```http
POST /api/v1/rag/query
```

文档列表：

```http
GET /api/v1/rag/documents?collection=project-memory
```

## 向量存储

切片向量写入 PostgreSQL `dev_agent_studio` 数据库的 `rag_chunk.embedding` 列，列类型为 `vector(DEV_AGENT_EMBEDDING_DIM)`，默认 1536 维。切片正文在 `content`，结构、标题/符号和 parent-child 信息在 `metadata_json`。本地 Compose 使用 `pgvector_data` Docker volume 持久化 PostgreSQL 数据目录 `/var/lib/postgresql/data`。

## 切片评测

固定问题集位于 `tests/data/rag_chunking_gold.json`，覆盖 Markdown 缓存文档、Java 重试策略、Python TokenBucket 和部署说明。它验证目标文档 Recall@3；当前 SQLite 与真实 pgvector/hash-fallback 的测试集 Recall@3 均为 1.00。

```powershell
# 单元与 SQLite 检查
.\.venv\Scripts\python.exe -m pytest tests\test_rag_chunking.py tests\test_rag_governance.py -q

# 真实 PostgreSQL + pgvector 集成测试；使用随机隔离 collection，测试结束清理
$env:DEV_AGENT_TEST_PGVECTOR='1'
.\.venv\Scripts\python.exe -m pytest tests\test_rag_postgres_integration.py -q

# 清除 opt-in 后运行常规全量测试
Remove-Item Env:DEV_AGENT_TEST_PGVECTOR -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pytest -q
```

最近一次验证：pgvector 集成测试 1 passed；开启集成测试后的全量测试 14 passed；Gold Set Recall@3 = 1.00。集成测试使用本地 hash fallback，验证的是 pgvector 写入、向量维度、查询和父上下文回读，不代表验证了 OpenAI embedding 服务。

## 当前边界

- 切换 `DEV_AGENT_RAG_STORE` 后需要重启后端。
- pgvector 数据库需要外部 PostgreSQL 服务。
- 如果没有可用的 `OPENAI_API_KEY`，仍会写入 pgvector，但 embedding 是 hash fallback，不等于真实语义 embedding。
- 当前语义边界是轻量本地词项相似度启发式，不是 transformer/sentence embedding；同义改写识别能力有限。
- Java 和 TypeScript/TSX 声明边界使用轻量源码扫描，不是完整 AST；复杂语法或生成代码可能走长度兜底。
- 后续可以增加 collection 级重建、删除、embedding 版本迁移和索引重建接口。
