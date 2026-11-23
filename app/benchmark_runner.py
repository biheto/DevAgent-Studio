from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.harness.events import utc_now_iso
from app.persistence.sqlite_store import task_store
from app.persistence.rag_store import rag_store
from app.providers.llm_provider import llm_provider
from app.providers.mcp_provider import mcp_provider
from app.graphs.collaboration_runner import run_collaboration_task
from app.graphs.workflow_compiler import run_compiled_workflow


def default_mcp_benchmark_cases() -> list[dict[str, Any]]:
    project_path = Path.cwd().as_posix()
    return [
        {
            "case_id": "fs_read_readme",
            "server_id": "real_filesystem",
            "tool_name": "read_text_file",
            "arguments": {"path": f"{project_path}/README.md", "head": 5},
            "enabled": True,
        },
        {
            "case_id": "fs_list_project",
            "server_id": "real_filesystem",
            "tool_name": "list_directory",
            "arguments": {"path": project_path},
            "enabled": True,
        },
        {
            "case_id": "fs_search_mcp",
            "server_id": "real_filesystem",
            "tool_name": "search_files",
            "arguments": {
                "path": project_path,
                "pattern": "**/*mcp*.py",
                "excludePatterns": ["**/.venv/**", "**/node_modules/**", "**/__pycache__/**"],
            },
            "enabled": True,
        },
        {
            "case_id": "memory_search_project",
            "server_id": "real_memory",
            "tool_name": "search_nodes",
            "arguments": {"query": "DevAgent"},
            "enabled": True,
        },
        {
            "case_id": "memory_read_graph",
            "server_id": "real_memory",
            "tool_name": "read_graph",
            "arguments": {},
            "enabled": True,
        },
    ]


def run_mcp_benchmark(payload: dict[str, Any]) -> dict[str, Any]:
    cases = [case for case in (payload.get("cases") or default_mcp_benchmark_cases()) if case.get("enabled", True)]
    iterations = max(1, min(20, int(payload.get("iterations") or 3)))
    agent_code = str(payload.get("agent_code") or "benchmark_runner")
    run_id = f"bench_{uuid4().hex}"
    config = {
        "agent_code": agent_code,
        "iterations": iterations,
        "case_count": len(cases),
        "cases": cases,
    }
    task_store.create_benchmark_run(
        run_id=run_id,
        name=str(payload.get("name") or "MCP Tool Benchmark"),
        benchmark_type="mcp",
        config=config,
    )

    results: list[dict[str, Any]] = []
    for case in cases:
        for iteration in range(1, iterations + 1):
            results.append(_run_single_mcp_case(run_id, case, iteration, agent_code))

    summary = _summarize_benchmark(results, cases, iterations)
    status = "completed" if summary["failed"] == 0 else "completed_with_failures"
    return task_store.finish_benchmark_run(run_id, status, summary)


def default_llm_benchmark_cases() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "planner_v1_project_plan",
            "arguments": {
                "agent": "planner",
                "prompt_version": "planner.v1",
                "system_prompt": "You are a concise software project planning agent.",
                "user_prompt": "Plan how to analyze DevAgent Studio from architecture, risks, knowledge, and workflow.",
                "fallback": "Analyze architecture, risks, knowledge assets, workflow runtime, and next actions.",
                "expected_keywords": ["architecture", "risk", "workflow"],
            },
            "enabled": True,
        },
        {
            "case_id": "reporter_v1_governance_report",
            "arguments": {
                "agent": "reporter",
                "prompt_version": "reporter.v1",
                "system_prompt": "You are a software governance report writer.",
                "user_prompt": "Write a concise governance summary for a multi-agent project analysis workbench.",
                "fallback": "The report should cover quality, risk, review, traceability, and next actions.",
                "expected_keywords": ["quality", "risk", "traceability"],
            },
            "enabled": True,
        },
    ]


