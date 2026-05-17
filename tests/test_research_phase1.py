import os
import json
import math
import sys
import time
from types import SimpleNamespace

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


def test_retry_skips_completed_stages_and_finishes_stage5(research_app):
    from research.jobs import create_run, get_run, update_run

    client = research_app.test_client()
    run_id = create_run("https://www.youtube.com/watch?v=retry_stage5")
    update_run(
        run_id,
        status="error",
        current_stage="stage5",
        stage1_output="stage 1 saved",
        stage2_output="stage 2 saved",
        stage3_plan_output="stage 3 plan saved",
        stage3_research={"P0": {"result": "stage 3 research saved", "citations": []}},
        stage4_output="stage 4 saved",
        error_message="Claude thinking parameter failed",
        error_stage="stage5",
    )

    response = client.post(f"/research/run/{run_id}/retry")

    assert response.status_code == 202
    assert response.get_json()["resume_from"] == "stage5"
    run = get_run(run_id)
    assert run["status"] == "complete"
    assert run["stage1_output"] == "stage 1 saved"
    assert run["stage2_output"] == "stage 2 saved"
    assert run["stage3_plan_output"] == "stage 3 plan saved"
    assert run["stage4_output"] == "stage 4 saved"
    assert "Personalized Portfolio Observations" in run["stage5_output"]


def test_manual_mode_with_stage4_upload_runs_only_stage5(research_app):
    from research.jobs import get_run

    client = research_app.test_client()

    response = client.post(
        "/research/run",
        json={
            "manual": True,
            "uploaded_stages": {
                "4": "# RESEARCH CONSOLIDATION\n\n## Updated Thesis\nFake uploaded thesis."
            },
        },
    )

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["manual"] is True
    assert payload["resume_from"] == "stage5"
    run = get_run(payload["run_id"])
    assert run["status"] == "complete"
    assert run["stage1_output"] is None
    assert run["stage4_output"].startswith("# RESEARCH CONSOLIDATION")
    assert "Personalized Portfolio Observations" in run["stage5_output"]


def test_manual_mode_with_stage3_plan_only_runs_research_onward(research_app):
    from research.jobs import get_run

    client = research_app.test_client()

    stage3_plan = """# RESEARCH PLAN

<prompts_json>
[
  {
    "id": "P0",
    "title": "Uploaded crux",
    "is_crux": true,
    "prompt_text": "Find current evidence for the uploaded thesis.",
    "output_format": "table",
    "bayesian_update": "If supported -> up. If refuted -> down."
  }
]
</prompts_json>
"""
    response = client.post(
        "/research/run",
        json={
            "manual": True,
            "uploaded_stages": {
                "1": "# INVESTMENT MEMO\n\nUploaded signal memo.",
                "2": "# THEMATIC DEEP-DIVE\n\nUploaded thematic memo.",
                "3-plan": stage3_plan,
            },
        },
    )

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["resume_from"] == "stage3"
    run = get_run(payload["run_id"])
    assert run["status"] == "complete"
    assert run["stage3_plan_output"] == stage3_plan
    assert run["stage3_research"]
    assert run["stage4_output"]
    assert run["stage5_output"]


def test_manual_mode_requires_at_least_one_upload_or_url(research_app):
    client = research_app.test_client()

    response = client.post("/research/run", json={"manual": True, "uploaded_stages": {}})

    assert response.status_code == 400


def test_manual_mode_with_stage2_upload_resumes_from_stage3(research_app):
    from research.jobs import get_run

    client = research_app.test_client()

    response = client.post(
        "/research/run",
        json={
            "manual": True,
            "uploaded_stages": {
                "1": "# INVESTMENT MEMO\n\nUploaded signal memo.",
                "2": "# THEMATIC DEEP-DIVE\n\nUploaded thematic memo.",
            },
        },
    )

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["resume_from"] == "stage3"
    run = get_run(payload["run_id"])
    assert run["status"] == "complete"
    assert run["stage1_output"].startswith("# INVESTMENT MEMO")
    assert run["stage2_output"].startswith("# THEMATIC DEEP-DIVE")
    assert run["stage3_plan_output"]
    assert run["stage4_output"]
    assert run["stage5_output"]


def test_retry_stage3_preserves_existing_plan_and_runs_missing_research(research_app):
    from research.jobs import create_run, get_run, update_run

    client = research_app.test_client()
    stage3_plan = """# RESEARCH PLAN

<prompts_json>
[
  {
    "id": "P0",
    "title": "Preserved crux",
    "is_crux": true,
    "prompt_text": "Find current evidence for the preserved plan.",
    "output_format": "table",
    "bayesian_update": "If supported -> up. If refuted -> down."
  }
]
</prompts_json>
"""
    run_id = create_run("https://www.youtube.com/watch?v=retry_stage3")
    update_run(
        run_id,
        status="error",
        current_stage="stage3",
        stage1_output="stage 1 saved",
        stage2_output="stage 2 saved",
        stage3_plan_output=stage3_plan,
        error_message="Pipeline interrupted by instance restart",
        error_stage="stage3",
    )

    response = client.post(f"/research/run/{run_id}/retry")

    assert response.status_code == 202
    assert response.get_json()["resume_from"] == "stage3"
    run = get_run(run_id)
    assert run["status"] == "complete"
    assert run["stage1_output"] == "stage 1 saved"
    assert run["stage2_output"] == "stage 2 saved"
    assert run["stage3_plan_output"] == stage3_plan
    assert run["stage3_research"]
    assert run["stage4_output"]
    assert run["stage5_output"]


