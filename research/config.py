import os


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


RESEARCH_DATABASE_URL = os.environ.get("RESEARCH_DATABASE_URL", "sqlite:///data/research.db")
RESEARCH_LIVE_MODE = env_bool("RESEARCH_LIVE_MODE", False)

GEMINI_STAGE1_MODEL = os.environ.get("GEMINI_STAGE1_MODEL", "gemini-2.5-pro")
GEMINI_STAGE4_MODEL = os.environ.get("GEMINI_STAGE4_MODEL", "gemini-2.5-pro")
CLAUDE_STAGE2_MODEL = os.environ.get("CLAUDE_STAGE2_MODEL", "claude-opus-4-6")
CLAUDE_STAGE3_MODEL = os.environ.get("CLAUDE_STAGE3_MODEL", "claude-opus-4-6")
CLAUDE_STAGE5_MODEL = os.environ.get("CLAUDE_STAGE5_MODEL", "claude-opus-4-7")
PERPLEXITY_STAGE3_MODEL = os.environ.get("PERPLEXITY_STAGE3_MODEL", "sonar-pro")

SELF_BASE_URL = os.environ.get("SELF_BASE_URL", "").rstrip("/")
PORTFOLIO_EXPORT_TOKEN = os.environ.get("PORTFOLIO_EXPORT_TOKEN", "")

