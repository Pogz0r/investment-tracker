import os
import json
from datetime import datetime, timedelta

import yfinance as yf
import requests
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

_data_dir = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
DATA_FILE = os.path.join(_data_dir, "portfolio.json")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")
COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
FRANKFURTER_BASE_URL = "https://api.frankfurter.dev"


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def load_portfolio():
    if not os.path.exists(DATA_FILE):
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        default = {"stocks": [], "crypto": [], "watchlist": [], "savings_goal": {"target": 0, "currency": "USD"}, "portfolio_history": []}
        save_portfolio(default)
        return default
    with open(DATA_FILE, "r") as f:
        return json.load(f)


def save_portfolio(data):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Price fetchers
# ---------------------------------------------------------------------------

def get_stock_prices(tickers: list[str]) -> dict:
    prices = {}
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).fast_info
            prices[ticker] = float(info.last_price or 0)
        except Exception as exc:
            print(f"[stocks] {ticker}: {exc}")
            prices[ticker] = 0.0
    return prices


def get_crypto_prices(coin_ids: list[str]) -> dict:
    if not coin_ids:
        return {}
    headers = {}
    if COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY
    try:
        resp = requests.get(
            f"{COINGECKO_BASE_URL}/simple/price",
            params={"ids": ",".join(coin_ids), "vs_currencies": "usd"},
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        raw = resp.json()
        return {cid: raw[cid]["usd"] for cid in coin_ids if cid in raw}
    except Exception as exc:
        print(f"[crypto] {exc}")
        return {}


def get_watchlist_data(tickers: list[str], usd_to_cad: float) -> list:
    result = []
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).fast_info
            price = float(info.last_price or 0)
            prev = float(info.previous_close or 0)
            day_change = price - prev
            day_change_pct = (day_change / prev * 100) if prev else 0
        except Exception as exc:
            print(f"[watchlist] {ticker}: {exc}")
            price, prev, day_change, day_change_pct = 0.0, 0.0, 0.0, 0.0
        result.append({
            "ticker": ticker,
            "name": ticker.replace(".TO", ""),
            "current_price": price,
            "current_price_cad": price * usd_to_cad,
            "day_change": day_change,
            "day_change_pct": day_change_pct,
        })
    return result


