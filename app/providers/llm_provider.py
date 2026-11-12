from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.harness.events import utc_now_iso
from app.persistence.sqlite_store import task_store


class LLMProvider:
    def __init__(self):
        self.project_root = Path(__file__).resolve().parents[2]
        self.env_path = Path(os.getenv("APP_ENV_FILE", str(self.project_root / ".env")))
        self.default_model = "gpt-4o-mini"
        self.known_agents = [
            "planner",
            "reporter",
            "supervisor",
            "project_analyzer",
            "code_reviewer",
            "file_reviewer",
            "task_qa",
            "learning_coach",
            "memory_extractor",
        ]
        self.prompt_versions = [
            {
                "agent": "planner",
                "prompt_family": "planner",
                "prompt_version": "planner.v1",
                "title": "Baseline planner",
                "description": "Default task decomposition prompt.",
                "system_suffix": "",
                "is_active": True,
            },
            {
                "agent": "planner",
                "prompt_family": "planner",
                "prompt_version": "planner.v2",
                "title": "Governance workflow planner",
                "description": "Adds risk gates, artifacts, and human review hints to planning.",
                "system_suffix": "请额外标注风险门禁、产物沉淀点、是否需要人工审核，以及适合转成 Workflow 节点的步骤。",
                "is_active": False,
            },
            {
                "agent": "reporter",
                "prompt_family": "reporter",
                "prompt_version": "reporter.v1",
                "title": "Baseline reporter",
                "description": "Default final report generation prompt.",
                "system_suffix": "",
                "is_active": True,
            },
            {
                "agent": "reporter",
                "prompt_family": "reporter",
                "prompt_version": "reporter.v2",
                "title": "Governance reporter",
                "description": "Emphasizes risk level, evidence, next actions, and ownership.",
                "system_suffix": "请把结论组织为：关键证据、风险等级、责任归属、下一步动作、可沉淀知识。不要只写泛泛建议。",
                "is_active": False,
            },
            {
                "agent": "supervisor",
                "prompt_family": "supervisor",
                "prompt_version": "supervisor.v1",
                "title": "Baseline supervisor",
                "description": "Default multi-agent quality gate prompt.",
                "system_suffix": "",
                "is_active": True,
            },
            {
                "agent": "supervisor",
                "prompt_family": "supervisor",
                "prompt_version": "supervisor.v2",
                "title": "Strict risk gate",
                "description": "Makes review-required and blocking risk judgement more explicit.",
                "system_suffix": "请优先判断是否存在阻断风险、是否必须人工审核、还缺少哪个 Agent 的证据，并给出短句结论。",
                "is_active": False,
            },
            {
                "agent": "project_analyzer",
                "prompt_family": "project_analyzer.architecture",
                "prompt_version": "project_analyzer.architecture.v1",
                "title": "Architecture baseline",
                "description": "Default project architecture interpretation prompt.",
                "system_suffix": "",
                "is_active": True,
            },
            {
                "agent": "project_analyzer",
                "prompt_family": "project_analyzer.architecture",
                "prompt_version": "project_analyzer.architecture.v2",
                "title": "Architecture mentor",
                "description": "Adds onboarding path and module ownership reading order.",
                "system_suffix": "请额外输出新成员阅读顺序、模块职责边界、最可能误解的点，以及治理建议的证据来源。",
                "is_active": False,
            },
            {
                "agent": "code_reviewer",
                "prompt_family": "code_reviewer.suggestions",
                "prompt_version": "code_reviewer.suggestions.v1",
                "title": "Review suggestions baseline",
                "description": "Default code review remediation suggestions prompt.",
                "system_suffix": "",
                "is_active": True,
            },
            {
                "agent": "code_reviewer",
                "prompt_family": "code_reviewer.suggestions",
                "prompt_version": "code_reviewer.suggestions.v2",
                "title": "Test-bound suggestions",
                "description": "Binds remediation suggestions to tests and governance actions.",
                "system_suffix": "每条建议都要尽量绑定具体 finding、推荐测试用例、优先级和人工审核条件。",
                "is_active": False,
            },
            {
                "agent": "file_reviewer",
                "prompt_family": "file_reviewer.semantic",
                "prompt_version": "file_reviewer.semantic.v1",
                "title": "Semantic file review baseline",
                "description": "Default hybrid semantic file review prompt.",
                "system_suffix": "",
                "is_active": True,
            },
            {
                "agent": "file_reviewer",
                "prompt_family": "file_reviewer.semantic",
                "prompt_version": "file_reviewer.semantic.v2",
                "title": "Call-chain risk review",
                "description": "Emphasizes call-chain impact, testability, and dependency risk.",
                "system_suffix": "请优先分析调用链影响、隐含依赖、可测试性缺口、失败传播路径和治理优先级。",
                "is_active": False,
            },
            {
                "agent": "task_qa",
                "prompt_family": "task_qa",
                "prompt_version": "task_qa.v1",
                "title": "Task Q&A baseline",
                "description": "Default task context Q&A prompt.",
                "system_suffix": "",
                "is_active": True,
            },
            {
                "agent": "task_qa",
                "prompt_family": "task_qa",
                "prompt_version": "task_qa.v2",
                "title": "Evidence-first Q&A",
                "description": "Answers with explicit source and uncertainty handling.",
                "system_suffix": "回答时请先说明依据来自报告、事件、知识库还是 fallback；信息不足时明确缺口。",
                "is_active": False,
            },
            {
                "agent": "learning_coach",
                "prompt_family": "learning_coach.reply",
                "prompt_version": "learning_coach.reply.v1",
                "title": "Learning reply baseline",
                "description": "Default learning coach reply prompt.",
                "system_suffix": "",
                "is_active": True,
            },
            {
                "agent": "learning_coach",
                "prompt_family": "learning_coach.reply",
                "prompt_version": "learning_coach.reply.v2",
                "title": "Socratic learning reply",
                "description": "Makes the coach ask more adaptive follow-up questions.",
                "system_suffix": "请用苏格拉底式追问推进理解，避免直接给标准答案；每次只推进一个认知台阶。",
                "is_active": False,
            },
            {
                "agent": "learning_coach",
                "prompt_family": "learning_coach.questions",
                "prompt_version": "learning_coach.questions.v1",
                "title": "Learning questions baseline",
                "description": "Default next-question generation prompt.",
                "system_suffix": "",
                "is_active": True,
            },
            {
                "agent": "learning_coach",
                "prompt_family": "learning_coach.questions",
                "prompt_version": "learning_coach.questions.v2",
                "title": "Stage-aware questions",
                "description": "Generates questions tied to the current learning plan stage.",
                "system_suffix": "请根据当前 day/theme 生成递进问题，问题之间要体现从事实、机制到迁移应用的层次。",
                "is_active": False,
            },
            {
                "agent": "memory_extractor",
                "prompt_family": "memory_extractor",
                "prompt_version": "memory_extractor.v1",
                "title": "Governed long-term memory extraction",
                "description": "Extracts only durable, confirmation-required memory candidates from user messages.",
                "system_suffix": "Only emit concise JSON candidates. Never extract secrets, one-off questions, or instructions that do not represent durable memory.",
                "is_active": True,
            },
        ]

    @property
    def model(self) -> str:
        return self._config()["model"]

    @property
    def enabled(self) -> bool:
        return bool(self._config()["api_key"])

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        fallback: str,
        *,
        agent: str = "unknown",
        prompt_version: str = "v1",
    ) -> str:
        return self.generate_with_status(
            system_prompt,
            user_prompt,
            fallback,
            agent=agent,
            prompt_version=prompt_version,
        )["text"]

    def generate_with_status(
        self,
        system_prompt: str,
        user_prompt: str,
        fallback: str,
        *,
        agent: str = "unknown",
        prompt_version: str = "v1",
        use_active_prompt: bool = True,
        model_override: str | None = None,
    ) -> dict[str, Any]:
        system_prompt, prompt_version = self._resolve_prompt(agent, prompt_version, system_prompt, use_active_prompt=use_active_prompt)
        config = self._config(agent)
        if model_override:
            config = {**config, "model": model_override}
        trace_id = f"llm_{uuid4().hex}"
        started = time.perf_counter()
        trace_input = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "fallback": fallback,
        }

        if not config["api_key"]:
            latency_ms = self._elapsed_ms(started)
            self._save_trace(
                trace_id=trace_id,
                agent=agent,
                prompt_version=prompt_version,
                model=None,
                input_payload=trace_input,
                output_text=fallback,
                fallback_used=True,
                error_message="OPENAI_API_KEY is not configured",
                latency_ms=latency_ms,
                token_usage={},
            )
            return {
                "text": fallback,
                "answer_source": "fallback",
                "fallback_used": True,
                "model": None,
                "trace_id": trace_id,
                "latency_ms": latency_ms,
            }

        try:
            from langchain_openai import ChatOpenAI

            llm = self._build_chat_openai(ChatOpenAI, config)
            response = llm.invoke(
                [
                    ("system", system_prompt),
                    ("user", user_prompt),
                ]
            )
            output_text = str(response.content)
            latency_ms = self._elapsed_ms(started)
            token_usage = self._extract_token_usage(response)
            self._save_trace(
                trace_id=trace_id,
                agent=agent,
                prompt_version=prompt_version,
                model=config["model"],
                input_payload=trace_input,
                output_text=output_text,
                fallback_used=False,
                error_message=None,
                latency_ms=latency_ms,
                token_usage=token_usage,
            )
            return {
                "text": output_text,
                "answer_source": "llm",
                "fallback_used": False,
                "model": config["model"],
                "trace_id": trace_id,
                "latency_ms": latency_ms,
                "token_usage": token_usage,
            }
        except Exception as exc:
            latency_ms = self._elapsed_ms(started)
            self._save_trace(
                trace_id=trace_id,
                agent=agent,
                prompt_version=prompt_version,
                model=config["model"],
                input_payload=trace_input,
                output_text=fallback,
                fallback_used=True,
                error_message=str(exc),
                latency_ms=latency_ms,
                token_usage={},
            )
            return {
                "text": fallback,
                "answer_source": "fallback",
                "fallback_used": True,
                "model": config["model"],
                "trace_id": trace_id,
                "latency_ms": latency_ms,
                "error_message": str(exc),
            }

    def plan_steps(self, goal: str, context: dict[str, Any], fallback_steps: list[str]) -> list[str]:
        fallback = "\n".join(f"- {item}" for item in fallback_steps)
        text = self.generate(
            "你是 DevAgent Studio 的任务规划器。请把用户目标拆成清晰、可执行、短句化的步骤。",
            f"目标：{goal}\n上下文：{context}",
            fallback,
            agent="planner",
            prompt_version="planner.v1",
        )
        steps = [line.strip("- ").strip() for line in text.splitlines() if line.strip()]
        return steps or fallback_steps

    def write_report(self, goal: str, facts: dict[str, Any], fallback: str) -> str:
        return self.generate(
            "你是 DevAgent Studio 的报告生成器。请基于事实生成结构清晰、可行动的中文 Markdown 报告，不要编造事实。",
            f"目标：{goal}\n事实：{facts}",
            fallback,
            agent="reporter",
            prompt_version="reporter.v1",
        )

    def status(self) -> dict[str, Any]:
        config = self._config()
        return {
            "enabled": bool(config["api_key"]),
            "model": config["model"],
            "base_url": config["base_url"] or None,
            "env_path": str(self.env_path),
            "source": config["source"],
            "agent_models": {agent: self._config(agent)["model"] for agent in self.known_agents},
            "active_prompts": self.active_prompt_map(),
        }

    def list_prompt_versions(self, agent: str | None = None) -> list[dict[str, Any]]:
        self._ensure_prompt_versions()
        return task_store.list_prompt_versions(agent)

    def set_active_prompt_version(self, agent: str, prompt_version: str) -> dict[str, Any] | None:
        self._ensure_prompt_versions()
        return task_store.set_active_prompt_version(agent, prompt_version)

    def save_prompt_version(self, prompt: dict[str, Any]) -> dict[str, Any]:
        self._ensure_prompt_versions()
        return task_store.upsert_prompt_version(prompt)

    def run_prompt_ab_test(
        self,
        *,
        agent: str,
        prompt_a: str,
        prompt_b: str,
        system_prompt: str,
        user_prompt: str,
        fallback: str,
    ) -> dict[str, Any]:
        self._ensure_prompt_versions()
        result_a = self.generate_with_status(
            system_prompt,
            user_prompt,
            fallback,
            agent=agent,
            prompt_version=prompt_a,
            use_active_prompt=False,
        )
        result_b = self.generate_with_status(
            system_prompt,
            user_prompt,
            fallback,
            agent=agent,
            prompt_version=prompt_b,
            use_active_prompt=False,
        )
        comparison = {
            "winner": self._pick_ab_winner(result_a, result_b),
            "criteria": [
                "fallback 优先级最低",
                "结构化程度越高越好",
                "回答越具体、长度适中越好",
                "token 与 latency 越低越好",
            ],
        }
        return {
            "agent": agent,
            "prompt_a": self._ab_result(prompt_a, result_a),
            "prompt_b": self._ab_result(prompt_b, result_b),
            "comparison": comparison,
        }

    def active_prompt_map(self) -> dict[str, str]:
        self._ensure_prompt_versions()
        active: dict[str, str] = {}
        for item in task_store.list_prompt_versions():
            if item.get("is_active"):
                key = f"{item.get('agent')}:{item.get('prompt_family')}"
                active[key] = str(item.get("prompt_version"))
        return active

    def usage_dashboard(self, limit: int = 500, agent: str | None = None) -> dict[str, Any]:
        traces = task_store.list_llm_traces(limit=limit, agent=agent)
        summary = self._aggregate_usage(traces)
        pricing = self._model_pricing()
        self._apply_costs(summary, pricing)
        return {
            **summary,
            "pricing": pricing,
            "sample_size": len(traces),
            "currency": "USD",
            "cost_basis": "estimated_from_configured_price_per_1m_tokens",
        }

    def _config(self, agent: str | None = None) -> dict[str, str]:
        env_file = self._read_env_file()
        api_key_from_process = os.getenv("OPENAI_API_KEY", "")
        base_url_from_process = os.getenv("OPENAI_BASE_URL", "")
        default_model = self._read_env_value("DEV_AGENT_LLM_MODEL", env_file, self.default_model)
        agent_model = self._agent_model(agent or "", env_file, default_model)
        return {
            "api_key": api_key_from_process or env_file.get("OPENAI_API_KEY", ""),
            "model": agent_model,
            "base_url": base_url_from_process or env_file.get("OPENAI_BASE_URL", ""),
            "source": "process_env" if api_key_from_process else ".env" if env_file.get("OPENAI_API_KEY") else "fallback",
        }

    def _read_env_file(self) -> dict[str, str]:
        if not self.env_path.exists():
            return {}
        values: dict[str, str] = {}
        for raw_line in self.env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                values[key] = value
        return values

    def _read_env_value(self, key: str, env_file: dict[str, str], default: str = "") -> str:
        return os.getenv(key, "") or env_file.get(key, default)

    def _agent_model(self, agent: str, env_file: dict[str, str], default_model: str) -> str:
        if not agent:
            return default_model
        direct_key = f"DEV_AGENT_LLM_MODEL_{self._agent_env_key(agent)}"
        direct_value = self._read_env_value(direct_key, env_file)
        if direct_value:
            return direct_value
        model_map = self._read_env_value("DEV_AGENT_LLM_AGENT_MODELS", env_file)
        for item in model_map.split(","):
            if ":" not in item:
                continue
            name, model = item.split(":", 1)
            if name.strip().lower() == agent.strip().lower() and model.strip():
                return model.strip()
        return default_model

    def _agent_env_key(self, agent: str) -> str:
        return "".join(ch if ch.isalnum() else "_" for ch in agent.upper()).strip("_")

    def _prompt_family(self, prompt_version: str) -> str:
        parts = prompt_version.split(".")
        if len(parts) > 1 and parts[-1].startswith("v") and parts[-1][1:].isdigit():
            return ".".join(parts[:-1])
        return prompt_version

    def _resolve_prompt(self, agent: str, prompt_version: str, system_prompt: str, *, use_active_prompt: bool = True) -> tuple[str, str]:
        self._ensure_prompt_versions()
        prompt_family = self._prompt_family(prompt_version)
        env_file = self._read_env_file()
        env_key = f"DEV_AGENT_PROMPT_{self._agent_env_key(prompt_family)}"
        configured_version = self._read_env_value(env_key, env_file)
        active = None
        if use_active_prompt and configured_version:
            active = task_store.get_prompt_version(agent, configured_version)
        if use_active_prompt and not active:
            active = task_store.get_active_prompt_version(agent, prompt_family)
        if not use_active_prompt:
            active = task_store.get_prompt_version(agent, prompt_version)
        if not active:
            return system_prompt, prompt_version
        suffix = str(active.get("system_suffix") or "").strip()
        resolved_version = str(active.get("prompt_version") or prompt_version)
        if suffix:
            system_prompt = f"{system_prompt}\n\nPrompt version instruction ({resolved_version}):\n{suffix}"
        return system_prompt, resolved_version

    def _ensure_prompt_versions(self) -> None:
        existing = {
            (item.get("agent"), item.get("prompt_version"))
            for item in task_store.list_prompt_versions()
        }
        for prompt in self.prompt_versions:
            key = (prompt["agent"], prompt["prompt_version"])
            if key not in existing:
                task_store.upsert_prompt_version(prompt)

    def _ab_result(self, prompt_version: str, result: dict[str, Any]) -> dict[str, Any]:
        token_usage = result.get("token_usage") if isinstance(result.get("token_usage"), dict) else {}
        input_tokens, output_tokens, total_tokens = self._token_counts(token_usage)
        if total_tokens <= 0:
            text = str(result.get("text") or "")
            input_tokens = max(1, len(text) // 8) if text else 0
            output_tokens = max(1, len(text) // 4) if text else 0
            total_tokens = input_tokens + output_tokens
        return {
            "prompt_version": prompt_version,
            "text": result.get("text") or "",
            "answer_source": result.get("answer_source") or "unknown",
            "fallback_used": bool(result.get("fallback_used")),
            "model": result.get("model"),
            "trace_id": result.get("trace_id"),
            "latency_ms": int(result.get("latency_ms") or 0),
            "token_usage": token_usage,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "quality_score": self._prompt_quality_score(str(result.get("text") or ""), bool(result.get("fallback_used")), total_tokens, int(result.get("latency_ms") or 0)),
            "error_message": result.get("error_message"),
        }

    def _pick_ab_winner(self, result_a: dict[str, Any], result_b: dict[str, Any]) -> str:
        a = self._ab_result("A", result_a)
        b = self._ab_result("B", result_b)
        if a["quality_score"] == b["quality_score"]:
            if a["total_tokens"] == b["total_tokens"]:
                return "tie"
            return "A" if a["total_tokens"] < b["total_tokens"] else "B"
        return "A" if a["quality_score"] > b["quality_score"] else "B"

    def _prompt_quality_score(self, text: str, fallback_used: bool, total_tokens: int, latency_ms: int) -> int:
        if fallback_used:
            return 35
        stripped = text.strip()
        if not stripped:
            return 20
        score = 52
        if len(stripped) >= 120:
            score += 12
        if any(marker in stripped for marker in ["1.", "2.", "-", "•", "：", ":"]):
            score += 10
        if any(word in stripped for word in ["风险", "建议", "步骤", "依据", "测试", "治理", "下一步"]):
            score += 12
        if 120 <= total_tokens <= 1200:
            score += 8
        if latency_ms and latency_ms < 15000:
            score += 6
        return max(0, min(100, score))

    def _model_pricing(self) -> dict[str, dict[str, float]]:
        env_file = self._read_env_file()
        pricing: dict[str, dict[str, float]] = {}
        models = {self.default_model, self._config()["model"]}
        for agent in self.known_agents:
            models.add(self._config(agent)["model"])
        for model in models:
            key = self._agent_env_key(model)
            input_price = self._read_env_value(f"DEV_AGENT_LLM_PRICE_{key}_INPUT_PER_1M", env_file)
            output_price = self._read_env_value(f"DEV_AGENT_LLM_PRICE_{key}_OUTPUT_PER_1M", env_file)
            pricing[model] = {
                "input_per_1m": self._float_or_zero(input_price),
                "output_per_1m": self._float_or_zero(output_price),
            }
        return pricing

    def _float_or_zero(self, value: str) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _build_chat_openai(self, chat_openai: Any, config: dict[str, str]) -> Any:
        kwargs: dict[str, Any] = {
            "model": config["model"],
            "temperature": 0.2,
            "api_key": config["api_key"],
        }
        if config["base_url"]:
            kwargs["base_url"] = config["base_url"]
        try:
            return chat_openai(**kwargs)
        except TypeError:
            legacy_kwargs = dict(kwargs)
            legacy_kwargs["openai_api_key"] = legacy_kwargs.pop("api_key")
            if "base_url" in legacy_kwargs:
                legacy_kwargs["openai_api_base"] = legacy_kwargs.pop("base_url")
            return chat_openai(**legacy_kwargs)

    def _extract_token_usage(self, response: Any) -> dict[str, Any]:
        usage_metadata = getattr(response, "usage_metadata", None)
        if isinstance(usage_metadata, dict):
            return usage_metadata
        response_metadata = getattr(response, "response_metadata", None)
        if isinstance(response_metadata, dict):
            token_usage = response_metadata.get("token_usage") or response_metadata.get("usage")
            if isinstance(token_usage, dict):
                return token_usage
        return {}

    def _aggregate_usage(self, traces: list[dict[str, Any]]) -> dict[str, Any]:
        total = self._usage_bucket("all")
        by_agent: dict[str, dict[str, Any]] = {}
        by_model: dict[str, dict[str, Any]] = {}
        by_prompt: dict[str, dict[str, Any]] = {}
        for trace in traces:
            usage = trace.get("token_usage") if isinstance(trace.get("token_usage"), dict) else {}
            input_tokens, output_tokens, total_tokens = self._token_counts(usage)
            fallback_used = bool(trace.get("fallback_used"))
            latency_ms = int(trace.get("latency_ms") or 0)
            agent = str(trace.get("agent") or "unknown")
            model = str(trace.get("model") or "fallback")
            prompt_version = str(trace.get("prompt_version") or "v1")
            prompt_key = f"{agent}:{prompt_version}"
            self._add_usage(total, model, input_tokens, output_tokens, total_tokens, latency_ms, fallback_used)
            self._add_usage(
                by_agent.setdefault(agent, self._usage_bucket(agent)),
                model,
                input_tokens,
                output_tokens,
                total_tokens,
                latency_ms,
                fallback_used,
            )
            self._add_usage(
                by_model.setdefault(model, self._usage_bucket(model)),
                model,
                input_tokens,
                output_tokens,
                total_tokens,
                latency_ms,
                fallback_used,
            )
            self._add_usage(
                by_prompt.setdefault(prompt_key, self._usage_bucket(prompt_key)),
                model,
                input_tokens,
                output_tokens,
                total_tokens,
                latency_ms,
                fallback_used,
            )
        return {
            "total": self._finalize_usage(total),
            "by_agent": [self._finalize_usage(item) for item in by_agent.values()],
            "by_model": [self._finalize_usage(item) for item in by_model.values()],
            "by_prompt": [self._finalize_usage(item) for item in by_prompt.values()],
        }

    def _usage_bucket(self, name: str) -> dict[str, Any]:
        return {
            "name": name,
            "calls": 0,
            "fallback_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "latency_ms": 0,
            "cost_by_model": {},
        }

    def _add_usage(
        self,
        bucket: dict[str, Any],
        model: str,
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
        model_tokens = bucket["cost_by_model"].setdefault(model, {"input_tokens": 0, "output_tokens": 0})
        model_tokens["input_tokens"] += input_tokens
        model_tokens["output_tokens"] += output_tokens

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

    def _apply_costs(self, summary: dict[str, Any], pricing: dict[str, dict[str, float]]) -> None:
        for section in ["total", "by_agent", "by_model", "by_prompt"]:
            value = summary.get(section)
            items = value if isinstance(value, list) else [value]
            for item in items:
                if isinstance(item, dict):
                    item["estimated_cost_usd"] = self._estimated_cost(item, pricing)

    def _estimated_cost(self, item: dict[str, Any], pricing: dict[str, dict[str, float]]) -> float:
        total = 0.0
        by_model = item.get("cost_by_model") if isinstance(item.get("cost_by_model"), dict) else {}
        for model, tokens in by_model.items():
            price = pricing.get(str(model), {})
            input_tokens = float(tokens.get("input_tokens") or 0)
            output_tokens = float(tokens.get("output_tokens") or 0)
            total += (input_tokens / 1_000_000) * float(price.get("input_per_1m") or 0)
            total += (output_tokens / 1_000_000) * float(price.get("output_per_1m") or 0)
        return round(total, 6)

    def _save_trace(
        self,
        *,
        trace_id: str,
        agent: str,
        prompt_version: str,
        model: str | None,
        input_payload: dict[str, Any],
        output_text: str,
        fallback_used: bool,
        error_message: str | None,
        latency_ms: int,
        token_usage: dict[str, Any],
    ) -> None:
        try:
            task_store.save_llm_trace(
                {
                    "trace_id": trace_id,
                    "agent": agent,
                    "prompt_version": prompt_version,
                    "model": model,
                    "input": input_payload,
                    "output": output_text,
                    "fallback_used": fallback_used,
                    "error_message": error_message,
                    "latency_ms": latency_ms,
                    "token_usage": token_usage,
                    "created_at": utc_now_iso(),
                }
            )
        except Exception:
            pass

    def _elapsed_ms(self, started: float) -> int:
        return max(0, int((time.perf_counter() - started) * 1000))


llm_provider = LLMProvider()
