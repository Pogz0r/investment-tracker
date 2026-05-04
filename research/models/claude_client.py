from research import config


def generate(prompt: str, model: str, thinking: bool = False, system: str = "") -> str:
    if not config.is_live_mode():
        return _fake_response(model)
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("anthropic is required for live Claude calls") from exc

    client = anthropic.Anthropic()
    kwargs = {
        "model": model,
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    if thinking:
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": "high"}
    response = client.messages.create(**kwargs)
    return "\n".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    )


def _fake_response(model: str) -> str:
    if model == config.CLAUDE_STAGE3_MODEL:
        return """# RESEARCH PLAN

### P1: AI infrastructure power constraints
Research recent evidence on power bottlenecks for AI data centers.

### P2: Data center equipment beneficiaries
Research listed companies exposed to data center power and cooling capex.

### P3: Semiconductor valuation risk
Research current market concerns about AI semiconductor valuation.
"""
    if model == config.CLAUDE_STAGE5_MODEL:
        return """# EQUITY SCREEN

## Personalized Portfolio Observations
- Your current holdings and watchlist should be compared against AI infrastructure exposure before adding new names.
- Watchlist language only: consider adding names for monitoring, holding current winners, or trimming thesis drift. No allocation guidance is provided.

## Watchlist Candidates
| Ticker | Rationale | Action Language |
|---|---|---|
| NVDA | AI accelerator exposure | Watchlist |
| VRT | Data center power and thermal exposure | Watchlist |

## Safety Check
No sizing, allocation percentage, or advisory phrasing included.
"""
    return """# THEMATIC DEEP-DIVE

## Thesis
AI infrastructure demand is shifting the bottleneck from chips alone to energy, cooling, and deployment capacity.

## Beneficiary Map
| Tier | Names | Rationale |
|---|---|---|
| Direct | NVDA, AMD | Accelerator demand |
| Picks-and-shovels | VRT, ETN | Power and thermal infrastructure |

## Risk Register
- Valuation compression
- Supply chain constraints
- Demand normalization
"""
