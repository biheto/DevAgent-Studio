from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4


BEIJING_TZ = timezone(timedelta(hours=8))
BEIJING_TIME_FORMAT = "%Y-%m-%d, %H:%M"

_event_listener: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar(
    "task_event_listener", default=None
)


@contextmanager
def listen_for_events(listener: Callable[[dict[str, Any]], None]) -> Iterator[None]:
    """Scope notifications to one execution, including its copied worker contexts."""
    token = _event_listener.set(listener)
    try:
        yield
    finally:
        _event_listener.reset(token)


def publish_event(event: dict[str, Any]) -> None:
    listener = _event_listener.get()
    if listener is not None:
        listener(dict(event))


def utc_now_iso() -> str:
    return datetime.now(BEIJING_TZ).strftime(BEIJING_TIME_FORMAT)


@dataclass
class AgentEvent:
    task_id: str
    type: str
    content: str
    node: str | None = None
    agent: str | None = None
    status: str = "running"
    data: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: f"evt_{uuid4().hex}")
    timestamp: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "task_id": self.task_id,
            "type": self.type,
            "node": self.node,
            "agent": self.agent,
            "status": self.status,
            "content": self.content,
            "data": self.data,
            "timestamp": self.timestamp,
        }


class EventSink:
    def __init__(self):
        self.events: list[AgentEvent] = []

    def emit(
        self,
        task_id: str,
        type: str,
        content: str,
        node: str | None = None,
        agent: str | None = None,
        status: str = "running",
        data: dict[str, Any] | None = None,
    ) -> AgentEvent:
        event = AgentEvent(
            task_id=task_id,
            type=type,
            content=content,
            node=node,
            agent=agent,
            status=status,
            data=data or {},
        )
        self.events.append(event)
        return event

    def to_list(self) -> list[dict[str, Any]]:
        return [event.to_dict() for event in self.events]
