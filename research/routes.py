import json
import logging
import os
import time

from flask import Blueprint, Response, current_app, jsonify, render_template, request, stream_with_context
from flask_login import current_user, login_required

from research.db import cleanup_zombie_runs, run_migrations
from research import config
from research.jobs import create_manual_run, create_run, find_existing_completed_run, get_run, update_run
from research.pipeline import start_pipeline, start_pipeline_resume

research_bp = Blueprint("research", __name__, url_prefix="/research")


@research_bp.before_app_request
def ensure_research_schema():
    if not getattr(ensure_research_schema, "_done", False):
        logging.getLogger(__name__).warning(
            "RESEARCH_LIVE_MODE env=%s parsed=%s",
            os.environ.get("RESEARCH_LIVE_MODE"),
            config.is_live_mode(),
        )
        run_migrations()
        cleanup_zombie_runs()
        ensure_research_schema._done = True


@research_bp.route("")
@login_required
def index():
    return render_template("research.html")


@research_bp.route("/run", methods=["POST"])
@login_required
def run():
    user_id = getattr(current_user, "id", "anon") if current_user.is_authenticated else "anon"
    current_app.logger.warning("[PIPELINE] POST /research/run received from user %s", user_id)
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip() or None
    manual = bool(body.get("manual"))
    uploaded_stages = body.get("uploaded_stages") or {}
    current_app.logger.warning("[PIPELINE] URL submitted: %s", url)
    if not manual and not url:
        return jsonify({"error": "YouTube URL required (or enable manual mode)"}), 400
    if manual:
        validation_error = _validate_manual_payload(uploaded_stages, url)
        if validation_error:
            return jsonify({"error": validation_error}), 400

    existing = find_existing_completed_run(url) if (url and not manual) else None
    if existing:
        current_app.logger.warning("[PIPELINE] Duplicate URL matched completed run %s", existing["id"])
        return jsonify({"run_id": existing["id"], "duplicate": True}), 200

    if manual:
        run_id = create_manual_run(url, uploaded_stages)
        resume_from = _determine_resume_from_uploads(uploaded_stages)
        current_app.logger.warning("[PIPELINE] Created manual run %s, resuming from %s", run_id, resume_from)
        start_pipeline_resume(run_id, resume_from)
        return jsonify(
            {"run_id": run_id, "duplicate": False, "manual": True, "resume_from": resume_from}
        ), 202

    run_id = create_run(url)
    current_app.logger.warning("[PIPELINE] Created run %s, dispatching background thread", run_id)
    start_pipeline(run_id, url)
    current_app.logger.warning("[PIPELINE] Background thread for run %s dispatched", run_id)
    return jsonify({"run_id": run_id, "duplicate": False}), 202


