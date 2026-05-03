import os

from research import config


def generate(prompt: str, model: str, system: str = "") -> str:
    if not config.is_live_mode():
        return _fake_response(prompt, model)
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("google-genai is required for live Gemini calls") from exc

    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    config_obj = types.GenerateContentConfig(
        system_instruction=system if system else None,
        temperature=0.3,
        max_output_tokens=8192,
    )
    response = client.models.generate_content(
        model=model,
        config=config_obj,
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
