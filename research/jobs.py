import json
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from sqlalchemy import desc, insert, select, update

from research.db import get_engine, pipeline_runs, podcasts

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


def create_manual_run(youtube_url: Optional[str], uploaded_stages: dict) -> int:
    values = {
        "youtube_url": youtube_url or "manual-mode-no-url",
        "status": "queued",
        "current_stage": "manual_init",
        "progress": {"events": []},
    }
    stage_to_column = {
        "1": "stage1_output",
        "2": "stage2_output",
        "3-plan": "stage3_plan_output",
        "3-research": "stage3_research",
        "4": "stage4_output",
    }
    for stage_key, column_name in stage_to_column.items():
        if stage_key not in uploaded_stages:
            continue
        content = uploaded_stages[stage_key]
        if column_name == "stage3_research":
            values[column_name] = _parse_uploaded_stage3_research(content)
        else:
            values[column_name] = content

    values = _sanitize_json_fields(values)
    with get_engine().begin() as conn:
        result = conn.execute(insert(pipeline_runs).values(**values))
        return int(result.inserted_primary_key[0])


def find_existing_completed_run(youtube_url: str):
    """
    Return a duplicate only when a previous run completed successfully.

    Failed, crashed, queued, and in-progress runs must not block a re-run.
    """
    with get_engine().connect() as conn:
        row = conn.execute(
            select(pipeline_runs)
            .where(pipeline_runs.c.youtube_url == youtube_url)
            .where(pipeline_runs.c.status == "complete")
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


def get_podcast_by_run(run_id: int):
    with get_engine().connect() as conn:
        row = conn.execute(
            select(podcasts)
            .select_from(podcasts.join(pipeline_runs, pipeline_runs.c.podcast_id == podcasts.c.id))
            .where(pipeline_runs.c.id == run_id)
        ).mappings().first()
    return dict(row) if row else None


def update_run(run_id: int, **fields):
    fields["updated_at"] = datetime.now(timezone.utc)
    fields = _sanitize_json_fields(fields)
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


def _sanitize_json_fields(fields: dict) -> dict:
    json_fields = {"progress", "stage3_research", "live_market_data", "portfolio_snapshot"}
    sanitized = dict(fields)
    for field in json_fields & sanitized.keys():
        sanitized[field] = _jsonb_safe_value(sanitized[field])
    return sanitized


def _jsonb_safe_value(value):
    try:
        json.dumps(value, allow_nan=False)
        return value
    except ValueError:
        from research.enrichers.yfinance_enricher import _sanitize_for_json

        sanitized = _sanitize_for_json(value)
        json.dumps(sanitized, allow_nan=False)
        return sanitized


def _parse_uploaded_stage3_research(content: str):
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        return {
            "P_uploaded": {
                "title": "Uploaded Stage 3 Research",
                "result": content,
                "citations": [],
                "error": None,
            }
        }
    if isinstance(parsed, dict):
        return {
            key: value
            if isinstance(value, dict)
            else {"title": str(key), "result": str(value), "citations": [], "error": None}
            for key, value in parsed.items()
        }
    return {
        "P_uploaded": {
            "title": "Uploaded Stage 3 Research",
            "result": json.dumps(parsed),
            "citations": [],
            "error": None,
        }
    }
