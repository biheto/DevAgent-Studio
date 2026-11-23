from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from types import SimpleNamespace

import pytest

from app.harness import budget


@pytest.fixture
def provider(tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_AGENT_CHECKPOINT_DB", str(tmp_path / "checkpoints.db"))
    return SimpleNamespace(_read_env_file=lambda: {}, _agent_env_key=lambda m: m,
                           _read_env_value=lambda *_: "", _token_counts=lambda u: (u["input_tokens"], u["output_tokens"], 0))


def test_concurrent_calls_reserve_atomically_and_survive_reopening(provider):
    with budget.task_budget("task", {"max_tokens": 2400}):
        def call():
            try:
                return budget.reserve(provider, "worker", "test", "x")[0]
            except budget.BudgetExceeded:
                return None
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = [pool.submit(copy_context().run, call) for _ in range(3)]
        ids = [job.result() for job in jobs if job.result()]
    assert len(ids) == 1
    with budget.task_budget("task", {"max_tokens": 999999}):
        assert budget.snapshot("task")["options"]["max_tokens"] == 2400
        with pytest.raises(budget.BudgetExceeded):
            budget.reserve(provider, "worker", "test", "x")
    budget.settle(ids[0], {"input_tokens": 30, "output_tokens": 20}, provider)
    assert budget.snapshot("task")["accounted_tokens"] == 50
    assert budget.snapshot("task")["estimated_cost_usd"] is None


def test_missing_price_blocks_money_budget_and_timeouts_remain_charged(provider):
    with budget.task_budget("task", {"max_cost_usd": 1}):
        with pytest.raises(budget.BudgetExceeded, match="单价"):
            budget.reserve(provider, "worker", "test", "hello")
        budget.update_budget("task", {"max_cost_usd": 1, "input_price_per_1m": 1, "output_price_per_1m": 2})
        call, _ = budget.reserve(provider, "worker", "test", "hello")
        before = budget.snapshot("task")
        budget.settle(call, None, provider)
        after = budget.snapshot("task")
        assert after["uncertain_calls"] == 1 and after["estimated_cost_usd"] == before["estimated_cost_usd"]


def test_prices_must_be_finite_and_zero_is_explicit(provider):
    with pytest.raises(ValueError):
        budget.BudgetOptions(max_cost_usd=float("nan"))
    assert budget.prices(provider, "test", {}) == (None, None)
    assert budget.prices(provider, "test", {"input_price_per_1m": 0, "output_price_per_1m": 0}) == (0, 0)


def test_incomplete_usage_is_not_treated_as_zero_output(provider):
    with budget.task_budget("task", {}):
        call, _ = budget.reserve(provider, "worker", "test", "x")
        before = budget.snapshot("task")["accounted_tokens"]
        budget.settle(call, {"input_tokens": 1}, provider)
    assert budget.snapshot("task")["uncertain_calls"] == 1
    assert budget.snapshot("task")["accounted_tokens"] == before