def run_llm_benchmark(payload: dict[str, Any]) -> dict[str, Any]:
    cases = [case for case in (payload.get("cases") or default_llm_benchmark_cases()) if case.get("enabled", True)]
    return _run_generic_benchmark(payload, "llm", cases, _run_single_llm_case, _summarize_llm_benchmark)


def default_rag_benchmark_cases() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "project_memory_workflow",
            "arguments": {
                "collection": "project-memory",
                "question": "workflow runtime human review resume",
                "expected_keywords": ["workflow", "review", "resume"],
                "limit": 5,
            },
            "enabled": True,
        },
        {
            "case_id": "default_project_structure",
            "arguments": {
                "collection": "default",
                "question": "project structure FastAPI LangGraph agents",
                "expected_keywords": ["FastAPI", "LangGraph", "Agent"],
                "limit": 5,
            },
            "enabled": True,
        },
    ]


def run_rag_benchmark(payload: dict[str, Any]) -> dict[str, Any]:
    raw_cases = payload.get("cases") or _rag_gold_cases_for_benchmark(payload) or default_rag_benchmark_cases()
    cases = [case for case in raw_cases if case.get("enabled", True)]
    return _run_generic_benchmark(payload, "rag", cases, _run_single_rag_case, _summarize_rag_benchmark)


def default_workflow_benchmark_cases() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "planner_reporter_smoke",
            "arguments": {
                "workflow_name": "benchmark_planner_reporter",
                "input_text": "Create a short governance summary for DevAgent Studio.",
                "nodes": [
                    {"id": "plan", "type": "planner", "name": "Planner", "config": {}},
                    {"id": "report", "type": "reporter", "name": "Reporter", "config": {}},
                ],
                "edges": [{"source": "plan", "target": "report"}],
                "expected_nodes": ["plan", "report"],
            },
            "enabled": True,
        },
        {
            "case_id": "agent_reporter_smoke",
            "arguments": {
                "workflow_name": "benchmark_agent_reporter",
                "input_text": "Analyze the project at a high level and produce a final report.",
                "nodes": [
                    {"id": "agent", "type": "agent", "name": "Project Agent", "config": {"agent_type": "project_analyzer", "max_files": 40}},
                    {"id": "report", "type": "reporter", "name": "Reporter", "config": {}},
                ],
                "edges": [{"source": "agent", "target": "report"}],
                "expected_nodes": ["agent", "report"],
            },
            "enabled": True,
        },
    ]


def run_workflow_benchmark(payload: dict[str, Any]) -> dict[str, Any]:
    cases = [case for case in (payload.get("cases") or default_workflow_benchmark_cases()) if case.get("enabled", True)]
    return _run_generic_benchmark(payload, "workflow", cases, _run_single_workflow_case, _summarize_workflow_benchmark)


def default_collaboration_benchmark_cases() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "collab_project_governance",
            "arguments": {
                "goal": "Analyze DevAgent Studio and produce project structure, code risk, knowledge, and governance suggestions.",
                "project_path": Path.cwd().as_posix(),
                "max_files": 80,
                "require_human_review": True,
                "expected_sections": ["Project", "Code", "RAG", "Supervisor"],
                "expected_risk_keywords": ["risk", "review", "governance", "quality"],
            },
            "enabled": True,
        }
    ]


def run_collaboration_benchmark(payload: dict[str, Any]) -> dict[str, Any]:
    cases = [case for case in (payload.get("cases") or default_collaboration_benchmark_cases()) if case.get("enabled", True)]
    return _run_generic_benchmark(payload, "collaboration", cases, _run_single_collaboration_case, _summarize_collaboration_benchmark)


