from datetime import datetime, timezone
import logging
import os
from threading import Thread

from sqlalchemy import insert, select

from research.db import get_engine, podcasts
from research.jobs import (
    append_event,
    get_podcast_by_run,
    get_run,
    mark_active,
    mark_inactive,
    update_run,
)
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

_STAGE_ORDER = ["transcript", "stage1", "stage2", "stage3", "stage4", "stage5"]


def start_pipeline(run_id: int, youtube_url: str) -> bool:
    return _start_pipeline(run_id, youtube_url, "transcript")


def start_pipeline_resume(run_id: int, resume_from: str, force: bool = False) -> bool:
    run = get_run(run_id)
    if not run:
        raise ValueError(f"Run {run_id} not found")
    if resume_from == "complete":
        log.warning("[PIPELINE %s] Retry skipped because run is already complete", run_id)
        return True
    return _start_pipeline(run_id, run["youtube_url"], resume_from, force=force)


def _start_pipeline(run_id: int, youtube_url: str, resume_from: str, force: bool = False) -> bool:
    if resume_from not in _STAGE_ORDER:
        raise ValueError(f"Unknown resume stage: {resume_from}")
    if force:
        mark_inactive(run_id)
    if not mark_active(run_id):
        log.warning("[PIPELINE %s] Start skipped because run is already active", run_id)
        return False
    if os.environ.get("RESEARCH_SYNC_MODE", "").strip().lower() in {"1", "true", "yes"}:
        log.warning("[PIPELINE %s] Running synchronously for test mode (resume_from=%s)", run_id, resume_from)
        _run_pipeline(run_id, youtube_url, resume_from)
        return True
    log.warning("[PIPELINE %s] Starting daemon background thread (resume_from=%s)", run_id, resume_from)
    thread = Thread(target=_run_pipeline, args=(run_id, youtube_url, resume_from), daemon=True)
    thread.start()
    return True


