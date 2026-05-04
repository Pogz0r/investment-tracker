import os
import time

import pytest


os.environ.setdefault("RESEARCH_LIVE_MODE", "false")
os.environ.setdefault("RESEARCH_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("RESEARCH_SYNC_MODE", "true")
os.environ.setdefault("SELF_BASE_URL", "http://example.test")
os.environ.setdefault("PORTFOLIO_EXPORT_TOKEN", "test-token")


@pytest.fixture()
def research_app(monkeypatch):
    monkeypatch.setenv("RESEARCH_DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("RESEARCH_LIVE_MODE", "false")
    monkeypatch.setenv("RESEARCH_SYNC_MODE", "true")
    monkeypatch.setenv("SELF_BASE_URL", "http://example.test")
    monkeypatch.setenv("PORTFOLIO_EXPORT_TOKEN", "test-token")

    import research.db as research_db
    import research.jobs as research_jobs
    import research.pipeline as research_pipeline
    import research.routes as research_routes

    research_db.config.RESEARCH_DATABASE_URL = "sqlite:///:memory:"
    research_db.reset_engine_for_tests()
    research_db.run_migrations()
    research_jobs.reset_active_jobs_for_tests()
    research_pipeline.reset_runner_for_tests()
    research_routes.ensure_research_schema._done = False

    from app import app

    app.config.update(TESTING=True, LOGIN_DISABLED=True)
    return app


def test_research_run_completes_with_fake_clients(research_app):
    client = research_app.test_client()

    response = client.post("/research/run", json={"url": "https://youtu.be/fake123"})

    assert response.status_code == 202
    run_id = response.get_json()["run_id"]

    run = _wait_for_complete(client, run_id)
    assert run["status"] == "complete"
    assert run["stage1_output"]
    assert run["stage2_output"]
    assert run["stage3_plan_output"]
    assert run["stage4_output"]
    assert "Personalized Portfolio Observations" in run["stage5_output"]
    assert "position sizing" not in run["stage5_output"].lower()
    assert "financial advice" not in run["stage5_output"].lower()


def test_duplicate_youtube_url_reuses_existing_run(research_app):
    client = research_app.test_client()

    first = client.post("/research/run", json={"url": "https://www.youtube.com/watch?v=dupe01"})
    second = client.post("/research/run", json={"url": "https://www.youtube.com/watch?v=dupe01"})

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.get_json()["duplicate"] is True
    assert second.get_json()["run_id"] == first.get_json()["run_id"]


def test_failed_duplicate_url_creates_new_run(research_app):
    from research.jobs import create_run, get_run, update_run

    client = research_app.test_client()
    url = "https://www.youtube.com/watch?v=retry01"
    failed_run_id = create_run(url)
    update_run(failed_run_id, status="error", error_message="boom", current_stage="stage1")

    response = client.post("/research/run", json={"url": url})

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["duplicate"] is False
    assert payload["run_id"] != failed_run_id
    assert get_run(failed_run_id)["status"] == "error"


def test_cleanup_zombie_runs_marks_queued_and_running_as_error(research_app):
    from research.db import cleanup_zombie_runs
    from research.jobs import create_run, get_run, update_run

    queued_id = create_run("https://www.youtube.com/watch?v=queued01")
    running_id = create_run("https://www.youtube.com/watch?v=running01")
    update_run(running_id, status="running", current_stage="stage2")

    updated = cleanup_zombie_runs()

    assert updated == 2
    queued = get_run(queued_id)
    running = get_run(running_id)
    assert queued["status"] == "error"
    assert running["status"] == "error"
    assert queued["error_message"] == "Pipeline interrupted by instance restart"
    assert running["error_stage"] == "stage2"


def test_markdown_report_download(research_app):
    client = research_app.test_client()
    run_id = client.post("/research/run", json={"url": "https://youtu.be/report01"}).get_json()["run_id"]
    _wait_for_complete(client, run_id)

    response = client.get(f"/research/report/{run_id}/full.md")

    assert response.status_code == 200
    assert response.mimetype == "text/markdown"
    assert b"Personalized Portfolio Observations" in response.data


def test_stage3_prompt_cap():
    from research.stages import parse_research_prompts

    markdown = "\n".join(
        f"### P{i}: Prompt {i}\nResearch prompt {i}" for i in range(1, 9)
    )

    prompts = parse_research_prompts(markdown)

    assert len(prompts) == 5
    assert prompts[0]["id"] == "P1"
    assert prompts[-1]["id"] == "P5"


def test_stage3_prefers_prompts_json():
    from research.stages import parse_research_prompts

    markdown = """
# RESEARCH PLAN

### P1 - Markdown fallback
This should not be used.

<prompts_json>
[
  {
    "id": "P0",
    "title": "Crux check",
    "is_crux": true,
    "prompt_text": "Research the single crux question.",
    "output_format": "table",
    "bayesian_update": "If X then conviction rises; if Y then conviction falls."
  }
]
</prompts_json>
"""

    prompts = parse_research_prompts(markdown)

    assert prompts == [{"id": "P0", "title": "Crux check", "prompt": "Research the single crux question."}]


def test_stage1_transcript_preparation_keeps_short_transcripts():
    from research.stages import prepare_stage1_transcript

    transcript = "short transcript with NVDA signal"

    assert prepare_stage1_transcript(transcript, max_words=30) == transcript


def test_stage1_transcript_preparation_bounds_long_transcripts():
    from research.stages import prepare_stage1_transcript

    transcript = " ".join(f"word{i}" for i in range(120))

    prepared = prepare_stage1_transcript(transcript, max_words=30)

    assert "Transcript excerpted for Stage 1 latency" in prepared
    assert "## Beginning Excerpt" in prepared
    assert "## Middle Excerpt" in prepared
    assert "## Ending Excerpt" in prepared
    assert "word0" in prepared
    assert "word60" in prepared
    assert "word119" in prepared
    assert "word30" not in prepared


def test_portfolio_fetcher_uses_self_base_url(monkeypatch):
    calls = {}

    def fake_get(url, headers=None, timeout=None):
        calls["url"] = url
        calls["headers"] = headers

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"stocks": [{"ticker": "TSLA"}], "watchlist": []}

        return Response()

    monkeypatch.setenv("SELF_BASE_URL", "https://investment-tracker-r8lm.onrender.com/")
    monkeypatch.setenv("PORTFOLIO_EXPORT_TOKEN", "secret-token")
    monkeypatch.setattr("research.enrichers.portfolio_fetcher.requests.get", fake_get)

    from research.enrichers.portfolio_fetcher import fetch_portfolio_snapshot

    payload = fetch_portfolio_snapshot()

    assert calls["url"] == "https://investment-tracker-r8lm.onrender.com/api/portfolio/export"
    assert calls["headers"]["Authorization"] == "Bearer secret-token"
    assert payload["stocks"][0]["ticker"] == "TSLA"


def _wait_for_complete(client, run_id):
    deadline = time.time() + 5
    last = None
    while time.time() < deadline:
        last = client.get(f"/research/runs/{run_id}").get_json()
        if last["status"] in {"complete", "error"}:
            return last
        time.sleep(0.05)
    pytest.fail(f"run {run_id} did not finish; last state: {last}")