def _run_generic_benchmark(
    payload: dict[str, Any],
    benchmark_type: str,
    cases: list[dict[str, Any]],
    case_runner: Any,
    summarizer: Any,
) -> dict[str, Any]:
    iterations = max(1, min(20, int(payload.get("iterations") or 1)))
    run_id = f"bench_{uuid4().hex}"
    config = {
        "agent_code": str(payload.get("agent_code") or "benchmark_runner"),
        "iterations": iterations,
        "case_count": len(cases),
        "cases": cases,
    }
    task_store.create_benchmark_run(
        run_id=run_id,
        name=str(payload.get("name") or f"{benchmark_type.upper()} Benchmark"),
        benchmark_type=benchmark_type,
        config=config,
    )
    results: list[dict[str, Any]] = []
    for case in cases:
        for iteration in range(1, iterations + 1):
            results.append(case_runner(run_id, case, iteration, config["agent_code"]))
    summary = summarizer(results, cases, iterations)
    status = "completed" if summary["failed"] == 0 else "completed_with_failures"
    return task_store.finish_benchmark_run(run_id, status, summary)


def _run_single_mcp_case(run_id: str, case: dict[str, Any], iteration: int, agent_code: str) -> dict[str, Any]:
    started = time.perf_counter()
    output: dict[str, Any] = {}
    error_message: str | None = None
    status = "completed"
    try:
        output = mcp_provider.call_tool(
            str(case.get("tool_name") or ""),
            dict(case.get("arguments") or {}),
            server_id=case.get("server_id"),
            agent_code=agent_code,
        )
        if output.get("status") == "failed":
            status = "failed"
            error_message = str(output.get("error_message") or "MCP tool returned failed status")
    except Exception as exc:
        status = "failed"
        error_message = str(exc)

    latency_ms = max(0, int((time.perf_counter() - started) * 1000))
    result = {
        "run_id": run_id,
        "case_id": str(case.get("case_id") or case.get("tool_name") or "case"),
        "server_id": case.get("server_id"),
        "tool_name": case.get("tool_name"),
        "iteration": iteration,
        "status": status,
        "latency_ms": latency_ms,
        "error_message": error_message,
        "input": dict(case.get("arguments") or {}),
        "output": _compact_json(output),
        "created_at": utc_now_iso(),
    }
    return task_store.append_benchmark_result(result)


def _run_single_llm_case(run_id: str, case: dict[str, Any], iteration: int, agent_code: str) -> dict[str, Any]:
    started = time.perf_counter()
    args = dict(case.get("arguments") or {})
    status = "completed"
    error_message: str | None = None
    output: dict[str, Any] = {}
    try:
        response = llm_provider.generate_with_status(
            str(args.get("system_prompt") or "You are a benchmarked software engineering agent."),
            str(args.get("user_prompt") or args.get("prompt") or ""),
            str(args.get("fallback") or "Fallback benchmark answer."),
            agent=str(args.get("agent") or agent_code or "benchmark_runner"),
            prompt_version=str(args.get("prompt_version") or "v1"),
            use_active_prompt=bool(args.get("use_active_prompt", False)),
            model_override=str(args.get("model") or "").strip() or None,
        )
        text = str(response.get("text") or "")
        expected_keywords = [str(item).lower() for item in args.get("expected_keywords", [])]
        keyword_hits = sum(1 for keyword in expected_keywords if keyword and keyword in text.lower())
        token_usage = response.get("token_usage") if isinstance(response.get("token_usage"), dict) else {}
        input_tokens, output_tokens, total_tokens = _token_counts(token_usage, text)
        quality_score = _quality_score(text, bool(response.get("fallback_used")), expected_keywords, keyword_hits, total_tokens, int(response.get("latency_ms") or 0))
        model = str(response.get("model") or args.get("model") or "fallback")
        cost = _estimate_llm_cost(model, input_tokens, output_tokens)
        output = {
            **response,
            "quality_score": quality_score,
            "keyword_hits": keyword_hits,
            "expected_keyword_count": len(expected_keywords),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "estimated_cost_usd": cost,
        }
        if response.get("fallback_used"):
            status = "failed"
            error_message = str(response.get("error_message") or "LLM fallback was used")
    except Exception as exc:
        status = "failed"
        error_message = str(exc)
    return _append_case_result(run_id, case, iteration, status, started, args, output, error_message, tool_name=str(args.get("prompt_version") or "llm.generate"))


