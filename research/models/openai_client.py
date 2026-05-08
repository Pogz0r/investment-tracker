import os

from research import config

_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def generate(prompt: str, model: str, system: str = "", reasoning_effort: str = "medium") -> str:
    if not config.is_live_mode():
        return _fake_response(model)

    client = _get_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    kwargs = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": 8192,
    }
    if "gpt-5" in model:
        kwargs["reasoning_effort"] = reasoning_effort

    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content or ""


def _fake_response(model: str) -> str:
    return f"""# RESEARCH PLAN - Fake OpenAI Response from {model}

## Thesis Being Tested
AI infrastructure demand is creating investable bottlenecks.

## Prompt Bank

### P0 - CRUX Power bottleneck
Research whether power availability is the binding constraint for AI data center growth.

<prompts_json>
[
  {{
    "id": "P0",
    "title": "Power bottleneck",
    "is_crux": true,
    "prompt_text": "Research whether power availability is the binding constraint for AI data center growth.",
    "output_format": "table",
    "bayesian_update": "If power is binding conviction rises; if not conviction falls."
  }}
]
</prompts_json>
"""
