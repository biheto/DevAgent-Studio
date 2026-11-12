"""Persistent graph recovery with leases and a conservative side-effect journal."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command


class RunBusyError(RuntimeError):
    pass


class UncertainSideEffect(RuntimeError):
    """A tool may have succeeded before a crash; never blindly replay it."""


def checkpoint_path() -> Path:
    path = Path(os.getenv("DEV_AGENT_CHECKPOINT_DB", "data/workflow_checkpoints.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def journal():
    conn = sqlite3.connect(str(checkpoint_path()) + ".journal", timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS workflow_run(
            run_id TEXT PRIMARY KEY, definition_hash TEXT NOT NULL,
            definition_json TEXT NOT NULL, input_json TEXT NOT NULL,
            status TEXT NOT NULL, lease_token TEXT, lease_until REAL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS node_operation(
            operation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, node_id TEXT NOT NULL,
            status TEXT NOT NULL, result_json TEXT, attempts INTEGER NOT NULL DEFAULT 1)""")
        yield conn
        conn.commit()
    finally:
        conn.close()


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def load_run(run_id: str) -> dict:
    with journal() as conn:
        row = conn.execute("SELECT * FROM workflow_run WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        raise KeyError(run_id)
    return {**dict(row), "definition": json.loads(row["definition_json"]), "input": json.loads(row["input_json"])}


@contextmanager
def run_lease(run_id: str, definition: dict, initial: dict):
    token = uuid4().hex
    ttl = max(2.0, float(os.getenv("DEV_AGENT_RUN_LEASE_SECONDS", "90")))
    with journal() as conn:
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute("SELECT * FROM workflow_run WHERE run_id=?", (run_id,)).fetchone()
        if old and old["definition_hash"] != digest(definition):
            raise ValueError("Workflow definition changed; create a new run instead of resuming.")
        if old and (old["lease_until"] or 0) > time.time():
            raise RunBusyError("Workflow is already executing; wait for its lease to expire after a crash.")
        conn.execute("""INSERT INTO workflow_run VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(run_id) DO UPDATE SET lease_token=excluded.lease_token,lease_until=excluded.lease_until""",
            (run_id, digest(definition), json.dumps(definition), json.dumps(initial), "running", token, time.time() + ttl))
    stop = threading.Event()

    def renew():
        while not stop.wait(ttl / 3):
            with journal() as conn:
                conn.execute("UPDATE workflow_run SET lease_until=? WHERE run_id=? AND lease_token=?",
                             (time.time() + ttl, run_id, token))

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)
        with journal() as conn:
            conn.execute("UPDATE workflow_run SET lease_until=NULL,lease_token=NULL WHERE run_id=? AND lease_token=?", (run_id, token))


def execute_operation(node: dict, state: dict, outputs: dict, execute):
    run_id = state.get("durable_run_id")
    if not run_id:
        return execute()
    operation_id = digest({"run": run_id, "node": node, "input": {
        "current": state.get("current"), "goal": state.get("goal"),
        "project_path": state.get("project_path"), "outputs": outputs}})
    side_effect = node.get("type") in {"skill", "mcp_tool"} and not node.get("config", {}).get("read_only", False)
    with journal() as conn:
        old = conn.execute("SELECT * FROM node_operation WHERE operation_id=?", (operation_id,)).fetchone()
        if old and old["status"] == "completed":
            result = json.loads(old["result_json"])
            return result[0], result[1]
        if old and old["status"] in {"running", "uncertain"} and side_effect:
            raise UncertainSideEffect(f"Reconcile tool operation {operation_id} before retrying; it may already have executed.")
        conn.execute("""INSERT INTO node_operation(operation_id,run_id,node_id,status) VALUES(?,?,?,'running')
            ON CONFLICT(operation_id) DO UPDATE SET status='running',attempts=attempts+1""", (operation_id, run_id, node["id"]))
    try:
        result = execute()
    except Exception:
        with journal() as conn:
            conn.execute("UPDATE node_operation SET status=? WHERE operation_id=?",
                         ("uncertain" if side_effect else "failed", operation_id))
        raise
    with journal() as conn:
        conn.execute("UPDATE node_operation SET status='completed',result_json=? WHERE operation_id=?",
                     (json.dumps(result, ensure_ascii=False), operation_id))
    return result


def execute_graph(build, definition: dict, initial: dict, action: str | None = None) -> dict:
    run_id = str(initial["task_id"])
    config = {"configurable": {"thread_id": run_id}}
    initial = {**initial, "durable_run_id": run_id}
    with run_lease(run_id, definition, initial), SqliteSaver.from_conn_string(str(checkpoint_path())) as saver:
        graph = build(saver)
        def invoke(value):
            try:
                return graph.invoke(value, config, durability="sync")
            except Exception as exc:
                return {**dict(graph.get_state(config).values), "execution_status": "failed", "execution_error": str(exc)}
        snapshot = graph.get_state(config)
        pending_interrupts = [item for task in snapshot.tasks for item in task.interrupts]
        if pending_interrupts:
            if action not in {"approved", "approve", "rejected", "reject"}:
                result = dict(snapshot.values)
                result["__interrupt__"] = pending_interrupts
            else:
                # Approve one interrupt at a time, including fan-out branches.
                result = invoke(Command(resume={pending_interrupts[0].id: action}))
        else:
            try:
                if snapshot.values and not snapshot.next:
                    result = dict(snapshot.values)
                else:
                    result = invoke(None if snapshot.values else initial)
            except Exception as exc:
                result = dict(graph.get_state(config).values)
                result["execution_status"] = "failed"
                result["execution_error"] = str(exc)
        interrupts = result.get("__interrupt__", [])
        if interrupts:
            result["human_review_packet"] = {**interrupts[0].value, "interrupt_id": interrupts[0].id}
            result["execution_status"] = "waiting_review"
        else:
            latest = {}
            for event in result.get("events", []):
                latest[event.get("node")] = event.get("status")
            result.setdefault("execution_status", "cancelled" if "cancelled" in latest.values() else
                              "partial_success" if "failed" in latest.values() else "completed")
        if result["execution_status"] in {"failed", "waiting_review"}:
            next_nodes = graph.get_state(config).next
            result["durable_checkpoint"] = {"durable": True, "run_id": run_id,
                "paused_node_id": (result.get("human_review_packet") or {}).get("node_id") or (next_nodes[0] if next_nodes else "resume"),
                **definition, "state": {"task_id": run_id}, "input_text": initial.get("input_text", "")}
        with journal() as conn:
            conn.execute("UPDATE workflow_run SET status=? WHERE run_id=?", (result["execution_status"], run_id))
        return result