def _run_single_rag_case(run_id: str, case: dict[str, Any], iteration: int, agent_code: str) -> dict[str, Any]:
    started = time.perf_counter()
    args = dict(case.get("arguments") or {})
    status = "completed"
    error_message: str | None = None
    output: dict[str, Any] = {}
    try:
        collection = str(args.get("collection") or "default")
        question = str(args.get("question") or "")
        limit = max(1, min(20, int(args.get("limit") or 5)))
        results = rag_store.query(collection, question, limit, actor_id=str(args.get("actor_id") or agent_code or "benchmark_runner"))
        expected_paths = [str(item).lower() for item in args.get("expected_paths", [])]
        expected_chunk_ids = [str(item) for item in args.get("expected_chunk_ids", [])]
        expected_keywords = [str(item).lower() for item in args.get("expected_keywords", [])]
        path_hit = bool(expected_paths) and any(expected in str(item.get("path") or "").lower() for expected in expected_paths for item in results)
        keyword_hits = sum(
            1
            for keyword in expected_keywords
            if any(keyword and keyword in str(item.get("content") or "").lower() for item in results)
        )
        hit = path_hit or (bool(expected_keywords) and keyword_hits > 0) or (not expected_paths and not expected_keywords and bool(results))
        source_quality = _rag_source_quality(results, keyword_hits, len(expected_keywords))
        retrieved_chunk_ids = [str(item.get("chunk_id") or "") for item in results]
        relevant_hits = [chunk_id for chunk_id in expected_chunk_ids if chunk_id in retrieved_chunk_ids]
        recall_at_k = len(relevant_hits) / len(expected_chunk_ids) if expected_chunk_ids else None
        precision_at_k = len(relevant_hits) / len(retrieved_chunk_ids) if expected_chunk_ids and retrieved_chunk_ids else None
        first_rank = next((index + 1 for index, chunk_id in enumerate(retrieved_chunk_ids) if chunk_id in expected_chunk_ids), None)
        mrr = 1 / first_rank if first_rank else 0.0
        chunk_hit = bool(relevant_hits) if expected_chunk_ids else False
        hit = hit or chunk_hit
        output = {
            "collection": collection,
            "question": question,
            "result_count": len(results),
            "hit": hit,
            "path_hit": path_hit,
            "keyword_hits": keyword_hits,
            "expected_keyword_count": len(expected_keywords),
            "source_quality": source_quality,
            "expected_chunk_ids": expected_chunk_ids,
            "retrieved_chunk_ids": retrieved_chunk_ids,
            "chunk_hit": chunk_hit,
            "recall_at_k": recall_at_k,
            "precision_at_k": precision_at_k,
            "mrr": mrr,
            "results": results,
        }
        if not hit:
            status = "failed"
            error_message = "No expected path or keyword was retrieved"
    except Exception as exc:
        status = "failed"
        error_message = str(exc)
    return _append_case_result(run_id, case, iteration, status, started, args, output, error_message, tool_name="rag.query")


