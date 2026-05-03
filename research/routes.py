import json
import time

from flask import Blueprint, Response, jsonify, render_template, request
from flask_login import login_required

from research.db import run_migrations
from research.jobs import create_run, find_existing_run, get_run
from research.pipeline import start_pipeline

research_bp = Blueprint("research", __name__, url_prefix="/research")


@research_bp.before_app_request
def ensure_research_schema():
    if not getattr(ensure_research_schema, "_done", False):
        run_migrations()
        ensure_research_schema._done = True


@research_bp.route("")
@login_required
def index():
    return render_template("research.html")


@research_bp.route("/run", methods=["POST"])
@login_required
def run():
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "YouTube URL is required"}), 400

    existing = find_existing_run(url)
    if existing:
        return jsonify({"run_id": existing["id"], "duplicate": True}), 200

    run_id = create_run(url)
    start_pipeline(run_id, url)
    return jsonify({"run_id": run_id, "duplicate": False}), 202


@research_bp.route("/runs/<int:run_id>")
@login_required
def run_state(run_id: int):
    run = get_run(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    return jsonify(_serialize_run(run))


@research_bp.route("/stream/<int:run_id>")
@login_required
def stream(run_id: int):
    def generate():
        sent = 0
        while True:
            run = get_run(run_id)
            if not run:
                yield _sse({"type": "error", "message": "Run not found"})
                return
            events = ((run.get("progress") or {}).get("events") or [])
            for event in events[sent:]:
                yield _sse(event)
            sent = len(events)
            if run["status"] in {"complete", "error"}:
                return
            time.sleep(1)

    return Response(generate(), mimetype="text/event-stream")


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


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"

