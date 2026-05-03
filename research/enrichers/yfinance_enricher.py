import re


TICKER_RE = re.compile(r"\b[A-Z]{1,5}(?:\.[A-Z]{1,3})?\b")
FALSE_POSITIVES = {
    # Generic acronyms
    "AI", "API", "ML", "LLM", "NLP",
    # Financial terms
    "CAD", "USD", "EUR", "GBP", "JPY", "AUD",
    "ETF", "IPO", "GDP", "CPI", "EPS", "PE",
    "IRR", "NPV", "ROI", "ROE", "ROIC",
    "TAM", "SAM", "SOM", "ARR", "MRR",
    "EBIT", "EBITDA", "CAPEX", "OPEX", "COGS",
    "YOY", "YTD", "MTD", "QTD",
    "Q1", "Q2", "Q3", "Q4",
    # Corporate titles
    "CEO", "CFO", "CTO", "COO", "CMO", "CSO",
    # Common words that match ticker pattern
    "THE", "AND", "FOR", "NOT", "BUT", "WITH",
    "NEW", "OLD", "TOP", "KEY", "VS", "PER",
    "US", "UK", "EU", "UN", "WHO", "CDC",
    "PDF", "JSON", "CSV", "URL", "URI",
    "MD", "DR", "MR", "MS",
    "SG", "RD", "OK", "NO", "YES",
    # Research/memo terms that appear in stage outputs
    "TBD", "TBC", "NA", "NM", "NR",
    "BUY", "SELL", "HOLD",
    "HIGH", "LOW", "MID",
    "PHASE", "STAGE",
}


def extract_tickers(*documents: str) -> list[str]:
    tickers = []
    for document in documents:
        for match in TICKER_RE.findall(document or ""):
            if match not in FALSE_POSITIVES and match not in tickers:
                tickers.append(match)
    return tickers[:12]


def fetch_market_data(tickers: list[str]) -> dict:
    if not tickers:
        return {}
    try:
        import yfinance as yf
    except ImportError:
        return {ticker: {"error": "yfinance not installed"} for ticker in tickers}

    data = {}
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).fast_info
            data[ticker] = {
                "last_price": _safe_float(getattr(info, "last_price", None)),
                "market_cap": _safe_float(getattr(info, "market_cap", None)),
                "year_high": _safe_float(getattr(info, "year_high", None)),
                "year_low": _safe_float(getattr(info, "year_low", None)),
                "previous_close": _safe_float(getattr(info, "previous_close", None)),
            }
        except Exception as exc:
            data[ticker] = {"error": str(exc)}
    return data


def _safe_float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
