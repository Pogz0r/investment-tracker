from datetime import datetime, timezone
import os
from threading import Thread

from sqlalchemy import insert, select

from research.db import get_engine, podcasts
from research.jobs import append_event, mark_active, mark_inactive, update_run
from research.stages import (
    parse_research_prompts,
    run_stage1,
    run_stage2,
    run_stage3_plan,
    run_stage3_research,
    run_stage4,
    run_stage5,
)
from research.transcriber import fetch_transcript
from research.utils import sha256_hash


def start_pipeline(run_id: int, youtube_url: str):
    if not mark_active(run_id):
        return
    if os.environ.get("RESEARCH_SYNC_MODE", "").strip().lower() in {"1", "true", "yes"}:
        _run_pipeline(run_id, youtube_url)
        return
    thread = Thread(target=_run_pipeline, args=(run_id, youtube_url), daemon=True)
    thread.start()


def _run_pipeline(run_id: int, youtube_url: str):
    try:
        update_run(run_id, status="running", current_stage="transcript")
        _event(run_id, {"type": "stage_start", "stage": "transcript", "label": "Transcript"})
        transcript_payload = fetch_transcript(youtube_url)
        podcast_id = _upsert_podcast(youtube_url, transcript_payload)
        update_run(run_id, podcast_id=podcast_id)
        _event(run_id, {"type": "stage_done", "stage": "transcript"})

        _event(run_id, {"type": "stage_start", "stage": 1, "label": "Signal Extraction"})
        update_run(run_id, current_stage="stage1")
        stage1 = run_stage1(transcript_payload["transcript"])
        update_run(run_id, stage1_output=stage1)
        _event(run_id, {"type": "stage_done", "stage": 1})

        _event(run_id, {"type": "stage_start", "stage": 2, "label": "Thematic Analysis"})
        update_run(run_id, current_stage="stage2")
        stage2 = run_stage2(stage1)
        update_run(run_id, stage2_output=stage2)
        _event(run_id, {"type": "stage_done", "stage": 2})

        _event(run_id, {"type": "stage_start", "stage": 3, "label": "Parallel Research"})
        update_run(run_id, current_stage="stage3")
        stage3_plan = run_stage3_plan(stage1, stage2)
        prompts = parse_research_prompts(stage3_plan)
        update_run(run_id, stage3_plan_output=stage3_plan)
        _event(
            run_id,
            {
                "type": "research_prompts_dispatched",
                "count": len(prompts),
                "titles": [prompt["title"] for prompt in prompts],
            },
        )
        research = run_stage3_research(
            prompts,
            on_prompt_done=lambda prompt_id, completed, total: _event(
                run_id,
                {
                    "type": "research_prompt_done",
                    "prompt_id": prompt_id,
                    "completed": completed,
                    "total": total,
                },
            ),
        )
        update_run(run_id, stage3_research=research)
        _event(run_id, {"type": "stage_done", "stage": 3})

        _event(run_id, {"type": "stage_start", "stage": 4, "label": "Consolidation"})
        update_run(run_id, current_stage="stage4")
        stage4 = run_stage4(stage2, research)
        update_run(run_id, stage4_output=stage4)
        _event(run_id, {"type": "stage_done", "stage": 4})

        _event(run_id, {"type": "stage_start", "stage": 5, "label": "Equity Screen"})
        update_run(run_id, current_stage="stage5")
        stage5, market_data, portfolio = run_stage5(stage4)
        update_run(
            run_id,
            stage5_output=stage5,
            live_market_data=market_data,
            portfolio_snapshot=portfolio,
        )
        _event(run_id, {"type": "stage_done", "stage": 5})

        update_run(
            run_id,
            status="complete",
            current_stage="done",
            completed_at=datetime.now(timezone.utc),
        )
        _event(run_id, {"type": "pipeline_complete"})
        _event(run_id, {"type": "final", "status": "complete"})
    except Exception as exc:
        update_run(
            run_id,
            status="error",
            error_message=str(exc),
            error_stage="pipeline",
            completed_at=datetime.now(timezone.utc),
        )
        _event(run_id, {"type": "error", "message": str(exc)})
        _event(run_id, {"type": "final", "status": "error"})
    finally:
        mark_inactive(run_id)


def _event(run_id: int, event: dict):
    append_event(run_id, event)


def _upsert_podcast(youtube_url: str, payload: dict) -> int:
    transcript_hash = sha256_hash(payload["transcript"])
    with get_engine().begin() as conn:
        existing = conn.execute(
            select(podcasts.c.id).where(podcasts.c.transcript_hash == transcript_hash)
        ).scalar_one_or_none()
        if existing:
            return int(existing)
        result = conn.execute(
            insert(podcasts).values(
                youtube_url=youtube_url,
                video_id=payload["video_id"],
                title=payload.get("title"),
                transcript=payload["transcript"],
                transcript_hash=transcript_hash,
                transcript_source=payload.get("source"),
                word_count=payload.get("word_count"),
            )
        )
        return int(result.inserted_primary_key[0])


def reset_runner_for_tests():
    return None