def _run_single_workflow_case(run_id: str, case: dict[str, Any], iteration: int, agent_code: str) -> dict[str, Any]:
    started = time.perf_counter()
    args = dict(case.get("arguments") or {})
    status = "completed"
    error_message: str | None = None
    output: dict[str, Any] = {}
    try:
        result = run_compiled_workflow(
            str(args.get("workflow_name") or case.get("case_id") or "benchmark_workflow"),
            str(args.get("input_text") or args.get("goal") or ""),
            list(args.get("nodes") or []),
            list(args.get("edges") or []),
            dict(args.get("extra_state") or {}),
        )
        events = result.get("events") if isinstance(result.get("events"), list) else []
        failed_nodes = _failed_nodes(events)
        completed_nodes = _completed_nodes(events)
        expected_nodes = [str(item) for item in args.get("expected_nodes", [])]
        expected_completed = all(node in completed_nodes for node in expected_nodes) if expected_nodes else True
        validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
        success = not failed_nodes and expected_completed and bool(validation.get("valid", True))
        output = {
            "workflow_name": result.get("workflow_name"),
            "event_count": len(events),
            "completed_nodes": completed_nodes,
            "failed_nodes": failed_nodes,
            "expected_completed": expected_completed,
            "success": success,
            "validation": validation,
            "final_report_preview": str(result.get("final_report") or "")[:1200],
        }
        if not success:
            status = "failed"
            error_message = f"Workflow failed nodes: {', '.join(failed_nodes) or 'none'}"
    except Exception as exc:
        status = "failed"
        error_message = str(exc)
    return _append_case_result(run_id, case, iteration, status, started, args, output, error_message, tool_name="workflow.run")


def _run_single_collaboration_case(run_id: str, case: dict[str, Any], iteration: int, agent_code: str) -> dict[str, Any]:
    started = time.perf_counter()
    args = dict(case.get("arguments") or {})
    status = "completed"
    error_message: str | None = None
    output: dict[str, Any] = {}
    try:
        result = run_collaboration_task(
            {
                "goal": str(args.get("goal") or ""),
                "project_path": args.get("project_path"),
                "max_files": int(args.get("max_files") or 80),
                "require_human_review": bool(args.get("require_human_review", True)),
                "input_text": str(args.get("goal") or ""),
            }
        )
        public = result.get("result") if isinstance(result.get("result"), dict) else {}
        report = str(public.get("final_report") or result.get("final_report") or "")
        expected_sections = [str(item).lower() for item in args.get("expected_sections", [])]
        risk_keywords = [str(item).lower() for item in args.get("expected_risk_keywords", [])]
        section_hits = sum(1 for section in expected_sections if section and section in report.lower())
        risk_hits = sum(1 for keyword in risk_keywords if keyword and keyword in report.lower())
        completeness_score = _collaboration_completeness(report, section_hits, len(expected_sections), public)
        human_review_triggered = bool(public.get("human_review_required") or public.get("human_review_packet"))
        risk_detection_score = _rate(risk_hits, len(risk_keywords)) if risk_keywords else (1.0 if public.get("risk_level") else 0.0)
        success = completeness_score >= 60 and (risk_detection_score > 0 or not risk_keywords)
        execution_status = public.get("execution_status", "completed")
        evaluation_target = "report"
        if execution_status == "waiting_review":
            evaluation_target = "review_gate"
            success = bool(public.get("resume_checkpoint") and public.get("human_review_packet")) and not report
        elif execution_status != "completed":
            success = False
        output = {
            "execution_status": execution_status,
            "evaluation_target": evaluation_target,
            "report_evaluated": bool(report),
            "completeness_score": completeness_score,
            "section_hits": section_hits,
            "expected_section_count": len(expected_sections),
            "risk_keyword_hits": risk_hits,
            "expected_risk_keyword_count": len(risk_keywords),
            "risk_detection_score": risk_detection_score,
            "human_review_triggered": human_review_triggered,
            "risk_level": public.get("risk_level"),
            "event_count": len(result.get("events") or []),
            "final_report_preview": report[:1800],
        }
        if not success:
            status = "failed"
            error_message = "Collaboration report did not meet completeness or risk coverage threshold"
    except Exception as exc:
        status = "failed"
        error_message = str(exc)
    return _append_case_result(run_id, case, iteration, status, started, args, output, error_message, tool_name="collaboration.run")


