from datetime import datetime, timezone
import logging
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

log = logging.getLogger(__name__)


def start_pipeline(run_id: int, youtube_url: str):
    if not mark_active(run_id):
        log.warning("[PIPELINE %s] Start skipped because run is already active", run_id)
        return
    if os.environ.get("RESEARCH_SYNC_MODE", "").strip().lower() in {"1", "true", "yes"}:
        log.warning("[PIPELINE %s] Running synchronously for test mode", run_id)
        _run_pipeline(run_id, youtube_url)
        return
    log.warning("[PIPELINE %s] Starting daemon background thread", run_id)
    thread = Thread(target=_run_pipeline, args=(run_id, youtube_url), daemon=True)
    thread.start()


def _run_pipeline(run_id: int, youtube_url: str):
    log.warning("[PIPELINE %s] Thread started, fetching transcript", run_id)
    try:
        update_run(run_id, status="running", current_stage="transcript")
        _event(run_id, {"type": "stage_start", "stage": "transcript", "label": "Transcript"})
        transcript_payload = fetch_transcript(youtube_url)
        log.warning(
            "[PIPELINE %s] Transcript fetched: %s words via %s",
            run_id,
            transcript_payload.get("word_count"),
            transcript_payload.get("source"),
        )
        podcast_id = _upsert_podcast(youtube_url, transcript_payload)
        update_run(run_id, podcast_id=podcast_id)
        _event(run_id, {"type": "stage_done", "stage": "transcript"})

        log.warning("[PIPELINE %s] Starting Stage 1 (Gemini)", run_id)
        _event(run_id, {"type": "stage_start", "stage": 1, "label": "Signal Extraction"})
        update_run(run_id, current_stage="stage1")
        stage1 = run_stage1(transcript_payload["transcript"])
        log.warning("[PIPELINE %s] Stage 1 complete: %s chars", run_id, len(stage1 or ""))
        update_run(run_id, stage1_output=stage1)
        _event(run_id, {"type": "stage_done", "stage": 1})

        log.warning("[PIPELINE %s] Starting Stage 2 (Claude)", run_id)
        _event(run_id, {"type": "stage_start", "stage": 2, "label": "Thematic Analysis"})
        update_run(run_id, current_stage="stage2")
        stage2 = run_stage2(stage1)
        log.warning("[PIPELINE %s] Stage 2 complete: %s chars", run_id, len(stage2 or ""))
        update_run(run_id, stage2_output=stage2)
        _event(run_id, {"type": "stage_done", "stage": 2})

        log.warning("[PIPELINE %s] Starting Stage 3 plan", run_id)
        _event(run_id, {"type": "stage_start", "stage": 3, "label": "Parallel Research"})
        update_run(run_id, current_stage="stage3")
        stage3_plan = run_stage3_plan(stage1, stage2)
        prompts = parse_research_prompts(stage3_plan)
        log.warning("[PIPELINE %s] Stage 3 plan complete: %s prompts", run_id, len(prompts))
        update_run(run_id, stage3_plan_output=stage3_plan)
        _event(
            run_id,
            {
                "type": "research_prompts_dispatched",
                "count": len(prompts),
                "titles": [prompt["title"] for prompt in prompts],
            },
        )
        log.warning("[PIPELINE %s] Dispatching Stage 3 parallel research", run_id)
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
        log.warning("[PIPELINE %s] Stage 3 research complete: %s prompts", run_id, len(research))
        update_run(run_id, stage3_research=research)
        _event(run_id, {"type": "stage_done", "stage": 3})

        log.warning("[PIPELINE %s] Starting Stage 4 (Gemini)", run_id)
        _event(run_id, {"type": "stage_start", "stage": 4, "label": "Consolidation"})
        update_run(run_id, current_stage="stage4")
        stage4 = run_stage4(stage2, research)
        log.warning("[PIPELINE %s] Stage 4 complete: %s chars", run_id, len(stage4 or ""))
        update_run(run_id, stage4_output=stage4)
        _event(run_id, {"type": "stage_done", "stage": 4})

        log.warning("[PIPELINE %s] Starting Stage 5 (Claude Opus 4.7 thinking)", run_id)
        _event(run_id, {"type": "stage_start", "stage": 5, "label": "Equity Screen"})
        update_run(run_id, current_stage="stage5")
        stage5, market_data, portfolio = run_stage5(stage4)
        log.warning(
            "[PIPELINE %s] Stage 5 complete: %s chars, %s market records",
            run_id,
            len(stage5 or ""),
            len(market_data or {}),
        )
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
        log.warning("[PIPELINE %s] PIPELINE COMPLETE", run_id)
        _event(run_id, {"type": "pipeline_complete"})
        _event(run_id, {"type": "final", "status": "complete"})
    except Exception as exc:
        log.error("[PIPELINE %s] FAILED: %s: %s", run_id, type(exc).__name__, exc, exc_info=True)
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
