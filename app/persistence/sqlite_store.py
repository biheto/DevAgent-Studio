from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.harness.events import utc_now_iso


class SQLiteTaskStore:
    def __init__(self, db_path: str | Path = "data/dev_agent_studio.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_task (
                    task_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    project_path TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    final_report TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_task_event (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    node TEXT,
                    agent TEXT,
                    status TEXT,
                    content TEXT,
                    data_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_task_artifact (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    content_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_definition (
                    workflow_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    nodes_json TEXT NOT NULL,
                    edges_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS human_review_action (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    comment TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_plan (
                    plan_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    level TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    quiz_json TEXT NOT NULL,
                    report_markdown TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_call_trace (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    model TEXT,
                    input_json TEXT,
                    output_text TEXT,
                    fallback_used INTEGER NOT NULL,
                    error_message TEXT,
                    latency_ms INTEGER NOT NULL,
                    token_usage_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_prompt_version (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent TEXT NOT NULL,
                    prompt_family TEXT NOT NULL DEFAULT '',
                    prompt_version TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    system_suffix TEXT,
                    is_active INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(agent, prompt_version)
                )
                """
            )
            self._ensure_column(conn, "llm_prompt_version", "prompt_family", "TEXT NOT NULL DEFAULT ''")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_server_config (
                    server_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    transport TEXT NOT NULL,
                    command TEXT,
                    args_json TEXT,
                    env_json TEXT,
                    url TEXT,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'unknown',
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_tool_registry (
                    tool_id TEXT PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    input_schema_json TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'available',
                    discovered_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(server_id, name)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_tool_approval (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_code TEXT NOT NULL,
                    server_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    allowed INTEGER NOT NULL DEFAULT 0,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(agent_code, server_id, tool_name)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_tool_call_log (
                    call_id TEXT PRIMARY KEY,
                    server_id TEXT,
                    tool_name TEXT NOT NULL,
                    agent_code TEXT,
                    input_json TEXT,
                    output_json TEXT,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    latency_ms INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS benchmark_run (
                    run_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    benchmark_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS benchmark_result (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    server_id TEXT,
                    tool_name TEXT,
                    iteration INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    error_message TEXT,
                    input_json TEXT,
                    output_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS skill_definition (
                    code TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    version TEXT,
                    author TEXT,
                    source_type TEXT NOT NULL DEFAULT 'user',
                    parameters_json TEXT,
                    execution_json TEXT NOT NULL,
                    is_enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            for col_sql in [
                "ALTER TABLE skill_definition ADD COLUMN source_url TEXT DEFAULT ''",
                "ALTER TABLE skill_definition ADD COLUMN skill_path TEXT DEFAULT ''",
            ]:
                try:
                    conn.execute(col_sql)
                except sqlite3.OperationalError:
                    pass

    def create_task(self, task_id: str, goal: str, project_path: str | None, status: str) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO agent_task(task_id, goal, project_path, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (task_id, goal, project_path, status, now, now),
            )

    def update_task(self, task_id: str, status: str, final_report: str | None = None) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE agent_task
                SET status = ?, final_report = COALESCE(?, final_report), updated_at = ?
                WHERE task_id = ?
                """,
                (status, final_report, now, task_id),
            )

    def append_event(self, event: dict[str, Any]) -> None:
        event_id = event.get("event_id") or f"evt_{event['task_id']}_{event.get('node') or 'event'}_{utc_now_iso()}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_task_event(
                    event_id, task_id, event_type, node, agent, status, content, data_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    event["task_id"],
                    event.get("type", "event"),
                    event.get("node"),
                    event.get("agent"),
                    event.get("status"),
                    event.get("content"),
                    json.dumps(event.get("data", {}), ensure_ascii=False),
                    event.get("timestamp") or utc_now_iso(),
                ),
            )

    def save_artifact(self, task_id: str, artifact_type: str, name: str, content: Any) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_task_artifact(task_id, artifact_type, name, content_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (task_id, artifact_type, name, json.dumps(content, ensure_ascii=False), utc_now_iso()),
            )

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT task_id, goal, project_path, status, created_at, updated_at, final_report
                FROM agent_task
                ORDER BY created_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT task_id, goal, project_path, status, created_at, updated_at, final_report
                FROM agent_task
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_events(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, task_id, event_type AS type, node, agent, status, content, data_json, created_at AS timestamp
                FROM agent_task_event
                WHERE task_id = ?
                ORDER BY id ASC
                """,
                (task_id,),
            ).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            item["data"] = json.loads(item.pop("data_json") or "{}")
            events.append(item)
        return events

    def get_artifacts(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT artifact_type, name, content_json, created_at
                FROM agent_task_artifact
                WHERE task_id = ?
                ORDER BY id ASC
                """,
                (task_id,),
            ).fetchall()
        artifacts = []
        for row in rows:
            item = dict(row)
            item["content"] = json.loads(item.pop("content_json") or "null")
            artifacts.append(item)
        return artifacts

    def save_workflow(
        self,
        workflow_id: str,
        name: str,
        description: str | None,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM workflow_definition WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT OR REPLACE INTO workflow_definition(
                    workflow_id, name, description, nodes_json, edges_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workflow_id,
                    name,
                    description,
                    json.dumps(nodes, ensure_ascii=False),
                    json.dumps(edges, ensure_ascii=False),
                    created_at,
                    now,
                ),
            )
        return self.get_workflow(workflow_id) or {}

    def list_workflows(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT workflow_id, name, description, nodes_json, edges_json, created_at, updated_at
                FROM workflow_definition
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [self._workflow_row_to_dict(row) for row in rows]

    def get_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT workflow_id, name, description, nodes_json, edges_json, created_at, updated_at
                FROM workflow_definition
                WHERE workflow_id = ?
                """,
                (workflow_id,),
            ).fetchone()
        return self._workflow_row_to_dict(row) if row else None

    def record_review_action(self, task_id: str, action: str, comment: str | None = None) -> dict[str, Any]:
        created_at = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO human_review_action(task_id, action, comment, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (task_id, action, comment, created_at),
            )
        return {"task_id": task_id, "action": action, "comment": comment, "created_at": created_at}

    def save_learning_plan(
        self,
        plan_id: str,
        task_id: str,
        topic: str,
        level: str,
        plan: list[dict[str, Any]],
        quiz: list[dict[str, Any]],
        report_markdown: str,
        status: str = "active",
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO learning_plan(
                    plan_id, task_id, topic, level, status, plan_json, quiz_json,
                    report_markdown, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    task_id,
                    topic,
                    level,
                    status,
                    json.dumps(plan, ensure_ascii=False),
                    json.dumps(quiz, ensure_ascii=False),
                    report_markdown,
                    now,
                    now,
                ),
            )
        return self.get_learning_plan(plan_id) or {}

    def list_learning_plans(self, task_id: str | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT plan_id, task_id, topic, level, status, plan_json, quiz_json,
                   report_markdown, created_at, updated_at
            FROM learning_plan
        """
        params: tuple[Any, ...] = ()
        if task_id:
            query += " WHERE task_id = ?"
            params = (task_id,)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._learning_plan_row_to_dict(row) for row in rows]

    def get_learning_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT plan_id, task_id, topic, level, status, plan_json, quiz_json,
                       report_markdown, created_at, updated_at
                FROM learning_plan
                WHERE plan_id = ?
                """,
                (plan_id,),
            ).fetchone()
        return self._learning_plan_row_to_dict(row) if row else None

    def update_learning_plan_status(self, plan_id: str, status: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE learning_plan
                SET status = ?, updated_at = ?
                WHERE plan_id = ?
                """,
                (status, utc_now_iso(), plan_id),
            )
        return self.get_learning_plan(plan_id)

    def save_llm_trace(self, trace: dict[str, Any]) -> dict[str, Any]:
        record = {
            "trace_id": trace.get("trace_id"),
            "agent": trace.get("agent") or "unknown",
            "prompt_version": trace.get("prompt_version") or "v1",
            "model": trace.get("model"),
            "input": trace.get("input"),
            "output": trace.get("output"),
            "fallback_used": bool(trace.get("fallback_used")),
            "error_message": trace.get("error_message"),
            "latency_ms": int(trace.get("latency_ms") or 0),
            "token_usage": trace.get("token_usage") or {},
            "created_at": trace.get("created_at") or utc_now_iso(),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO llm_call_trace(
                    trace_id, agent, prompt_version, model, input_json, output_text,
                    fallback_used, error_message, latency_ms, token_usage_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["trace_id"],
                    record["agent"],
                    record["prompt_version"],
                    record["model"],
                    json.dumps(record["input"], ensure_ascii=False),
                    record["output"],
                    1 if record["fallback_used"] else 0,
                    record["error_message"],
                    record["latency_ms"],
                    json.dumps(record["token_usage"], ensure_ascii=False),
                    record["created_at"],
                ),
            )
        return record

    def list_llm_traces(self, limit: int = 50, agent: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        query = """
            SELECT trace_id, agent, prompt_version, model, input_json, output_text,
                   fallback_used, error_message, latency_ms, token_usage_json, created_at
            FROM llm_call_trace
        """
        params: list[Any] = []
        if agent:
            query += " WHERE agent = ?"
            params.append(agent)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        traces: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["input"] = json.loads(item.pop("input_json") or "{}")
            item["token_usage"] = json.loads(item.pop("token_usage_json") or "{}")
            item["fallback_used"] = bool(item["fallback_used"])
            traces.append(item)
        return traces

    def upsert_prompt_version(self, prompt: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        agent = str(prompt.get("agent") or "unknown")
        prompt_version = str(prompt.get("prompt_version") or "v1")
        prompt_family = str(prompt.get("prompt_family") or self._prompt_family(prompt_version))
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT created_at FROM llm_prompt_version
                WHERE agent = ? AND prompt_version = ?
                """,
                (agent, prompt_version),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT OR REPLACE INTO llm_prompt_version(
                    agent, prompt_family, prompt_version, title, description, system_suffix,
                    is_active, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent,
                    prompt_family,
                    prompt_version,
                    str(prompt.get("title") or prompt_version),
                    prompt.get("description"),
                    prompt.get("system_suffix"),
                    1 if prompt.get("is_active") else 0,
                    created_at,
                    now,
                ),
            )
        return self.get_prompt_version(agent, prompt_version) or {}

    def set_active_prompt_version(self, agent: str, prompt_version: str) -> dict[str, Any] | None:
        now = utc_now_iso()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT agent, prompt_family, prompt_version FROM llm_prompt_version
                WHERE agent = ? AND prompt_version = ?
                """,
                (agent, prompt_version),
            ).fetchone()
            if not row:
                return None
            prompt_family = row["prompt_family"] or self._prompt_family(prompt_version)
            conn.execute(
                """
                UPDATE llm_prompt_version
                SET is_active = 0, updated_at = ?
                WHERE agent = ? AND prompt_family = ?
                """,
                (now, agent, prompt_family),
            )
            conn.execute(
                """
                UPDATE llm_prompt_version
                SET is_active = 1, updated_at = ?
                WHERE agent = ? AND prompt_version = ?
                """,
                (now, agent, prompt_version),
            )
        return self.get_prompt_version(agent, prompt_version)

    def get_prompt_version(self, agent: str, prompt_version: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT agent, prompt_version, title, description, system_suffix,
                       prompt_family, is_active, created_at, updated_at
                FROM llm_prompt_version
                WHERE agent = ? AND prompt_version = ?
                """,
                (agent, prompt_version),
            ).fetchone()
        return self._prompt_row_to_dict(row) if row else None

    def list_prompt_versions(self, agent: str | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT agent, prompt_version, title, description, system_suffix,
                   prompt_family, is_active, created_at, updated_at
            FROM llm_prompt_version
        """
        params: list[Any] = []
        if agent:
            query += " WHERE agent = ?"
            params.append(agent)
        query += " ORDER BY agent ASC, prompt_version ASC"
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._prompt_row_to_dict(row) for row in rows]

    def get_active_prompt_version(self, agent: str, prompt_family: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT agent, prompt_version, title, description, system_suffix,
                       prompt_family, is_active, created_at, updated_at
                FROM llm_prompt_version
                WHERE agent = ? AND prompt_family = ? AND is_active = 1
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (agent, prompt_family),
            ).fetchone()
        return self._prompt_row_to_dict(row) if row else None

    def llm_usage_summary(self, limit: int = 500, agent: str | None = None) -> dict[str, Any]:
        traces = self.list_llm_traces(limit=limit, agent=agent)
        by_agent: dict[str, dict[str, Any]] = {}
        by_model: dict[str, dict[str, Any]] = {}
        by_prompt: dict[str, dict[str, Any]] = {}
        total = self._usage_bucket("all")
        for trace in traces:
            usage = trace.get("token_usage") if isinstance(trace.get("token_usage"), dict) else {}
            input_tokens, output_tokens, total_tokens = self._token_counts(usage)
            fallback_used = bool(trace.get("fallback_used"))
            latency_ms = int(trace.get("latency_ms") or 0)
            agent_name = str(trace.get("agent") or "unknown")
            model = str(trace.get("model") or "fallback")
            prompt_version = str(trace.get("prompt_version") or "v1")
            prompt_key = f"{agent_name}:{prompt_version}"
            self._add_usage(total, input_tokens, output_tokens, total_tokens, latency_ms, fallback_used)
            self._add_usage(by_agent.setdefault(agent_name, self._usage_bucket(agent_name)), input_tokens, output_tokens, total_tokens, latency_ms, fallback_used)
            self._add_usage(by_model.setdefault(model, self._usage_bucket(model)), input_tokens, output_tokens, total_tokens, latency_ms, fallback_used)
            self._add_usage(by_prompt.setdefault(prompt_key, self._usage_bucket(prompt_key)), input_tokens, output_tokens, total_tokens, latency_ms, fallback_used)
        return {
            "total": self._finalize_usage(total),
            "by_agent": [self._finalize_usage(item) for item in by_agent.values()],
            "by_model": [self._finalize_usage(item) for item in by_model.values()],
            "by_prompt": [self._finalize_usage(item) for item in by_prompt.values()],
            "sample_size": len(traces),
        }

    def _workflow_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["nodes"] = json.loads(item.pop("nodes_json") or "[]")
        item["edges"] = json.loads(item.pop("edges_json") or "[]")
        return item

    def _learning_plan_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["plan"] = json.loads(item.pop("plan_json") or "[]")
        item["quiz"] = json.loads(item.pop("quiz_json") or "[]")
        return item

    def _prompt_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["is_active"] = bool(item["is_active"])
        return item

    def _prompt_family(self, prompt_version: str) -> str:
        parts = prompt_version.split(".")
        if len(parts) > 1 and parts[-1].startswith("v") and parts[-1][1:].isdigit():
            return ".".join(parts[:-1])
        return prompt_version

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def save_mcp_server(self, server: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        server_id = str(server.get("server_id") or server.get("name") or "").strip()
        if not server_id:
            raise ValueError("server_id is required")
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM mcp_server_config WHERE server_id = ?",
                (server_id,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT OR REPLACE INTO mcp_server_config(
                    server_id, name, transport, command, args_json, env_json, url,
                    enabled, status, last_error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    server_id,
                    str(server.get("name") or server_id),
                    str(server.get("transport") or "stdio"),
                    server.get("command"),
                    json.dumps(server.get("args") or [], ensure_ascii=False),
                    json.dumps(server.get("env") or {}, ensure_ascii=False),
                    server.get("url"),
                    1 if server.get("enabled") else 0,
                    str(server.get("status") or "unknown"),
                    server.get("last_error"),
                    created_at,
                    now,
                ),
            )
        return self.get_mcp_server(server_id) or {}

    def list_mcp_servers(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT server_id, name, transport, command, args_json, env_json, url,
                       enabled, status, last_error, created_at, updated_at
                FROM mcp_server_config
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [self._mcp_server_row_to_dict(row) for row in rows]

    def get_mcp_server(self, server_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT server_id, name, transport, command, args_json, env_json, url,
                       enabled, status, last_error, created_at, updated_at
                FROM mcp_server_config
                WHERE server_id = ?
                """,
                (server_id,),
            ).fetchone()
        return self._mcp_server_row_to_dict(row) if row else None

    def update_mcp_server_status(self, server_id: str, status: str, last_error: str | None = None, enabled: bool | None = None) -> dict[str, Any] | None:
        now = utc_now_iso()
        with self._connect() as conn:
            if enabled is None:
                conn.execute(
                    "UPDATE mcp_server_config SET status = ?, last_error = ?, updated_at = ? WHERE server_id = ?",
                    (status, last_error, now, server_id),
                )
            else:
                conn.execute(
                    "UPDATE mcp_server_config SET status = ?, last_error = ?, enabled = ?, updated_at = ? WHERE server_id = ?",
                    (status, last_error, 1 if enabled else 0, now, server_id),
                )
        return self.get_mcp_server(server_id)

    def upsert_mcp_tool(self, tool: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        server_id = str(tool.get("server_id") or "").strip()
        name = str(tool.get("name") or "").strip()
        if not server_id or not name:
            raise ValueError("server_id and tool name are required")
        tool_id = str(tool.get("tool_id") or f"{server_id}:{name}")
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT discovered_at FROM mcp_tool_registry WHERE server_id = ? AND name = ?",
                (server_id, name),
            ).fetchone()
            discovered_at = existing["discovered_at"] if existing else now
            conn.execute(
                """
                INSERT OR REPLACE INTO mcp_tool_registry(
                    tool_id, server_id, name, description, input_schema_json,
                    enabled, status, discovered_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tool_id,
                    server_id,
                    name,
                    tool.get("description"),
                    json.dumps(tool.get("input_schema") or {}, ensure_ascii=False),
                    1 if tool.get("enabled", True) else 0,
                    str(tool.get("status") or "available"),
                    discovered_at,
                    now,
                ),
            )
        return self.get_mcp_tool(server_id, name) or {}

    def prune_mcp_tools(self, server_id: str, keep_names: set[str]) -> int:
        names = {str(name).strip() for name in keep_names if str(name).strip()}
        if not server_id:
            return 0
        with self._connect() as conn:
            if not names:
                cursor = conn.execute("DELETE FROM mcp_tool_registry WHERE server_id = ?", (server_id,))
                return cursor.rowcount
            placeholders = ",".join("?" for _ in names)
            cursor = conn.execute(
                f"DELETE FROM mcp_tool_registry WHERE server_id = ? AND name NOT IN ({placeholders})",
                (server_id, *sorted(names)),
            )
            return cursor.rowcount

    def list_mcp_tools(self, server_id: str | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT tool_id, server_id, name, description, input_schema_json,
                   enabled, status, discovered_at, updated_at
            FROM mcp_tool_registry
        """
        params: list[Any] = []
        if server_id:
            query += " WHERE server_id = ?"
            params.append(server_id)
        query += " ORDER BY server_id ASC, name ASC"
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._mcp_tool_row_to_dict(row) for row in rows]

    def get_mcp_tool(self, server_id: str, name: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT tool_id, server_id, name, description, input_schema_json,
                       enabled, status, discovered_at, updated_at
                FROM mcp_tool_registry
                WHERE server_id = ? AND name = ?
                """,
                (server_id, name),
            ).fetchone()
        return self._mcp_tool_row_to_dict(row) if row else None

    def update_mcp_tool_enabled(self, server_id: str, name: str, enabled: bool) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE mcp_tool_registry SET enabled = ?, updated_at = ? WHERE server_id = ? AND name = ?",
                (1 if enabled else 0, utc_now_iso(), server_id, name),
            )
        return self.get_mcp_tool(server_id, name)

    def set_mcp_tool_approval(self, agent_code: str, server_id: str, tool_name: str, allowed: bool, reason: str | None = None) -> dict[str, Any]:
        now = utc_now_iso()
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT created_at FROM mcp_tool_approval
                WHERE agent_code = ? AND server_id = ? AND tool_name = ?
                """,
                (agent_code, server_id, tool_name),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT OR REPLACE INTO mcp_tool_approval(
                    agent_code, server_id, tool_name, allowed, reason, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (agent_code, server_id, tool_name, 1 if allowed else 0, reason, created_at, now),
            )
        return {"agent_code": agent_code, "server_id": server_id, "tool_name": tool_name, "allowed": allowed, "reason": reason, "created_at": created_at, "updated_at": now}

    def get_mcp_tool_approval(self, agent_code: str, server_id: str, tool_name: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT agent_code, server_id, tool_name, allowed, reason, created_at, updated_at
                FROM mcp_tool_approval
                WHERE agent_code = ? AND server_id = ? AND tool_name = ?
                """,
                (agent_code, server_id, tool_name),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["allowed"] = bool(item["allowed"])
        return item

    def save_mcp_call_log(self, log: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO mcp_tool_call_log(
                    call_id, server_id, tool_name, agent_code, input_json, output_json,
                    status, error_message, latency_ms, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log["call_id"],
                    log.get("server_id"),
                    log["tool_name"],
                    log.get("agent_code"),
                    json.dumps(log.get("input") or {}, ensure_ascii=False),
                    json.dumps(log.get("output") or {}, ensure_ascii=False),
                    log.get("status") or "unknown",
                    log.get("error_message"),
                    int(log.get("latency_ms") or 0),
                    log.get("created_at") or utc_now_iso(),
                ),
            )
        return log

    def list_mcp_call_logs(self, limit: int = 100, server_id: str | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT rowid AS sort_id, call_id, server_id, tool_name, agent_code, input_json, output_json,
                   status, error_message, latency_ms, created_at
            FROM mcp_tool_call_log
        """
        params: list[Any] = []
        if server_id:
            query += " WHERE server_id = ?"
            params.append(server_id)
        query += " ORDER BY rowid DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        logs = []
        for row in rows:
            item = dict(row)
            item.pop("sort_id", None)
            item["input"] = json.loads(item.pop("input_json") or "{}")
            item["output"] = json.loads(item.pop("output_json") or "{}")
            logs.append(item)
        return logs

    def create_benchmark_run(
        self,
        run_id: str,
        name: str,
        benchmark_type: str,
        config: dict[str, Any],
        summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO benchmark_run(
                    run_id, name, benchmark_type, status, config_json, summary_json,
                    started_at, finished_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    name,
                    benchmark_type,
                    "running",
                    json.dumps(config, ensure_ascii=False),
                    json.dumps(summary or {}, ensure_ascii=False),
                    now,
                    None,
                    now,
                ),
            )
        return self.get_benchmark_run(run_id) or {}

    def append_benchmark_result(self, result: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO benchmark_result(
                    run_id, case_id, server_id, tool_name, iteration, status, latency_ms,
                    error_message, input_json, output_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result["run_id"],
                    result["case_id"],
                    result.get("server_id"),
                    result.get("tool_name"),
                    int(result.get("iteration") or 1),
                    result.get("status") or "unknown",
                    int(result.get("latency_ms") or 0),
                    result.get("error_message"),
                    json.dumps(result.get("input") or {}, ensure_ascii=False),
                    json.dumps(result.get("output") or {}, ensure_ascii=False),
                    result.get("created_at") or utc_now_iso(),
                ),
            )
            result["id"] = cursor.lastrowid
        return result

    def finish_benchmark_run(self, run_id: str, status: str, summary: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE benchmark_run
                SET status = ?, summary_json = ?, finished_at = ?
                WHERE run_id = ?
                """,
                (status, json.dumps(summary, ensure_ascii=False), utc_now_iso(), run_id),
            )
        return self.get_benchmark_run(run_id) or {}

    def list_benchmark_runs(self, limit: int = 50, benchmark_type: str | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT rowid AS sort_id, run_id, name, benchmark_type, status, config_json,
                   summary_json, started_at, finished_at, created_at
            FROM benchmark_run
        """
        params: list[Any] = []
        if benchmark_type:
            query += " WHERE benchmark_type = ?"
            params.append(benchmark_type)
        query += " ORDER BY rowid DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._benchmark_run_row_to_dict(row) for row in rows]

    def get_benchmark_run(self, run_id: str, *, include_results: bool = True) -> dict[str, Any] | None:
        with self._connect() as conn:
            run_row = conn.execute(
                """
                SELECT rowid AS sort_id, run_id, name, benchmark_type, status, config_json,
                       summary_json, started_at, finished_at, created_at
                FROM benchmark_run
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if not run_row:
                return None
            run = self._benchmark_run_row_to_dict(run_row)
            if include_results:
                rows = conn.execute(
                    """
                    SELECT id, run_id, case_id, server_id, tool_name, iteration, status,
                           latency_ms, error_message, input_json, output_json, created_at
                    FROM benchmark_result
                    WHERE run_id = ?
                    ORDER BY id ASC
                    """,
                    (run_id,),
                ).fetchall()
                run["results"] = [self._benchmark_result_row_to_dict(row) for row in rows]
        return run

    def _mcp_server_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["args"] = json.loads(item.pop("args_json") or "[]")
        item["env"] = json.loads(item.pop("env_json") or "{}")
        item["enabled"] = bool(item["enabled"])
        return item

    def _mcp_tool_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["input_schema"] = json.loads(item.pop("input_schema_json") or "{}")
        item["enabled"] = bool(item["enabled"])
        return item

    def _benchmark_run_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item.pop("sort_id", None)
        item["config"] = json.loads(item.pop("config_json") or "{}")
        item["summary"] = json.loads(item.pop("summary_json") or "{}")
        item.setdefault("results", [])
        return item

    def _benchmark_result_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["input"] = json.loads(item.pop("input_json") or "{}")
        item["output"] = json.loads(item.pop("output_json") or "{}")
        return item

    def _usage_bucket(self, name: str) -> dict[str, Any]:
        return {
            "name": name,
            "calls": 0,
            "fallback_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "latency_ms": 0,
        }

    def _add_usage(
        self,
        bucket: dict[str, Any],
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        latency_ms: int,
        fallback_used: bool,
    ) -> None:
        bucket["calls"] += 1
        bucket["fallback_calls"] += 1 if fallback_used else 0
        bucket["input_tokens"] += input_tokens
        bucket["output_tokens"] += output_tokens
        bucket["total_tokens"] += total_tokens
        bucket["latency_ms"] += latency_ms

    def _finalize_usage(self, bucket: dict[str, Any]) -> dict[str, Any]:
        calls = int(bucket["calls"] or 0)
        return {
            **bucket,
            "avg_latency_ms": int(bucket["latency_ms"] / calls) if calls else 0,
            "fallback_rate": round(float(bucket["fallback_calls"]) / calls, 4) if calls else 0,
        }

    def _token_counts(self, usage: dict[str, Any]) -> tuple[int, int, int]:
        input_tokens = int(
            usage.get("input_tokens")
            or usage.get("prompt_tokens")
            or usage.get("input_token_count")
            or 0
        )
        output_tokens = int(
            usage.get("output_tokens")
            or usage.get("completion_tokens")
            or usage.get("output_token_count")
            or 0
        )
        total_tokens = int(usage.get("total_tokens") or usage.get("total_token_count") or input_tokens + output_tokens)
        return input_tokens, output_tokens, total_tokens

    def save_skill(self, skill: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        code = str(skill.get("code") or "").strip()
        if not code:
            raise ValueError("code is required")
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM skill_definition WHERE code = ?",
                (code,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT OR REPLACE INTO skill_definition(
                    code, name, description, version, author, source_type,
                    parameters_json, execution_json, is_enabled, created_at, updated_at,
                    source_url, skill_path
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    code,
                    str(skill.get("name") or code),
                    skill.get("description"),
                    skill.get("version"),
                    skill.get("author"),
                    str(skill.get("source_type") or "user"),
                    json.dumps(skill.get("parameters") or [], ensure_ascii=False),
                    json.dumps(skill.get("execution") or {}, ensure_ascii=False),
                    1 if skill.get("is_enabled", True) else 0,
                    created_at,
                    now,
                    skill.get("source_url", ""),
                    skill.get("skill_path", ""),
                ),
            )
        return self.get_skill(code) or {}

    def get_skill(self, code: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT code, name, description, version, author, source_type,
                       parameters_json, execution_json, is_enabled, created_at, updated_at,
                       source_url, skill_path
                FROM skill_definition WHERE code = ?
                """,
                (code,),
            ).fetchone()
        return self._skill_row_to_dict(row) if row else None

    def list_skills(self, source_type: str | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT code, name, description, version, author, source_type,
                   parameters_json, execution_json, is_enabled, created_at, updated_at,
                   source_url, skill_path
            FROM skill_definition
        """
        params: list[Any] = []
        if source_type:
            query += " WHERE source_type = ?"
            params.append(source_type)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._skill_row_to_dict(row) for row in rows]

    def delete_skill(self, code: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM skill_definition WHERE code = ?", (code,))
        return cursor.rowcount > 0

    def update_skill_enabled(self, code: str, enabled: bool) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE skill_definition SET is_enabled = ?, updated_at = ? WHERE code = ?",
                (1 if enabled else 0, utc_now_iso(), code),
            )
        return self.get_skill(code)

    def _skill_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["parameters"] = json.loads(item.pop("parameters_json") or "[]")
        item["execution"] = json.loads(item.pop("execution_json") or "{}")
        item["is_enabled"] = bool(item["is_enabled"])
        return item

    def seed_builtin_skills(self) -> None:
        from app.skills.builtin import builtin_skills

        for skill in builtin_skills():
            existing = self.get_skill(skill.code)
            if existing and existing.get("source_type") == "builtin":
                continue
            if existing:
                continue
            self.save_skill({
                "code": skill.code,
                "name": skill.name,
                "description": skill.description,
                "source_type": "builtin",
                "parameters": [],
                "execution": {"type": "builtin", "handler": skill.code},
            })


task_store = SQLiteTaskStore()