def _append_case_result(
    run_id: str,
    case: dict[str, Any],
    iteration: int,
    status: str,
    started: float,
    input_payload: dict[str, Any],
    output: dict[str, Any],
    error_message: str | None,
    *,
    tool_name: str,
) -> dict[str, Any]:
    result = {
        "run_id": run_id,
        "case_id": str(case.get("case_id") or tool_name or "case"),
        "server_id": case.get("server_id"),
        "tool_name": case.get("tool_name") or tool_name,
        "iteration": iteration,
        "status": status,
        "latency_ms": max(0, int((time.perf_counter() - started) * 1000)),
        "error_message": error_message,
        "input": input_payload,
        "output": _compact_json(output),
        "created_at": utc_now_iso(),
    }
    return task_store.append_benchmark_result(result)


def _summarize_benchmark(results: list[dict[str, Any]], cases: list[dict[str, Any]], iterations: int) -> dict[str, Any]:
    total = len(results)
    completed = sum(1 for item in results if item.get("status") == "completed")
    failed = total - completed
    latencies = sorted(int(item.get("latency_ms") or 0) for item in results)
    by_case: list[dict[str, Any]] = []
    for case in cases:
        case_id = str(case.get("case_id") or case.get("tool_name") or "case")
        case_results = [item for item in results if item.get("case_id") == case_id]
        case_latencies = sorted(int(item.get("latency_ms") or 0) for item in case_results)
        case_total = len(case_results)
        case_failed = sum(1 for item in case_results if item.get("status") != "completed")
        by_case.append(
            {
                "case_id": case_id,
                "server_id": case.get("server_id"),
                "tool_name": case.get("tool_name"),
                "total": case_total,
                "completed": case_total - case_failed,
                "failed": case_failed,
                "success_rate": _rate(case_total - case_failed, case_total),
                "avg_latency_ms": _avg(case_latencies),
                "p95_latency_ms": _p95(case_latencies),
                "min_latency_ms": case_latencies[0] if case_latencies else 0,
                "max_latency_ms": case_latencies[-1] if case_latencies else 0,
            }
        )
    return {
        "total": total,
        "completed": completed,
        "failed": failed,
        "success_rate": _rate(completed, total),
        "avg_latency_ms": _avg(latencies),
        "p95_latency_ms": _p95(latencies),
        "min_latency_ms": latencies[0] if latencies else 0,
        "max_latency_ms": latencies[-1] if latencies else 0,
        "iterations": iterations,
        "case_count": len(cases),
        "by_case": by_case,
    }


def _summarize_llm_benchmark(results: list[dict[str, Any]], cases: list[dict[str, Any]], iterations: int) -> dict[str, Any]:
    summary = _summarize_benchmark(results, cases, iterations)
    outputs = [item.get("output") if isinstance(item.get("output"), dict) else {} for item in results]
    quality_scores = [int(output.get("quality_score") or 0) for output in outputs]
    total_tokens = sum(int(output.get("total_tokens") or 0) for output in outputs)
    fallback_calls = sum(1 for output in outputs if output.get("fallback_used"))
    total_cost = sum(float(output.get("estimated_cost_usd") or 0.0) for output in outputs)
    summary.update(
        {
            "avg_quality_score": _avg(quality_scores),
            "fallback_calls": fallback_calls,
            "fallback_rate": _rate(fallback_calls, len(results)),
            "total_tokens": total_tokens,
            "estimated_cost_usd": round(total_cost, 8),
            "benchmark_focus": "prompt/model quality, token usage, cost, fallback",
        }
    )
    return summary


