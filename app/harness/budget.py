"""Durable, atomic per-task LLM reservations. Prices are user configuration, not tariffs."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
import math
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.harness.durable import journal


class BudgetOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    max_cost_usd: float | None = Field(default=None, ge=0, le=10000)
    max_tokens: int | None = Field(default=100000, ge=1, le=10000000)
    output_tokens_per_call: int = Field(default=2048, ge=128, le=16384)
    input_price_per_1m: float | None = Field(default=None, ge=0, le=100000)
    output_price_per_1m: float | None = Field(default=None, ge=0, le=100000)


class BudgetExceeded(RuntimeError):
    pass


active_task: ContextVar[str | None] = ContextVar("budget_task", default=None)


@contextmanager
def connection():
    with journal() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS task_budget(task_id TEXT PRIMARY KEY, options TEXT NOT NULL)")
        conn.execute("""CREATE TABLE IF NOT EXISTS budget_call(
            call_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, agent TEXT, model TEXT,
            status TEXT, input_tokens INTEGER, output_tokens INTEGER, cost REAL,
            input_price REAL, output_price REAL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_budget_call_task ON budget_call(task_id)")
        yield conn


@contextmanager
def task_budget(task_id: str, options: dict):
    options = BudgetOptions.model_validate(options).model_dump()
    with connection() as conn:
        conn.execute("INSERT OR IGNORE INTO task_budget VALUES(?,?)", (task_id, json.dumps(options)))
    token = active_task.set(task_id)
    try:
        yield
    finally:
        active_task.reset(token)


def options_for(task_id: str) -> dict:
    with connection() as conn:
        row = conn.execute("SELECT options FROM task_budget WHERE task_id=?", (task_id,)).fetchone()
    if not row:
        raise KeyError(task_id)
    return json.loads(row[0])


def prices(provider, model: str, options: dict) -> tuple[float | None, float | None]:
    env = provider._read_env_file()
    key = provider._agent_env_key(model)
    result = []
    for side in ("input", "output"):
        raw = options.get(f"{side}_price_per_1m")
        if raw is None:
            raw = provider._read_env_value(f"DEV_AGENT_LLM_PRICE_{key}_{side.upper()}_PER_1M", env)
        try:
            value = float(raw)
            result.append(value if math.isfinite(value) and value >= 0 else None)
        except (ValueError, TypeError):
            result.append(None)
    return tuple(result)


def cost_for(input_tokens, output_tokens, rates):
    return None if None in rates else (input_tokens * rates[0] + output_tokens * rates[1]) / 1000000


def reserve(provider, agent: str, model: str, prompt: str) -> tuple[str | None, int | None]:
    task_id = active_task.get()
    if not task_id:
        return None, None
    options = options_for(task_id)
    # UTF-8 byte count plus message overhead is deliberately conservative.
    input_tokens = len(prompt.encode("utf-8")) + 128
    output_tokens = options["output_tokens_per_call"]
    rates = prices(provider, model, options)
    cost = cost_for(input_tokens, output_tokens, rates)
    call_id = uuid4().hex
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        used = conn.execute("SELECT COALESCE(SUM(input_tokens+output_tokens),0), COALESCE(SUM(cost),0), SUM(cost IS NULL) FROM budget_call WHERE task_id=?", (task_id,)).fetchone()
        if options["max_cost_usd"] is not None and (cost is None or used[2]):
            raise BudgetExceeded("模型单价缺失，无法保证费用预算；请补充输入和输出单价后继续。")
        if options["max_tokens"] is not None and used[0] + input_tokens + output_tokens > options["max_tokens"]:
            raise BudgetExceeded("剩余 token 预算不足以预留下一次调用；请提高预算后继续。")
        if options["max_cost_usd"] is not None and used[1] + cost > options["max_cost_usd"]:
            raise BudgetExceeded("剩余费用预算不足以预留下一次调用；请提高预算后继续。")
        conn.execute("INSERT INTO budget_call VALUES(?,?,?,?,?,?,?,?,?,?)", (call_id, task_id, agent, model, "reserved", input_tokens, output_tokens, cost, *rates))
    return call_id, output_tokens


def settle(call_id: str | None, usage: dict | None, provider):
    if not call_id:
        return
    with connection() as conn:
        row = conn.execute("SELECT * FROM budget_call WHERE call_id=?", (call_id,)).fetchone()
        if not row or row["status"] != "reserved":
            return
        if (usage and any(key in usage for key in ("input_tokens", "prompt_tokens", "input_token_count"))
                and any(key in usage for key in ("output_tokens", "completion_tokens", "output_token_count"))):
            inp, out, _ = provider._token_counts(usage)
            inp, out = max(0, inp), max(0, out)
            cost = cost_for(inp, out, (row["input_price"], row["output_price"]))
            conn.execute("UPDATE budget_call SET status='reported',input_tokens=?,output_tokens=?,cost=? WHERE call_id=?", (inp, out, cost, call_id))
        else:
            # A timeout/absent usage may still be billable. Keep the reservation charged.
            conn.execute("UPDATE budget_call SET status='uncertain' WHERE call_id=?", (call_id,))


def snapshot(task_id: str) -> dict:
    options = options_for(task_id)
    with connection() as conn:
        calls = [dict(row) for row in conn.execute("SELECT agent,model,status,input_tokens,output_tokens,cost FROM budget_call WHERE task_id=?", (task_id,))]
    reported = [row for row in calls if row["status"] == "reported"]
    return {"options": options, "currency": "USD", "calls": len(calls),
            "reported_tokens": sum(row["input_tokens"] + row["output_tokens"] for row in reported),
            "accounted_tokens": sum(row["input_tokens"] + row["output_tokens"] for row in calls),
            "estimated_cost_usd": None if any(row["cost"] is None for row in calls) else sum(row["cost"] for row in calls),
            "reserved_calls": sum(row["status"] == "reserved" for row in calls),
            "uncertain_calls": sum(row["status"] == "uncertain" for row in calls),
            "usage": calls, "cost_basis": "configured_prices_with_conservative_reservations"}


def update_budget(task_id: str, options: dict):
    options = BudgetOptions.model_validate(options).model_dump()
    options_for(task_id)
    with connection() as conn:
        conn.execute("UPDATE task_budget SET options=? WHERE task_id=?", (json.dumps(options), task_id))
        from app.providers.llm_provider import llm_provider
        rows = conn.execute("SELECT * FROM budget_call WHERE task_id=? AND cost IS NULL", (task_id,)).fetchall()
        for row in rows:
            rates = prices(llm_provider, row["model"], options)
            cost = cost_for(row["input_tokens"], row["output_tokens"], rates)
            if cost is not None:
                conn.execute("UPDATE budget_call SET cost=?,input_price=?,output_price=? WHERE call_id=?", (cost, *rates, row["call_id"]))
    return snapshot(task_id)


def estimate(provider, options: dict, *, max_files: int, repair_rounds: int = 0) -> dict:
    options = BudgetOptions.model_validate(options).model_dump()
    agents = ["planner", "project_analyzer", "code_reviewer", "supervisor", "reporter"]
    agents += ["repairer", "planner", "code_reviewer", "supervisor"] * repair_rounds
    rows = []
    for agent in agents:
        config = provider._config(agent)
        inp = 1500 + min(max_files, 500) * 80 if agent in {"code_reviewer", "project_analyzer", "repairer"} else 3000
        out = options["output_tokens_per_call"]
        cost = cost_for(inp, out, prices(provider, config["model"], options)) if config["api_key"] else 0
        rows.append({"agent": agent, "model": config["model"], "input_tokens": inp if config["api_key"] else 0,
                     "output_tokens": out if config["api_key"] else 0, "estimated_cost_usd": cost})
    return {"currency": "USD", "estimated_cost_usd": None if any(r["estimated_cost_usd"] is None for r in rows) else sum(r["estimated_cost_usd"] for r in rows),
            "estimated_tokens": sum(r["input_tokens"] + r["output_tokens"] for r in rows), "steps": rows,
            "basis": "粗估：按文件上限和最大修复轮次计算，不是报价；实际调用数量、上下文和供应商计费可能不同。"}
