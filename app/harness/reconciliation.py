"""Explicit operator decisions for uncertain external effects, with an audit trail."""
import json
import time
from uuid import uuid4

from app.harness.durable import journal, RunBusyError


def reconcile(run_id: str, operation_id: str, outcome: str, reason: str, actor_id: str,
              output=None, extra: dict | None = None):
    if outcome not in {"not_executed", "completed"} or len(reason.strip()) < 5:
        raise ValueError("A verified outcome and reconciliation reason are required")
    with journal() as conn:
        conn.execute("BEGIN IMMEDIATE")
        run = conn.execute("SELECT lease_until FROM workflow_run WHERE run_id=?", (run_id,)).fetchone()
        if run and (run["lease_until"] or 0) > time.time():
            raise RunBusyError("Cannot reconcile an executing run")
        row = conn.execute("SELECT status FROM node_operation WHERE run_id=? AND operation_id=?", (run_id, operation_id)).fetchone()
        if not row:
            raise KeyError(operation_id)
        if row["status"] not in {"running", "uncertain"}:
            raise ValueError("Only uncertain operations can be reconciled")
        conn.execute("""CREATE TABLE IF NOT EXISTS operation_reconciliation(
            id TEXT PRIMARY KEY,operation_id TEXT NOT NULL,actor_id TEXT NOT NULL,
            outcome TEXT NOT NULL,reason TEXT NOT NULL,created_at REAL NOT NULL)""")
        conn.execute("INSERT INTO operation_reconciliation VALUES(?,?,?,?,?,?)", (uuid4().hex, operation_id, actor_id, outcome, reason, time.time()))
        conn.execute("UPDATE node_operation SET status=?,result_json=? WHERE operation_id=?",
                     ("completed" if outcome == "completed" else "failed", json.dumps([output, extra or {}]) if outcome == "completed" else None, operation_id))
