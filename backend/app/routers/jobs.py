"""Job listing, cancellation, and the live event stream."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..jobs import queue

router = APIRouter()


@router.get("/projects/{project_id}/jobs")
def list_jobs(project_id: str, limit: int = 100, status: Optional[str] = None) -> list[dict]:
    return queue.list_jobs(project_id, limit=limit, status=status)


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = queue.get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, bool]:
    queue.cancel(job_id)
    return {"cancelled": True}


@router.delete("/projects/{project_id}/jobs")
def clear_jobs(project_id: str) -> dict[str, int]:
    return {"cleared": queue.clear_finished(project_id)}


@router.get("/projects/{project_id}/events")
async def events(project_id: str, request: Request) -> StreamingResponse:
    """SSE stream of job progress, so node badges update live."""
    subscription = queue.subscribe()

    async def stream():
        try:
            yield "retry: 2000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(subscription.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event.get("project_id") not in (None, project_id):
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            queue.unsubscribe(subscription)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
