import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from research import config
from research.enrichers.portfolio_fetcher import fetch_portfolio_snapshot
from research.enrichers.yfinance_enricher import extract_tickers, fetch_market_data
from research.models import claude_client, gemini_client, perplexity_client
from research.prompts import (
    stage1_signal,
    stage2_thematic,
    stage3_research_plan,
    stage4_consolidation,
    stage5_screen,
)
from research.utils import is_fake_mode


def run_stage1(transcript: str) -> str:
    return gemini_client.generate(
        system=stage1_signal.SYSTEM_PROMPT,
        prompt=stage1_signal.USER_TEMPLATE.format(transcript=transcript),
        model=config.GEMINI_STAGE1_MODEL,
    )


def run_stage2(stage1_output: str) -> str:
    return claude_client.generate(
        system=stage2_thematic.SYSTEM_PROMPT,
        prompt=stage2_thematic.USER_TEMPLATE.format(stage1_output=stage1_output),
        model=config.CLAUDE_STAGE2_MODEL,
    )


def run_stage3_plan(stage1_output: str, stage2_output: str) -> str:
    return claude_client.generate(
        system=stage3_research_plan.SYSTEM_PROMPT,
        prompt=stage3_research_plan.USER_TEMPLATE.format(
            stage1_output=stage1_output,
            stage2_output=stage2_output,
        ),
        model=config.CLAUDE_STAGE3_MODEL,
    )


def parse_research_prompts(markdown: str) -> list[dict]:
    json_prompts = _parse_prompts_json(markdown)
    if json_prompts:
        return json_prompts[:5]

    matches = re.finditer(
        r"###\s*(P\d+)[:\s\-—]+(?:\[.*?\]\s*)?(.+?)\n(.*?)(?=\n---\s*\n###\s*P\d+|\n###\s*P\d+[:\s\-—]+|\Z)",
        markdown or "",
        flags=re.DOTALL,
    )
    prompts = []
    for match in matches:
        prompts.append(
            {
                "id": match.group(1).strip(),
                "title": match.group(2).strip(),
                "prompt": match.group(3).strip(),
            }
        )
        if len(prompts) == 5:
            break
    if prompts:
        return prompts
    return [{"id": "P1", "title": "Fallback research prompt", "prompt": markdown[:1000]}]


def _parse_prompts_json(markdown: str) -> list[dict]:
    match = re.search(r"<prompts_json>\s*(.*?)\s*</prompts_json>", markdown or "", re.DOTALL | re.IGNORECASE)
    if not match:
        return []
    try:
        raw_items = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    prompts = []
    for item in raw_items:
        prompt_id = str(item.get("id") or f"P{len(prompts)}").strip()
        title = str(item.get("title") or prompt_id).strip()
        prompt_text = str(item.get("prompt_text") or "").strip()
        if prompt_text:
            prompts.append({"id": prompt_id, "title": title, "prompt": prompt_text})
        if len(prompts) == 5:
            break
    return prompts


def run_stage3_research(prompts: list[dict], on_prompt_done=None) -> dict:
    results = {}
    completed = 0
    with ThreadPoolExecutor(max_workers=min(5, len(prompts))) as executor:
        futures = {
            executor.submit(
                perplexity_client.research,
                prompt["prompt"],
                prompt["id"],
                prompt["title"],
            ): prompt
            for prompt in prompts
        }
        for future in as_completed(futures):
            prompt = futures[future]
            results[prompt["id"]] = future.result()
            completed += 1
            if on_prompt_done:
                on_prompt_done(prompt["id"], completed, len(prompts))
    return results


def run_stage4(stage2_output: str, research_results: dict) -> str:
    return gemini_client.generate(
        system=stage4_consolidation.SYSTEM_PROMPT,
        prompt=stage4_consolidation.USER_TEMPLATE.format(
            stage2_output=stage2_output,
            research_results=research_results,
        ),
        model=config.GEMINI_STAGE4_MODEL,
    )


def run_stage5(stage4_output: str) -> tuple[str, dict, dict]:
    if is_fake_mode():
        market_data = {
            "NVDA": {"last_price": 900.0, "market_cap": 2200000000000, "year_high": 950.0, "year_low": 390.0},
            "VRT": {"last_price": 95.0, "market_cap": 35000000000, "year_high": 110.0, "year_low": 35.0},
        }
        portfolio = {
            "stocks": [{"ticker": "TSLA"}, {"ticker": "XEQT"}],
            "watchlist": [{"ticker": "NVDA"}],
        }
        output = claude_client.generate(
            system=stage5_screen.SYSTEM_PROMPT,
            prompt=stage5_screen.USER_TEMPLATE.format(
                stage4_output=stage4_output,
                market_data=market_data,
                portfolio_snapshot=portfolio,
            ),
            model=config.CLAUDE_STAGE5_MODEL,
            thinking=True,
        )
        return enforce_safety_language(output), market_data, portfolio

    tickers = extract_tickers(stage4_output)
    market_data = fetch_market_data(tickers)
    portfolio = fetch_portfolio_snapshot()
    prompt = stage5_screen.USER_TEMPLATE.format(
        stage4_output=stage4_output,
        market_data=market_data,
        portfolio_snapshot=portfolio,
    )
    output = claude_client.generate(
        system=stage5_screen.SYSTEM_PROMPT,
        prompt=prompt,
        model=config.CLAUDE_STAGE5_MODEL,
        thinking=True,
    )
    return enforce_safety_language(output), market_data, portfolio


def enforce_safety_language(markdown: str) -> str:
    forbidden = ["position size", "allocate ", "% of portfolio", "financial advice"]
    lowered = markdown.lower()
    if any(term in lowered for term in forbidden):
        markdown += "\n\n## Safety Revision\nSizing or advisory phrasing was removed or flagged by the safety guard."
    return markdown