def get_exchange_rate() -> float:
    try:
        resp = requests.get(
            f"{FRANKFURTER_BASE_URL}/latest",
            params={"from": "USD", "to": "CAD"},
            timeout=10,
        )
        resp.raise_for_status()
        return float(resp.json()["rates"]["CAD"])
    except Exception as exc:
        print(f"[fx] {exc}")
        return 1.37  # fallback


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/portfolio")
def get_portfolio():
    portfolio = load_portfolio()

    stock_tickers = [s["ticker"] for s in portfolio["stocks"]]
    coin_ids = [c["coin_id"] for c in portfolio["crypto"]]

    stock_prices = get_stock_prices(stock_tickers)
    crypto_prices = get_crypto_prices(coin_ids)
    usd_to_cad = get_exchange_rate()

    # --- stocks ---
    stocks_out = []
    total_stocks_usd = 0.0
    for s in portfolio["stocks"]:
        cp = stock_prices.get(s["ticker"], 0)
        cv = cp * s["shares"]
        pv = s["avg_purchase_price"] * s["shares"]
        pl = cv - pv
        pct = ((cp - s["avg_purchase_price"]) / s["avg_purchase_price"] * 100) if s["avg_purchase_price"] else 0
        stocks_out.append({
            "ticker": s["ticker"],
            "name": s["ticker"].replace(".TO", ""),
            "shares": s["shares"],
            "avg_purchase_price": s["avg_purchase_price"],
            "current_price": cp,
            "current_value_usd": cv,
            "current_value_cad": cv * usd_to_cad,
            "profit_loss_usd": pl,
            "profit_loss_cad": pl * usd_to_cad,
            "percent_change": pct,
            "type": "stock",
        })
        total_stocks_usd += cv

    # --- crypto ---
    crypto_out = []
    total_crypto_usd = 0.0
    for c in portfolio["crypto"]:
        cp = crypto_prices.get(c["coin_id"], 0)
        cv = cp * c["amount"]
        pv = c["avg_purchase_price"] * c["amount"]
        pl = cv - pv
        pct = ((cp - c["avg_purchase_price"]) / c["avg_purchase_price"] * 100) if c["avg_purchase_price"] else 0
        crypto_out.append({
            "coin_id": c["coin_id"],
            "name": c.get("coin_name", c["coin_id"]).title(),
            "amount": c["amount"],
            "avg_purchase_price": c["avg_purchase_price"],
            "current_price": cp,
            "current_value_usd": cv,
            "current_value_cad": cv * usd_to_cad,
            "profit_loss_usd": pl,
            "profit_loss_cad": pl * usd_to_cad,
            "percent_change": pct,
            "type": "crypto",
        })
        total_crypto_usd += cv

    total_usd = total_stocks_usd + total_crypto_usd
    total_cad = total_usd * usd_to_cad

    # --- history snapshot (max once per hour) ---
    now = datetime.utcnow()
    history = portfolio.get("portfolio_history", [])
    if total_usd > 0:
        add_snapshot = True
        if history:
            last_ts = datetime.fromisoformat(history[-1]["timestamp"])
            if (now - last_ts).total_seconds() < 3600:
                add_snapshot = False
        if add_snapshot:
            history.append({"timestamp": now.isoformat(), "value_usd": total_usd, "value_cad": total_cad})
        cutoff = now - timedelta(days=90)
        history = [h for h in history if datetime.fromisoformat(h["timestamp"]) > cutoff]
        portfolio["portfolio_history"] = history
        save_portfolio(portfolio)

    watchlist_tickers = [w["ticker"] for w in portfolio.get("watchlist", [])]
    watchlist_out = get_watchlist_data(watchlist_tickers, usd_to_cad)

    return jsonify({
        "stocks": stocks_out,
        "crypto": crypto_out,
        "watchlist": watchlist_out,
        "total_usd": total_usd,
        "total_cad": total_cad,
        "total_stocks_usd": total_stocks_usd,
        "total_stocks_cad": total_stocks_usd * usd_to_cad,
        "total_crypto_usd": total_crypto_usd,
        "total_crypto_cad": total_crypto_usd * usd_to_cad,
        "usd_to_cad": usd_to_cad,
        "savings_goal": portfolio.get("savings_goal", {"target": 0, "currency": "USD"}),
        "portfolio_history": history,
        "last_updated": now.isoformat(),
    })