def test_retry_clears_stale_active_lock_and_starts(research_app):
    from research.jobs import create_run, get_run, mark_active, update_run

    client = research_app.test_client()
    run_id = create_run("https://www.youtube.com/watch?v=stale_retry_lock")
    update_run(
        run_id,
        status="error",
        current_stage="stage5",
        stage1_output="stage 1 saved",
        stage2_output="stage 2 saved",
        stage3_plan_output="stage 3 plan saved",
        stage3_research={"P0": {"result": "stage 3 research saved", "citations": []}},
        stage4_output="stage 4 saved",
        error_message="Pipeline interrupted by instance restart",
        error_stage="stage5",
    )
    assert mark_active(run_id) is True

    response = client.post(f"/research/run/{run_id}/retry")

    assert response.status_code == 202
    run = get_run(run_id)
    assert run["status"] == "complete"
    assert "Personalized Portfolio Observations" in run["stage5_output"]


def test_latest_run_state_returns_most_recent_run(research_app):
    client = research_app.test_client()

    first = client.post("/research/run", json={"url": "https://youtu.be/latest01"}).get_json()["run_id"]
    second = client.post("/research/run", json={"url": "https://youtu.be/latest02"}).get_json()["run_id"]
    assert first != second

    response = client.get("/research/runs/latest")

    assert response.status_code == 200
    assert response.get_json()["id"] == second


def test_stage3_research_report_download(research_app):
    client = research_app.test_client()
    run_id = client.post("/research/run", json={"url": "https://youtu.be/stage3report"}).get_json()["run_id"]
    _wait_for_complete(client, run_id)

    response = client.get(f"/research/report/{run_id}/stage3-research.md")

    assert response.status_code == 200
    assert response.mimetype == "text/markdown"
    text = response.get_data(as_text=True)
    assert "STAGE 3 RESEARCH RESULTS" in text
    assert "P0" in text


def test_claude_thinking_uses_adaptive_output_config(monkeypatch):
    calls = {}

    class Messages:
        def create(self, **kwargs):
            calls.update(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")])

    class FakeAnthropic:
        def __init__(self):
            self.messages = Messages()

    monkeypatch.setenv("RESEARCH_LIVE_MODE", "true")
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic))

    from research.models import claude_client

    output = claude_client.generate(
        prompt="screen names",
        model="claude-opus-4-7",
        thinking=True,
        system="system prompt",
    )

    assert output == "ok"
    assert calls["thinking"] == {"type": "adaptive"}
    assert calls["output_config"] == {"effort": "high"}
    assert "budget_tokens" not in calls["thinking"]


def test_gemini_client_handles_thinking_level_for_v3_models(monkeypatch):
    calls = []

    class ThinkingConfig:
        def __init__(self, thinking_level=None):
            self.thinking_level = thinking_level

    class GenerateContentConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class Models:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text="gemini ok")

    class FakeGeminiClient:
        def __init__(self, api_key=None):
            self.api_key = api_key
            self.models = Models()

    types_mod = SimpleNamespace(
        ThinkingConfig=ThinkingConfig,
        GenerateContentConfig=GenerateContentConfig,
    )
    genai_mod = SimpleNamespace(Client=FakeGeminiClient, types=types_mod)
    monkeypatch.setenv("RESEARCH_LIVE_MODE", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setitem(sys.modules, "google", SimpleNamespace(genai=genai_mod))
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_mod)

    from research.models import gemini_client

    gemini_client._client = None
    output = gemini_client.generate(
        prompt="extract",
        model="gemini-3.1-pro-preview",
        system="system",
        thinking_level="low",
    )

    assert output == "gemini ok"
    config_obj = calls[-1]["config"]
    assert "temperature" not in config_obj.kwargs
    assert config_obj.kwargs["thinking_config"].thinking_level == "low"

    gemini_client.generate(prompt="extract", model="gemini-2.5-pro", system="system")
    config_obj = calls[-1]["config"]
    assert config_obj.kwargs["temperature"] == 0.3
    assert "thinking_config" not in config_obj.kwargs


def test_openai_client_uses_reasoning_effort_for_gpt5(monkeypatch):
    calls = {}

    class Completions:
        def create(self, **kwargs):
            calls.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="openai ok"))]
            )

    class FakeOpenAI:
        def __init__(self, api_key=None):
            self.api_key = api_key
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setenv("RESEARCH_LIVE_MODE", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))

    from research.models import openai_client

    openai_client._client = None
    output = openai_client.generate(
        prompt="plan",
        model="gpt-5.5",
        system="system",
        reasoning_effort="medium",
    )

    assert output == "openai ok"
    assert calls["reasoning_effort"] == "medium"
    assert calls["max_completion_tokens"] == 8192
    assert calls["messages"][0] == {"role": "system", "content": "system"}


