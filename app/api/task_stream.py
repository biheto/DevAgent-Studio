"""Stream committed task events while synchronous graph work runs off the event loop."""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from queue import Empty, Full, Queue
from threading import Event, Lock

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.harness.events import listen_for_events
from app.schemas.studio import TaskRunResponse

# Retain executions after a browser disconnects; their results still reach SQLite.
_executions: set[asyncio.Task] = set()


def task_stream_response(run: Callable[[], TaskRunResponse]) -> StreamingResponse:
    async def stream():
        queue: Queue[dict] = Queue(maxsize=256)
        disconnected = Event()
        seen: set[str] = set()
        lock = Lock()

        def enqueue(payload: dict) -> None:
            while not disconnected.is_set():
                try:
                    queue.put(payload, timeout=0.1)
                    return
                except Full:
                    continue

        def on_event(event: dict) -> None:
            # A graph's returned state can include events already committed and sent.
            with lock:
                event_id = event.get("event_id")
                if event_id and event_id in seen:
                    return
                if event_id:
                    seen.add(event_id)
                enqueue(event)

        def execute() -> None:
            try:
                with listen_for_events(on_event):
                    response = run()
                    for event in response.events:
                        on_event(event)
                    enqueue({**response.result, "type": "task_result",
                             "task_id": response.task_id, "status": response.status})
            except Exception as exc:
                message = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
                enqueue({"type": "error", "content": message})
            finally:
                enqueue({"type": "complete", "completed": True})

        worker = asyncio.create_task(asyncio.to_thread(execute))
        _executions.add(worker)
        worker.add_done_callback(_executions.discard)
        last_send = time.monotonic()
        try:
            while True:
                try:
                    payload = queue.get_nowait()
                except Empty:
                    if time.monotonic() - last_send >= 15:
                        yield ": keep-alive\n\n"
                        last_send = time.monotonic()
                    await asyncio.sleep(0.05)
                    continue
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                last_send = time.monotonic()
                if payload.get("type") == "complete":
                    break
        finally:
            # Disconnect stops delivery, not the underlying persisted execution.
            disconnected.set()

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })
