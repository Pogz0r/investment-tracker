import os

import requests

from research.enrichers.yfinance_enricher import _sanitize_for_json


def fetch_portfolio_snapshot() -> dict:
    base_url = os.environ.get("SELF_BASE_URL", "").rstrip("/")
    token = os.environ.get("PORTFOLIO_EXPORT_TOKEN", "")
    if not base_url:
        return {"error": "SELF_BASE_URL is not configured"}
    if not token:
        return {"error": "PORTFOLIO_EXPORT_TOKEN is not configured"}

    response = requests.get(
        f"{base_url}/api/portfolio/export",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    response.raise_for_status()
    return _sanitize_for_json(response.json())