def _run_pipeline(run_id: int, youtube_url: str, resume_from: str = "transcript"):
    log.warning("[PIPELINE %s] Thread started (resume_from=%s)", run_id, resume_from)
    try:
        run = get_run(run_id)
        if not run:
            raise RuntimeError(f"Run {run_id} not found")
        update_run(run_id, status="running", completed_at=None)

        transcript_text = None
        if _should_run(resume_from, "transcript"):
            if _is_manual_no_url(youtube_url):
                log.warning("[PIPELINE %s] Manual mode with no URL; skipping transcript", run_id)
                transcript_text = ""
                _event(run_id, {"type": "stage_skipped", "stage": "transcript"})
            else:
                update_run(run_id, current_stage="transcript")
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
                transcript_text = transcript_payload["transcript"]
                _event(run_id, {"type": "stage_done", "stage": "transcript"})
        else:
            podcast = get_podcast_by_run(run_id)
            if podcast:
                transcript_text = podcast["transcript"]
                log.warning("[PIPELINE %s] Transcript skipped (loaded from DB)", run_id)
                _event(run_id, {"type": "stage_skipped", "stage": "transcript"})
            else:
                transcript_text = ""
                log.warning("[PIPELINE %s] Resume from %s; transcript not needed", run_id, resume_from)
        if _should_run(resume_from, "stage1"):
            if not transcript_text:
                transcript_text = _load_or_fetch_transcript(run_id, youtube_url)
            log.warning("[PIPELINE %s] Starting Stage 1 (Gemini)", run_id)
            _event(run_id, {"type": "stage_start", "stage": 1, "label": "Signal Extraction"})
            update_run(run_id, current_stage="stage1")
            stage1 = run_stage1(transcript_text)
            log.warning("[PIPELINE %s] Stage 1 complete: %s chars", run_id, len(stage1 or ""))
            update_run(run_id, stage1_output=stage1)
            _event(run_id, {"type": "stage_done", "stage": 1})
        else:
            stage1 = run.get("stage1_output")
            if _resume_index(resume_from) <= _resume_index("stage4"):
                stage1 = _require_output(run, "stage1_output", run_id)
            log.warning("[PIPELINE %s] Stage 1 skipped%s", run_id, " (loaded from DB)" if stage1 else "")
            _event(run_id, {"type": "stage_skipped", "stage": 1})

        if _should_run(resume_from, "stage2"):
            log.warning("[PIPELINE %s] Starting Stage 2 (Claude)", run_id)
            _event(run_id, {"type": "stage_start", "stage": 2, "label": "Thematic Analysis"})
            update_run(run_id, current_stage="stage2")
            stage2 = run_stage2(stage1)
            log.warning("[PIPELINE %s] Stage 2 complete: %s chars", run_id, len(stage2 or ""))
            update_run(run_id, stage2_output=stage2)
            _event(run_id, {"type": "stage_done", "stage": 2})
        else:
            stage2 = run.get("stage2_output")
            if _resume_index(resume_from) <= _resume_index("stage4"):
                stage2 = _require_output(run, "stage2_output", run_id)
            log.warning("[PIPELINE %s] Stage 2 skipped%s", run_id, " (loaded from DB)" if stage2 else "")
            _event(run_id, {"type": "stage_skipped", "stage": 2})

        if _should_run(resume_from, "stage3"):
            log.warning("[PIPELINE %s] Starting Stage 3", run_id)
            _event(run_id, {"type": "stage_start", "stage": 3, "label": "Parallel Research"})
            update_run(run_id, current_stage="stage3")
            stage3_plan = run.get("stage3_plan_output")
            if stage3_plan:
                log.warning("[PIPELINE %s] Stage 3 plan reused from DB", run_id)
                _event(run_id, {"type": "stage_artifact_reused", "stage": "3-plan"})
            else:
                log.warning("[PIPELINE %s] Starting Stage 3 plan", run_id)
                stage3_plan = run_stage3_plan(stage1, stage2)
                update_run(run_id, stage3_plan_output=stage3_plan)
            prompts = parse_research_prompts(stage3_plan)
            log.warning("[PIPELINE %s] Stage 3 plan complete: %s prompts", run_id, len(prompts))
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
        else:
            stage3_plan = run.get("stage3_plan_output")
            research = run.get("stage3_research")
            if resume_from == "stage4":
                stage3_plan = _require_output(run, "stage3_plan_output", run_id)
                research = _require_output(run, "stage3_research", run_id)
            log.warning("[PIPELINE %s] Stage 3 skipped%s", run_id, " (loaded from DB)" if research else "")
            _event(run_id, {"type": "stage_skipped", "stage": 3})

        if _should_run(resume_from, "stage4"):
            log.warning("[PIPELINE %s] Starting Stage 4 (Gemini)", run_id)
            _event(run_id, {"type": "stage_start", "stage": 4, "label": "Consolidation"})
            update_run(run_id, current_stage="stage4")
            stage4 = run_stage4(stage2, research)
            log.warning("[PIPELINE %s] Stage 4 complete: %s chars", run_id, len(stage4 or ""))
            update_run(run_id, stage4_output=stage4)
            _event(run_id, {"type": "stage_done", "stage": 4})
        else:
            stage4 = _require_output(run, "stage4_output", run_id)
            log.warning("[PIPELINE %s] Stage 4 skipped (loaded from DB)", run_id)
            _event(run_id, {"type": "stage_skipped", "stage": 4})

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
        current = get_run(run_id) or {}
        update_run(
            run_id,
            status="error",
            error_message=str(exc),
            error_stage=current.get("current_stage") or "pipeline",
            completed_at=datetime.now(timezone.utc),
        )
        _event(run_id, {"type": "error", "message": str(exc)})
        _event(run_id, {"type": "final", "status": "error"})
    finally:
        mark_inactive(run_id)


def _should_run(resume_from: str, stage: str) -> bool:
    return _resume_index(stage) >= _resume_index(resume_from)


def _resume_index(stage: str) -> int:
    return _STAGE_ORDER.index(stage)


def _require_output(run: dict, field: str, run_id: int):
    value = run.get(field)
    if value is None or value == "":
        raise RuntimeError(f"Cannot resume run {run_id}; missing {field}")
    return value


def _load_or_fetch_transcript(run_id: int, youtube_url: str) -> str:
    podcast = get_podcast_by_run(run_id)
    if podcast:
        return podcast["transcript"]
    if _is_manual_no_url(youtube_url):
        raise RuntimeError("Cannot run Stage 1 without a YouTube URL or uploaded Stage 1 output")
    transcript_payload = fetch_transcript(youtube_url)
    podcast_id = _upsert_podcast(youtube_url, transcript_payload)
    update_run(run_id, podcast_id=podcast_id)
    return transcript_payload["transcript"]


def _is_manual_no_url(youtube_url: str | None) -> bool:
    return not youtube_url or youtube_url == "manual-mode-no-url"


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