def test_yfinance_data_is_postgres_jsonb_safe():
    """yfinance can return NaN for missing fields. These must become None."""
    from research.enrichers.yfinance_enricher import _sanitize_for_json

    raw = {
        "AAPL": {
            "price": 175.50,
            "year_high": float("nan"),
            "year_low": float("inf"),
            "nested": [{"beta": -float("inf")}],
            "market_cap": 2.5e12,
        }
    }

    cleaned = _sanitize_for_json(raw)
    serialized = json.dumps(cleaned, allow_nan=False)

    assert "NaN" not in serialized
    assert "Infinity" not in serialized
    assert cleaned["AAPL"]["year_high"] is None
    assert cleaned["AAPL"]["year_low"] is None
    assert cleaned["AAPL"]["nested"][0]["beta"] is None
    assert cleaned["AAPL"]["price"] == 175.50


def test_update_run_sanitizes_jsonb_fields(research_app):
    from research.jobs import create_run, get_run, update_run

    run_id = create_run("https://www.youtube.com/watch?v=jsonb_nan")

    update_run(
        run_id,
        live_market_data={
            "NVDA": {"last_price": 900.0, "year_high": math.nan, "year_low": math.inf}
        },
    )

    market_data = get_run(run_id)["live_market_data"]
    assert market_data["NVDA"]["last_price"] == 900.0
    assert market_data["NVDA"]["year_high"] is None
    assert market_data["NVDA"]["year_low"] is None
    json.dumps(market_data, allow_nan=False)


def test_hardware_acronyms_are_not_extracted_as_tickers():
    from research.enrichers.yfinance_enricher import extract_tickers

    tickers = extract_tickers("GPU CPU TPU ASIC HBM demand benefits NVDA and AMD. IGV and ENTG remain valid.")

    assert "GPU" not in tickers
    assert "CPU" not in tickers
    assert "TPU" not in tickers
    assert "ASIC" not in tickers
    assert "HBM" not in tickers
    assert "NVDA" in tickers
    assert "AMD" in tickers
    assert "IGV" in tickers
    assert "ENTG" in tickers


def test_fetch_market_data_includes_stage5_live_fields(monkeypatch):
    from research.enrichers.yfinance_enricher import fetch_market_data

    class FastInfo:
        last_price = 125.5
        market_cap = 32000000000
        year_high = 150.0
        year_low = 90.0
        previous_close = 123.0

    class Ticker:
        fast_info = FastInfo()
        info = {
            "beta": 1.25,
            "shortPercentOfFloat": 0.034,
            "currency": "USD",
            "shortName": "Test Power Co",
        }

    class FakeYFinance:
        @staticmethod
        def Ticker(ticker):
            return Ticker()

    monkeypatch.setitem(sys.modules, "yfinance", FakeYFinance)

    data = fetch_market_data(["CEG"])

    assert data["CEG"]["last_price"] == 125.5
    assert data["CEG"]["price"] == 125.5
    assert data["CEG"]["fifty_two_week_high"] == 150.0
    assert data["CEG"]["fifty_two_week_low"] == 90.0
    assert data["CEG"]["beta"] == 1.25
    assert data["CEG"]["short_interest_pct"] == 3.4000000000000004
    assert data["CEG"]["short_name"] == "Test Power Co"


def test_stage5_refetches_tickers_from_needs_live_data_output(monkeypatch):
    from research import stages

    calls = {"fetches": [], "generations": []}

    def fake_fetch_market_data(tickers):
        calls["fetches"].append(list(tickers))
        return {
            ticker: {"last_price": 100.0, "market_cap": 1000000000, "beta": 1.0}
            for ticker in tickers
        }

    def fake_fetch_portfolio_snapshot():
        return {"stocks": [], "watchlist": []}

    def fake_generate(system, prompt, model, thinking=False):
        calls["generations"].append(prompt)
        if len(calls["generations"]) == 1:
            return "### #1 - Constellation Energy / CEG\n- **Live data:** [NEEDS LIVE DATA]"
        return "### #1 - Constellation Energy / CEG\n- **Live data:** Price $100 | Market cap $1B"

    monkeypatch.setenv("RESEARCH_LIVE_MODE", "true")
    monkeypatch.setattr(stages, "fetch_market_data", fake_fetch_market_data)
    monkeypatch.setattr(stages, "fetch_portfolio_snapshot", fake_fetch_portfolio_snapshot)
    monkeypatch.setattr(stages.claude_client, "generate", fake_generate)

    output, market_data, _portfolio = stages.run_stage5("# Stage 4 thesis without ticker")

    assert "[NEEDS LIVE DATA]" not in output
    assert "CEG" in market_data
    assert calls["fetches"] == [[], ["CEG"]]
    assert len(calls["generations"]) == 2


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