def _summarize_rag_benchmark(results: list[dict[str, Any]], cases: list[dict[str, Any]], iterations: int) -> dict[str, Any]:
    summary = _summarize_benchmark(results, cases, iterations)
    outputs = [item.get("output") if isinstance(item.get("output"), dict) else {} for item in results]
    hits = sum(1 for output in outputs if output.get("hit"))
    source_scores = [int(output.get("source_quality") or 0) for output in outputs]
    result_counts = [int(output.get("result_count") or 0) for output in outputs]
    chunk_outputs = [output for output in outputs if output.get("expected_chunk_ids")]
    chunk_hits = sum(1 for output in chunk_outputs if output.get("chunk_hit"))
    recalls = [float(output["recall_at_k"]) for output in chunk_outputs if output.get("recall_at_k") is not None]
    precisions = [float(output["precision_at_k"]) for output in chunk_outputs if output.get("precision_at_k") is not None]
    mrrs = [float(output.get("mrr") or 0) for output in chunk_outputs]
    summary.update(
        {
            "hit_count": hits,
            "hit_rate": _rate(hits, len(results)),
            "avg_source_quality": _avg(source_scores),
            "avg_result_count": _avg(result_counts),
            "chunk_gold_case_count": len(chunk_outputs),
            "chunk_hit_rate": _rate(chunk_hits, len(chunk_outputs)),
            "recall_at_k": round(sum(recalls) / len(recalls), 4) if recalls else None,
            "precision_at_k": round(sum(precisions) / len(precisions), 4) if precisions else None,
            "mrr": round(sum(mrrs) / len(mrrs), 4) if mrrs else None,
            "benchmark_focus": "retrieval hit rate, source quality, chunk Gold Set recall/precision/MRR",
        }
    )
    return summary


def _summarize_workflow_benchmark(results: list[dict[str, Any]], cases: list[dict[str, Any]], iterations: int) -> dict[str, Any]:
    summary = _summarize_benchmark(results, cases, iterations)
    outputs = [item.get("output") if isinstance(item.get("output"), dict) else {} for item in results]
    successful = sum(1 for output in outputs if output.get("success"))
    failed_nodes: list[str] = []
    completed_counts: list[int] = []
    for output in outputs:
        failed_nodes.extend(str(item) for item in output.get("failed_nodes", []))
        completed_counts.append(len(output.get("completed_nodes", [])))
    summary.update(
        {
            "workflow_success_count": successful,
            "workflow_success_rate": _rate(successful, len(results)),
            "failed_node_count": len(failed_nodes),
            "failed_nodes": sorted(set(failed_nodes)),
            "avg_completed_nodes": _avg(completed_counts),
            "benchmark_focus": "workflow success rate, latency, failed nodes",
        }
    )
    return summary


def _summarize_collaboration_benchmark(results: list[dict[str, Any]], cases: list[dict[str, Any]], iterations: int) -> dict[str, Any]:
    summary = _summarize_benchmark(results, cases, iterations)
    outputs = [item.get("output") if isinstance(item.get("output"), dict) else {} for item in results]
    report_outputs = [output for output in outputs if output.get("report_evaluated", True)]
    completeness_scores = [int(output.get("completeness_score") or 0) for output in report_outputs]
    risk_scores = [float(output.get("risk_detection_score") or 0.0) for output in report_outputs]
    review_triggers = sum(1 for output in outputs if output.get("human_review_triggered"))
    summary.update(
        {
            "report_evaluated_count": len(report_outputs),
            "review_gate_case_count": sum(output.get("evaluation_target") == "review_gate" for output in outputs),
            "avg_completeness_score": _avg(completeness_scores),
            "avg_risk_detection_score": round(sum(risk_scores) / len(risk_scores), 4) if risk_scores else 0.0,
            "human_review_trigger_count": review_triggers,
            "human_review_trigger_rate": _rate(review_triggers, len(results)),
            "benchmark_focus": "collaboration report completeness, risk coverage, human review trigger",
        }
    )
    return summary


def _avg(values: list[int]) -> int:
    return int(sum(values) / len(values)) if values else 0


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    index = max(0, min(len(values) - 1, math.ceil(len(values) * 0.95) - 1))
    return values[index]


def _rate(numerator: int, denominator: int) -> float:
    return round(float(numerator) / float(denominator), 4) if denominator else 0.0


