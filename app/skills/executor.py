"""Centralized skill execution logic.

All skill execution paths (API endpoint, SkillRegistry, workflow compiler)
must go through execute_skill_by_type() to avoid code duplication and ensure
consistent error handling, logging, and type safety.
"""
from __future__ import annotations

import json as _json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _safe_int(value: Any, default: int = 200) -> int:
    """Safely cast value to int, returning default on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def execute_skill_by_type(
    skill_code: str,
    execution: dict[str, Any],
    input_data: dict[str, Any],
    goal: str = "",
    project_path: str | None = None,
    max_files: int = 200,
) -> dict[str, Any]:
    """Execute a skill by its execution type.

    This is the single entry point for all skill execution. It handles
    prompt, builtin, workflow, and agent types with consistent error
    handling and logging.

    Args:
        skill_code: The skill identifier.
        execution: The execution config dict from skill DB row.
        input_data: Input data for the skill.
        goal: The task goal (injected if not in input_data).
        project_path: Optional project path (injected if not in input_data).
        max_files: Max files for agent-type skills.

    Returns:
        Result dict with 'output' key on success, or 'error' key on failure.
    """
    exec_type = execution.get("type", "prompt")
    input_data.setdefault("goal", goal)
    if project_path:
        input_data.setdefault("project_path", project_path)

    logger.info("Executing skill %s (type=%s)", skill_code, exec_type)

    try:
        if exec_type == "prompt":
            return _execute_prompt(skill_code, execution, input_data)
        elif exec_type == "builtin":
            return _execute_builtin(skill_code, execution, input_data)
        elif exec_type == "workflow":
            return _execute_workflow(skill_code, execution, input_data)
        elif exec_type == "agent":
            return _execute_agent(skill_code, execution, input_data, goal, project_path, max_files)
        else:
            logger.warning("Skill %s has unknown execution type: %s", skill_code, exec_type)
            return {"output": f"Skill {skill_code} execution type '{exec_type}' completed.", "input": input_data}
    except Exception as exc:
        logger.error("Skill %s failed: %s", skill_code, exc, exc_info=True)
        return {"error": str(exc), "input": input_data}


def _execute_prompt(
    skill_code: str,
    execution: dict[str, Any],
    input_data: dict[str, Any],
) -> dict[str, Any]:
    """Execute a prompt-type skill by calling LLM with the SKILL.md as system prompt."""
    from app.providers.llm_provider import llm_provider

    system_prompt = execution.get("prompt", "")
    goal = input_data.get("goal", "")
    user_prompt = f"Goal: {goal}\nInput: {_json.dumps(input_data, ensure_ascii=False)}"
    fallback = f"Skill {skill_code} prompt executed. Input: {_json.dumps(input_data, ensure_ascii=False)}"

    llm_result = llm_provider.generate(
        system_prompt,
        user_prompt,
        fallback,
        agent="skill",
        prompt_version=f"skill.{skill_code}.v1",
    )
    return {"output": llm_result, "input": input_data, "skill_code": skill_code}


def _execute_builtin(
    skill_code: str,
    execution: dict[str, Any],
    input_data: dict[str, Any],
) -> dict[str, Any]:
    """Execute a builtin-type skill by dynamically importing and running Python code.

    Supports two modes:
    1. module_path + class_name: Dynamic import from any Python module
    2. handler: Legacy builtin skill registered in builtin_skills()
    """
    import importlib

    module_path = execution.get("module_path", "")
    class_name = execution.get("class_name", "")
    handler = execution.get("handler", "")

    # Legacy handler-based builtin: route through SkillRegistry
    if handler and not module_path:
        from app.skills.registry import SkillRegistry
        try:
            registry = SkillRegistry()
            skill_instance = registry.get(handler)
            from app.skills.base import SkillContext
            context = SkillContext(agent_code="skill_executor")
            result = skill_instance.execute(context, input_data)
            return {"output": result, "input": input_data, "skill_code": skill_code}
        except KeyError:
            return {"error": f"Builtin skill '{handler}' not found in registry", "input": input_data}

    if not module_path or not class_name:
        return {"output": f"Skill {skill_code} builtin config incomplete (module_path={module_path}, class_name={class_name})", "input": input_data}

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        return {"error": f"Failed to import module '{module_path}': {exc}", "input": input_data}

    skill_class = getattr(module, class_name, None)
    if skill_class is None:
        return {"error": f"Class '{class_name}' not found in module '{module_path}'", "input": input_data}

    skill_instance = skill_class()
    from app.skills.base import SkillContext
    context = SkillContext(agent_code="skill_executor")
    result = skill_instance.execute(context, input_data)
    return {"output": result, "input": input_data, "skill_code": skill_code}


def _execute_workflow(
    skill_code: str,
    execution: dict[str, Any],
    input_data: dict[str, Any],
) -> dict[str, Any]:
    """Execute a workflow-type skill by running a compiled LangGraph workflow."""
    from app.graphs.workflow_compiler import run_compiled_workflow

    nodes = execution.get("nodes", [])
    edges = execution.get("edges", [])
    if not nodes:
        return {"output": "Skill has no workflow nodes defined.", "input": input_data}

    return run_compiled_workflow(
        workflow_name=skill_code,
        input_text=_json.dumps(input_data, ensure_ascii=False),
        nodes=nodes,
        edges=edges,
    )


def _execute_agent(
    skill_code: str,
    execution: dict[str, Any],
    input_data: dict[str, Any],
    goal: str,
    project_path: str | None,
    max_files: int,
) -> dict[str, Any]:
    """Execute an agent-type skill by dispatching to the existing agent tools."""
    from app.graphs.workflow_compiler import _run_agent_node

    agent_type = execution.get("agent_type", "")
    agent_config = execution.get("agent_config", {})
    if not agent_type:
        return {"output": f"Skill {skill_code} agent type not configured.", "input": input_data}

    agent_node = {"id": "skill_agent", "type": "agent", "config": {"agent_type": agent_type, **agent_config}}
    output, _extra = _run_agent_node(
        agent_node,
        {**agent_config, "agent_type": agent_type},
        goal,
        project_path,
        max_files,
    )
    return {"output": output, "input": input_data, "agent_type": agent_type, "skill_code": skill_code}
