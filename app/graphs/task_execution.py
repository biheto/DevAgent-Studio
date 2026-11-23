"""Dispatch recovery by persisted graph kind, never by a caller-supplied execution mode."""
from app.harness.durable import load_run


def runner_for(stored: dict):
    if stored["definition"].get("graph_type") == "collaboration":
        if stored["input"].get("business_scenario") in {"onboarding", "governance"}:
            from app.business_scenarios import run_business_scenario
            return run_business_scenario
        from app.graphs.collaboration_runner import run_collaboration_task
        return run_collaboration_task
    from app.graphs.workflow_compiler import run_task_workflow
    return run_task_workflow


def resume_task_run(checkpoint: dict, action: str, comment: str | None = None) -> dict:
    if checkpoint.get("durable"):
        stored = load_run(str(checkpoint["run_id"]))
        return runner_for(stored)({**stored["input"], **stored["definition"],
                                   "durable_action": action, "review_comment": comment})
    from app.graphs.workflow_compiler import resume_task_workflow
    return resume_task_workflow(checkpoint, action, comment)
