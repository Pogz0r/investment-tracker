import re


TICKER_RE = re.compile(r"\b[A-Z]{1,5}(?:\.[A-Z]{1,3})?\b")
FALSE_POSITIVES = {
    "AI",
    "API",
    "CAD",
    "CEO",
    "CFO",
    "ETF",
    "GDP",
    "JSON",
    "LLM",
    "MD",
    "PDF",
    "USD",
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

