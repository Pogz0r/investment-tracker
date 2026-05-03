import os

from research import config


def research(prompt: str, prompt_id: str, title: str) -> dict:
    if not config.is_live_mode():
        return {
            "prompt_id": prompt_id,
            "title": title,
            "result": f"Fake research result for {title}. Key finding: infrastructure bottlenecks remain investable but require valuation discipline.",
            "citations": [
                {"title": "Example market research source", "url": "https://example.com/research"}
            ],
        }

    import httpx

    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        raise RuntimeError("PERPLEXITY_API_KEY is required for live Perplexity calls")
    response = httpx.post(
        "https://api.perplexity.ai/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": config.PERPLEXITY_STAGE3_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "return_citations": True,
        },
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
    citations = payload.get("citations") or []
    return {"prompt_id": prompt_id, "title": title, "result": content, "citations": citations}