def _compact_json(value: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= 12000:
        return value
    return {"truncated": True, "preview": text[:12000]}


def _token_counts(usage: dict[str, Any], text: str) -> tuple[int, int, int]:
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or usage.get("input_token_count") or 0)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or usage.get("output_token_count") or 0)
    total_tokens = int(usage.get("total_tokens") or usage.get("total_token_count") or input_tokens + output_tokens)
    if total_tokens <= 0:
        output_tokens = max(1, len(text) // 4) if text else 0
        input_tokens = max(1, len(text) // 8) if text else 0
        total_tokens = input_tokens + output_tokens
    return input_tokens, output_tokens, total_tokens


def _quality_score(text: str, fallback_used: bool, expected_keywords: list[str], keyword_hits: int, total_tokens: int, latency_ms: int) -> int:
    if fallback_used:
        return 35
    stripped = text.strip()
    if not stripped:
        return 20
    score = 50
    if len(stripped) >= 120:
        score += 12
    if any(marker in stripped for marker in ["1.", "2.", "-", ":", "建议", "risk", "next"]):
        score += 10
    if expected_keywords:
        score += int(20 * _rate(keyword_hits, len(expected_keywords)))
    if 120 <= total_tokens <= 1200:
        score += 6
    if latency_ms and latency_ms < 15000:
        score += 4
    return max(0, min(100, score))


def _estimate_llm_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = llm_provider.usage_dashboard(limit=1).get("pricing", {})
    model_pricing = pricing.get(model) or {}
    input_price = float(model_pricing.get("input_per_1m") or 0.0)
    output_price = float(model_pricing.get("output_per_1m") or 0.0)
    return round((input_tokens * input_price + output_tokens * output_price) / 1_000_000, 8)


def _rag_source_quality(results: list[dict[str, Any]], keyword_hits: int, expected_keyword_count: int) -> int:
    if not results:
        return 0
    score = min(45, len(results) * 9)
    if expected_keyword_count:
        score += int(45 * _rate(keyword_hits, expected_keyword_count))
    top_score = results[0].get("score") if isinstance(results[0], dict) else 0
    if isinstance(top_score, (int, float)) and top_score > 0:
        score += 10
    return max(0, min(100, score))


def _rag_gold_cases_for_benchmark(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not hasattr(rag_store, "list_gold_cases"):
        return []
    collection = str(payload.get("collection") or "").strip() or None
    gold_cases = rag_store.list_gold_cases(collection, include_disabled=False)
    return [
        {
            "case_id": case["case_id"],
            "tool_name": "rag.query",
            "arguments": {
                "collection": case["collection"],
                "question": case["question"],
                "expected_chunk_ids": case.get("expected_chunk_ids", []),
                "expected_paths": case.get("expected_paths", []),
                "expected_keywords": case.get("expected_keywords", []),
                "limit": int(case.get("metadata", {}).get("limit") or 5) if isinstance(case.get("metadata"), dict) else 5,
            },
            "enabled": bool(case.get("enabled", True)),
        }
        for case in gold_cases
    ]


def _failed_nodes(events: list[dict[str, Any]]) -> list[str]:
    return sorted({str(event.get("data", {}).get("node_id") or event.get("node") or "unknown") for event in events if event.get("status") == "failed"})


def _completed_nodes(events: list[dict[str, Any]]) -> list[str]:
    return sorted({str(event.get("data", {}).get("node_id") or event.get("node") or "unknown") for event in events if event.get("status") == "completed"})


def _collaboration_completeness(report: str, section_hits: int, expected_section_count: int, public_result: dict[str, Any]) -> int:
    score = 35 if len(report.strip()) >= 400 else 20 if report.strip() else 0
    if expected_section_count:
        score += int(35 * _rate(section_hits, expected_section_count))
    if public_result.get("agent_outputs"):
        score += 10
    if public_result.get("supervisor_notes") or public_result.get("suggestions"):
        score += 10
    if public_result.get("governance") or public_result.get("risk_level"):
        score += 10
    return max(0, min(100, score))
