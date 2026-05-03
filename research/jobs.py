from datetime import datetime, timezone
from threading import Lock

from sqlalchemy import desc, insert, select, update

from research.db import get_engine, pipeline_runs

_active_jobs = set()
_active_jobs_lock = Lock()


def create_run(youtube_url: str) -> int:
    with get_engine().begin() as conn:
        result = conn.execute(
            insert(pipeline_runs).values(
                youtube_url=youtube_url,
                status="queued",
                current_stage="queued",
                progress={"events": []},
            )
        )
        return int(result.inserted_primary_key[0])


def find_existing_run(youtube_url: str):
    with get_engine().connect() as conn:
        row = conn.execute(
            select(pipeline_runs)
            .where(pipeline_runs.c.youtube_url == youtube_url)
            .order_by(desc(pipeline_runs.c.started_at))
            .limit(1)
        ).mappings().first()
    return dict(row) if row else None


def get_run(run_id: int):
    with get_engine().connect() as conn:
        row = conn.execute(
            select(pipeline_runs).where(pipeline_runs.c.id == run_id)
        ).mappings().first()
    return dict(row) if row else None


def update_run(run_id: int, **fields):
    fields["updated_at"] = datetime.now(timezone.utc)
    with get_engine().begin() as conn:
        conn.execute(
            update(pipeline_runs).where(pipeline_runs.c.id == run_id).values(**fields)
        )


def append_event(run_id: int, event: dict):
    run = get_run(run_id)
    progress = run.get("progress") or {}
    events = list(progress.get("events") or [])
    events.append({**event, "ts": datetime.now(timezone.utc).isoformat()})
    update_run(run_id, progress={"events": events})
    return events


def mark_active(run_id: int) -> bool:
    with _active_jobs_lock:
        if run_id in _active_jobs:
            return False
        _active_jobs.add(run_id)
        return True


def mark_inactive(run_id: int):
    with _active_jobs_lock:
        _active_jobs.discard(run_id)


def reset_active_jobs_for_tests():
    with _active_jobs_lock:
        _active_jobs.clear()
