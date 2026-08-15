"""Jobs — the poll target for work that outlives its request.

One endpoint, because that is all a polling client needs: where it is, what it
is doing in words, and the video id once there is one. `progress_note` is
written by the worker as a human sentence rather than a stage enum, so the UI
can show "verifying 18 source URLs" without shipping a translation table that
falls out of date the next time a stage is added.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from vira.api import store
from vira.api.schemas import JobOut

router = APIRouter(prefix="/v1", tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: str) -> JobOut:
    row = await store.get_job(job_id)
    if not row:
        raise HTTPException(404, f"no job {job_id}")
    return JobOut.of(row)