@app.route("/api/stocks", methods=["POST"])
def add_stock():
    body = request.json or {}
    ticker = body.get("ticker", "").upper().strip()
    try:
        shares = float(body.get("shares", 0))
        avg_price = float(body.get("avg_purchase_price", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Shares and price must be numbers"}), 400

    if not ticker or shares <= 0 or avg_price <= 0:
        return jsonify({"error": "Ticker, shares > 0, and purchase price > 0 are required"}), 400

    # validate ticker via yfinance; auto-retry with .TO suffix for TSX stocks
    def _valid_ticker(t):
        try:
            price = yf.Ticker(t).fast_info.last_price
            return bool(price)
        except Exception:
            return False

    if not _valid_ticker(ticker):
        tsx_ticker = ticker if ticker.endswith(".TO") else ticker + ".TO"
        if _valid_ticker(tsx_ticker):
            ticker = tsx_ticker
        else:
            return jsonify({"error": f"Could not find ticker '{ticker}'. Check the symbol and try again."}), 400

    portfolio = load_portfolio()
    if any(s["ticker"] == ticker for s in portfolio["stocks"]):
        return jsonify({"error": f"{ticker.replace('.TO', '')} is already in your portfolio"}), 409

    portfolio["stocks"].append({"ticker": ticker, "shares": shares, "avg_purchase_price": avg_price})
    save_portfolio(portfolio)
    return jsonify({"message": f"{ticker} added successfully"})


@app.route("/api/stocks/<ticker>", methods=["DELETE"])
def remove_stock(ticker):
    ticker = ticker.upper()
    portfolio = load_portfolio()
    portfolio["stocks"] = [s for s in portfolio["stocks"] if s["ticker"] != ticker]
    save_portfolio(portfolio)
    return jsonify({"message": f"{ticker} removed"})


@app.route("/api/crypto", methods=["POST"])
def add_crypto():
    body = request.json or {}
    coin_id = body.get("coin_id", "").lower().strip()
    coin_name = body.get("coin_name", coin_id).strip()
    try:
        amount = float(body.get("amount", 0))
        avg_price = float(body.get("avg_purchase_price", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Amount and price must be numbers"}), 400

    if not coin_id or amount <= 0 or avg_price <= 0:
        return jsonify({"error": "Coin ID, amount > 0, and purchase price > 0 are required"}), 400

    # validate coin via CoinGecko
    headers = {}
    if COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY
    try:
        resp = requests.get(
            f"{COINGECKO_BASE_URL}/simple/price",
            params={"ids": coin_id, "vs_currencies": "usd"},
            headers=headers,
            timeout=10,
        )
        if coin_id not in resp.json():
            return jsonify({"error": f"Coin '{coin_id}' not found on CoinGecko. Use the coin ID (e.g. 'bitcoin', 'ethereum')."}), 400
    except Exception:
        return jsonify({"error": "Could not validate coin. Check your API key or try again later."}), 400

    portfolio = load_portfolio()
    if any(c["coin_id"] == coin_id for c in portfolio["crypto"]):
        return jsonify({"error": f"{coin_id} is already in your portfolio"}), 409

    portfolio["crypto"].append({"coin_id": coin_id, "coin_name": coin_name, "amount": amount, "avg_purchase_price": avg_price})
    save_portfolio(portfolio)
    return jsonify({"message": f"{coin_name} added successfully"})


@app.route("/api/crypto/<coin_id>", methods=["DELETE"])
def remove_crypto(coin_id):
    portfolio = load_portfolio()
    portfolio["crypto"] = [c for c in portfolio["crypto"] if c["coin_id"] != coin_id]
    save_portfolio(portfolio)
    return jsonify({"message": f"{coin_id} removed"})


@app.route("/api/savings-goal", methods=["POST"])
def update_savings_goal():
    body = request.json or {}
    try:
        target = float(body.get("target", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Target must be a number"}), 400
    currency = body.get("currency", "USD")

    portfolio = load_portfolio()
    portfolio["savings_goal"] = {"target": target, "currency": currency}
    save_portfolio(portfolio)
    return jsonify({"message": "Savings goal updated"})


def _resolve_ticker(raw: str):
    """Return the yfinance-valid ticker string, auto-retrying with .TO for TSX."""
    def valid(t):
        try:
            return bool(yf.Ticker(t).fast_info.last_price)
        except Exception:
            return False
    if valid(raw):
        return raw
    tsx = raw if raw.endswith(".TO") else raw + ".TO"
    return tsx if valid(tsx) else None


@app.route("/api/watchlist", methods=["POST"])
def add_watchlist():
    body = request.json or {}
    raw = body.get("ticker", "").upper().strip()
    if not raw:
        return jsonify({"error": "Ticker is required"}), 400

    ticker = _resolve_ticker(raw)
    if not ticker:
        return jsonify({"error": f"Could not find ticker '{raw}'. Check the symbol and try again."}), 400

    portfolio = load_portfolio()
    if "watchlist" not in portfolio:
        portfolio["watchlist"] = []
    if any(w["ticker"] == ticker for w in portfolio["watchlist"]):
        return jsonify({"error": f"{ticker.replace('.TO', '')} is already in your watchlist"}), 409

    portfolio["watchlist"].append({"ticker": ticker})
    save_portfolio(portfolio)
    return jsonify({"message": f"{ticker.replace('.TO', '')} added to watchlist"})


@app.route("/api/watchlist/<ticker>", methods=["DELETE"])
def remove_watchlist(ticker):
    ticker = ticker.upper()
    portfolio = load_portfolio()
    portfolio["watchlist"] = [w for w in portfolio.get("watchlist", []) if w["ticker"] != ticker]
    save_portfolio(portfolio)
    return jsonify({"message": f"{ticker} removed from watchlist"})


@app.route("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
