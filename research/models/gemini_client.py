import os

from research import config

_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai

        _client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _client


def generate(prompt: str, model: str, system: str = "", thinking_level: str = "low") -> str:
    if not config.is_live_mode():
        return _fake_response(prompt, model)
    try:
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("google-genai is required for live Gemini calls") from exc

    client = _get_client()
    config_kwargs = {
        "system_instruction": system if system else None,
        "max_output_tokens": 8192,
    }
    if "gemini-3" in model:
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=thinking_level)
    else:
        config_kwargs["temperature"] = 0.3

    response = client.models.generate_content(
        model=model,
        config=types.GenerateContentConfig(
            **{key: value for key, value in config_kwargs.items() if value is not None}
        ),
        contents=prompt,
    )
    return response.text or ""


def _fake_response(prompt: str, model: str) -> str:
    if "RESEARCH RESULTS" in prompt.upper():
        return """# CONSOLIDATED RESEARCH REPORT

## Sourced Findings
- Perplexity research indicates data center capacity and power availability are recurring bottlenecks.

## Model Assumptions
- Assumes current podcast themes are relevant to public equity exposure.

## Investment Thesis
AI infrastructure remains investable, but the best Phase 1 action is watchlist refinement rather than position sizing.
"""
    return """# INVESTMENT MEMO - Fake Podcast

## TL;DR
1. AI infrastructure demand remains the key actionable signal. [C:4/5 | S:4/5] - Supports watchlist review.
2. Grid capacity is an emerging bottleneck. [C:3/5 | S:4/5] - Deep-dive utilities and power equipment.
3. Valuation discipline matters despite strong secular demand. [C:4/5 | S:3/5] - Avoid hype-only names.

## Recommended Actions
- Watchlist additions: NVDA, VRT, XLU
- Deep-dive triggers: data center power constraints
"""