@research_bp.route("/run/<int:run_id>/retry", methods=["POST"])
@login_required
def retry_run(run_id: int):
    current_app.logger.warning("[PIPELINE] Retry requested for run %s", run_id)
    run = get_run(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    if run["status"] not in {"error", "complete"}:
        return jsonify({"error": f"Cannot retry a run with status '{run['status']}'"}), 400

    resume_from = _determine_resume_stage(run)
    current_app.logger.warning("[PIPELINE] Retrying run %s from %s", run_id, resume_from)
    if resume_from == "complete":
        return jsonify({"run_id": run_id, "resume_from": resume_from, "status": "complete"}), 200

    update_run(
        run_id,
        status="running",
        current_stage=resume_from,
        progress={"events": []},
        error_message=None,
        error_stage=None,
        completed_at=None,
        **_clear_outputs_from(resume_from),
    )
    start_pipeline_resume(run_id, resume_from)
    return jsonify({"run_id": run_id, "resume_from": resume_from, "status": "running"}), 202


@research_bp.route("/runs/<int:run_id>")
@login_required
def run_state(run_id: int):
    try:
        run = get_run(run_id)
        if not run:
            return jsonify({"error": "Run not found"}), 404
        return jsonify(_serialize_run(run))
    except Exception as exc:
        current_app.logger.warning("Failed to fetch run %s: %s", run_id, exc)
        return jsonify({"error": "transient", "message": "State temporarily unavailable"}), 503


@research_bp.route("/stream/<int:run_id>")
@login_required
def stream(run_id: int):
    def generate():
        last_event_index = 0
        max_iterations = 600
        iteration = 0
        while iteration < max_iterations:
            iteration += 1
            try:
                run = get_run(run_id)
            except Exception:
                yield ": keepalive\n\n"
                time.sleep(2)
                continue

            if not run:
                yield _sse({"type": "error", "message": "Run not found"})
                return

            events = ((run.get("progress") or {}).get("events") or [])
            for event in events[last_event_index:]:
                yield _sse(event)
            last_event_index = len(events)

            if run["status"] in {"complete", "error"}:
                yield _sse({"type": "final", "status": run["status"], "run_id": run_id})
                return

            yield ": keepalive\n\n"
            time.sleep(2)

    response = Response(stream_with_context(generate()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


@research_bp.route("/report/<int:run_id>/<path:filename>")
@login_required
def report(run_id: int, filename: str):
    run = get_run(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    content = _report_content(run, filename)
    if content is None:
        return jsonify({"error": "Report not available"}), 404
    return Response(
        content,
        mimetype="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _report_content(run: dict, filename: str):
    stage_map = {
        "stage1.md": run.get("stage1_output"),
        "stage2.md": run.get("stage2_output"),
        "stage3-plan.md": run.get("stage3_plan_output"),
        "stage4.md": run.get("stage4_output"),
        "stage5.md": run.get("stage5_output"),
    }
    if filename == "full.md":
        sections = [
            run.get("stage1_output"),
            run.get("stage2_output"),
            run.get("stage3_plan_output"),
            _format_stage3_research(run.get("stage3_research")),
            run.get("stage4_output"),
            run.get("stage5_output"),
        ]
        return "\n\n---\n\n".join(section for section in sections if section)
    return stage_map.get(filename)


def _format_stage3_research(research):
    if not research:
        return ""
    lines = ["# STAGE 3 RESEARCH RESULTS"]
    for prompt_id, result in research.items():
        lines.append(f"## {prompt_id}: {result.get('title', '')}")
        lines.append(result.get("result", ""))
        citations = result.get("citations") or []
        if citations:
            lines.append("### Citations")
            for citation in citations:
                if isinstance(citation, dict):
                    lines.append(f"- [{citation.get('title', citation.get('url'))}]({citation.get('url')})")
                else:
                    lines.append(f"- {citation}")
    return "\n".join(lines)


def _serialize_run(run: dict) -> dict:
    out = {}
    for key, value in run.items():
        out[key] = value.isoformat() if hasattr(value, "isoformat") else value
    return out


def _determine_resume_stage(run: dict) -> str:
    if not run.get("stage1_output"):
        return "stage1"
    if not run.get("stage2_output"):
        return "stage2"
    if not run.get("stage3_plan_output") or not run.get("stage3_research"):
        return "stage3"
    if not run.get("stage4_output"):
        return "stage4"
    if not run.get("stage5_output"):
        return "stage5"
    return "complete"


def _clear_outputs_from(resume_from: str) -> dict:
    fields_by_stage = {
        "stage1": {
            "stage1_output": None,
            "stage2_output": None,
            "stage3_plan_output": None,
            "stage3_research": None,
            "stage4_output": None,
            "stage5_output": None,
            "live_market_data": None,
            "portfolio_snapshot": None,
        },
        "stage2": {
            "stage2_output": None,
            "stage3_plan_output": None,
            "stage3_research": None,
            "stage4_output": None,
            "stage5_output": None,
            "live_market_data": None,
            "portfolio_snapshot": None,
        },
        "stage3": {
            "stage3_plan_output": None,
            "stage3_research": None,
            "stage4_output": None,
            "stage5_output": None,
            "live_market_data": None,
            "portfolio_snapshot": None,
        },
        "stage4": {
            "stage4_output": None,
            "stage5_output": None,
            "live_market_data": None,
            "portfolio_snapshot": None,
        },
        "stage5": {
            "stage5_output": None,
            "live_market_data": None,
            "portfolio_snapshot": None,
        },
    }
    return fields_by_stage.get(resume_from, {})


def _validate_manual_payload(uploaded_stages: dict, youtube_url: str | None) -> str | None:
    if not isinstance(uploaded_stages, dict):
        return "uploaded_stages must be an object"
    if not uploaded_stages and not youtube_url:
        return "Manual mode requires at least one uploaded stage or a YouTube URL"
    if ("3-plan" in uploaded_stages) != ("3-research" in uploaded_stages):
        return "Stage 3 requires both the research plan AND research findings, or neither"
    allowed_keys = {"1", "2", "3-plan", "3-research", "4"}
    for stage_key, content in uploaded_stages.items():
        if stage_key not in allowed_keys:
            return f"Unknown uploaded stage {stage_key}"
        if not isinstance(content, str) or not content.strip():
            return f"Uploaded stage {stage_key} is empty"
        if len(content.encode("utf-8")) > 5 * 1024 * 1024:
            return f"Stage {stage_key} content exceeds 5MB"
    return None


def _determine_resume_from_uploads(uploaded_stages: dict) -> str:
    if "4" in uploaded_stages:
        return "stage5"
    if "3-plan" in uploaded_stages and "3-research" in uploaded_stages:
        return "stage4"
    if "2" in uploaded_stages:
        return "stage3"
    if "1" in uploaded_stages:
        return "stage2"
    return "stage1"


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"
