import os
from datetime import datetime, timedelta

import yfinance as yf
import requests
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user,
)
from authlib.integrations.flask_client import OAuth
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
# Trust X-Forwarded-Proto from Render's proxy so url_for generates https://
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-insecure-key-change-me")

# ── Database ─────────────────────────────────────────────────────────────────
_data_dir = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
os.makedirs(_data_dir, exist_ok=True)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", f"sqlite:///{os.path.join(_data_dir, 'portfolio.db')}"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# ── Flask-Login ───────────────────────────────────────────────────────────────
login_manager = LoginManager(app)
login_manager.login_view = "login"

# ── External API config ───────────────────────────────────────────────────────
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "")
COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
FRANKFURTER_BASE_URL = "https://api.frankfurter.app"

# ── Google OAuth ──────────────────────────────────────────────────────────────
oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


# ── Models ────────────────────────────────────────────────────────────────────

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    google_id = db.Column(db.String(128), unique=True, nullable=False)
    name = db.Column(db.String(256))
    email = db.Column(db.String(256))
    picture = db.Column(db.String(512))


class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    ticker = db.Column(db.String(20), nullable=False)
    shares = db.Column(db.Float, nullable=False)
    avg_purchase_price = db.Column(db.Float, nullable=False)


class Crypto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    coin_id = db.Column(db.String(100), nullable=False)
    coin_name = db.Column(db.String(100))
    amount = db.Column(db.Float, nullable=False)
    avg_purchase_price = db.Column(db.Float, nullable=False)


class WatchlistItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    ticker = db.Column(db.String(20), nullable=False)


class SavingsGoal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True)
    target = db.Column(db.Float, default=0.0)
    currency = db.Column(db.String(3), default="USD")


class PortfolioHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False)
    value_usd = db.Column(db.Float)
    value_cad = db.Column(db.Float)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# Create tables on startup (idempotent)
with app.app_context():
    db.create_all()


# ── Price helpers ─────────────────────────────────────────────────────────────

def get_stock_prices(tickers: list[str]) -> dict:
    prices = {}
    for ticker in tickers:
        try:
            prices[ticker] = float(yf.Ticker(ticker).fast_info.last_price or 0)
        except Exception as exc:
            print(f"[stocks] {ticker}: {exc}")
            prices[ticker] = 0.0
    return prices


def get_watchlist_data(tickers: list[str], usd_to_cad: float) -> list:
    result = []
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).fast_info
            price = float(info.last_price or 0)
            prev = float(info.previous_close or 0)
            day_change = price - prev
            day_change_pct = (day_change / prev * 100) if prev else 0.0
        except Exception as exc:
            print(f"[watchlist] {ticker}: {exc}")
            price, day_change, day_change_pct = 0.0, 0.0, 0.0
        result.append({
            "ticker": ticker,
            "name": ticker.replace(".TO", ""),
            "current_price": price,
            "current_price_cad": price * usd_to_cad,
            "day_change": day_change,
            "day_change_pct": day_change_pct,
        })
    return result


def get_crypto_prices(coin_ids: list[str]) -> dict:
    if not coin_ids:
        return {}
    headers = {"x-cg-demo-api-key": COINGECKO_API_KEY} if COINGECKO_API_KEY else {}
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
        return 1.37


def _resolve_ticker(raw: str) -> str | None:
    """Return valid yfinance ticker, auto-retrying with .TO suffix for TSX stocks."""
    def valid(t):
        try:
            return bool(yf.Ticker(t).fast_info.last_price)
        except Exception:
            return False
    if valid(raw):
        return raw
    tsx = raw if raw.endswith(".TO") else raw + ".TO"
    return tsx if valid(tsx) else None


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/login")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/auth/google")
def auth_google():
    redirect_uri = url_for("auth_callback", _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/auth/callback")
def auth_callback():
    token = google.authorize_access_token()
    userinfo = token.get("userinfo")
    if not userinfo:
        return redirect(url_for("login"))

    google_id = userinfo["sub"]
    user = User.query.filter_by(google_id=google_id).first()
    if not user:
        user = User(
            google_id=google_id,
            name=userinfo.get("name"),
            email=userinfo.get("email"),
            picture=userinfo.get("picture"),
        )
        db.session.add(user)
    else:
        # Refresh name/picture in case they changed
        user.name = userinfo.get("name")
        user.picture = userinfo.get("picture")
    db.session.commit()
    login_user(user)
    return redirect(url_for("index"))


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/api/portfolio")
@login_required
def get_portfolio():
    uid = current_user.id
    stocks = Stock.query.filter_by(user_id=uid).all()
    cryptos = Crypto.query.filter_by(user_id=uid).all()
    watchlist_items = WatchlistItem.query.filter_by(user_id=uid).all()
    goal = SavingsGoal.query.filter_by(user_id=uid).first()

    stock_prices = get_stock_prices([s.ticker for s in stocks])
    crypto_prices = get_crypto_prices([c.coin_id for c in cryptos])
    usd_to_cad = get_exchange_rate()

    # --- stocks ---
    stocks_out, total_stocks_usd = [], 0.0
    for s in stocks:
        cp = stock_prices.get(s.ticker, 0)
        cv = cp * s.shares
        pl = cv - s.avg_purchase_price * s.shares
        pct = ((cp - s.avg_purchase_price) / s.avg_purchase_price * 100) if s.avg_purchase_price else 0
        stocks_out.append({
            "ticker": s.ticker,
            "name": s.ticker.replace(".TO", ""),
            "shares": s.shares,
            "avg_purchase_price": s.avg_purchase_price,
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
    crypto_out, total_crypto_usd = [], 0.0
    for c in cryptos:
        cp = crypto_prices.get(c.coin_id, 0)
        cv = cp * c.amount
        pl = cv - c.avg_purchase_price * c.amount
        pct = ((cp - c.avg_purchase_price) / c.avg_purchase_price * 100) if c.avg_purchase_price else 0
        crypto_out.append({
            "coin_id": c.coin_id,
            "name": (c.coin_name or c.coin_id).title(),
            "amount": c.amount,
            "avg_purchase_price": c.avg_purchase_price,
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
    watchlist_out = get_watchlist_data([w.ticker for w in watchlist_items], usd_to_cad)

    # --- history snapshot (max once per hour per user) ---
    now = datetime.utcnow()
    if total_usd > 0:
        last = (PortfolioHistory.query
                .filter_by(user_id=uid)
                .order_by(PortfolioHistory.timestamp.desc())
                .first())
        if not last or (now - last.timestamp).total_seconds() >= 3600:
            db.session.add(PortfolioHistory(user_id=uid, timestamp=now,
                                             value_usd=total_usd, value_cad=total_cad))
            cutoff = now - timedelta(days=90)
            PortfolioHistory.query.filter(
                PortfolioHistory.user_id == uid,
                PortfolioHistory.timestamp < cutoff,
            ).delete()
            db.session.commit()

    history = (PortfolioHistory.query
               .filter_by(user_id=uid)
               .order_by(PortfolioHistory.timestamp)
               .all())

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
        "savings_goal": {
            "target": goal.target if goal else 0,
            "currency": goal.currency if goal else "USD",
        },
        "portfolio_history": [
            {"timestamp": h.timestamp.isoformat(), "value_usd": h.value_usd, "value_cad": h.value_cad}
            for h in history
        ],
        "last_updated": now.isoformat(),
    })


# ── Stock routes ──────────────────────────────────────────────────────────────

@app.route("/api/stocks", methods=["POST"])
@login_required
def add_stock():
    body = request.json or {}
    raw = body.get("ticker", "").upper().strip()
    try:
        shares = float(body.get("shares", 0))
        avg_price = float(body.get("avg_purchase_price", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Shares and price must be numbers"}), 400

    if not raw or shares <= 0 or avg_price <= 0:
        return jsonify({"error": "Ticker, shares > 0, and purchase price > 0 are required"}), 400

    ticker = _resolve_ticker(raw)
    if not ticker:
        return jsonify({"error": f"Could not find ticker '{raw}'. Check the symbol and try again."}), 400

    if Stock.query.filter_by(user_id=current_user.id, ticker=ticker).first():
        return jsonify({"error": f"{ticker.replace('.TO', '')} is already in your portfolio"}), 409

    db.session.add(Stock(user_id=current_user.id, ticker=ticker, shares=shares, avg_purchase_price=avg_price))
    db.session.commit()
    return jsonify({"message": f"{ticker.replace('.TO', '')} added successfully"})


@app.route("/api/stocks/<ticker>", methods=["DELETE"])
@login_required
def remove_stock(ticker):
    Stock.query.filter_by(user_id=current_user.id, ticker=ticker.upper()).delete()
    db.session.commit()
    return jsonify({"message": f"{ticker} removed"})


# ── Crypto routes ─────────────────────────────────────────────────────────────

@app.route("/api/crypto", methods=["POST"])
@login_required
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

    headers = {"x-cg-demo-api-key": COINGECKO_API_KEY} if COINGECKO_API_KEY else {}
    try:
        resp = requests.get(f"{COINGECKO_BASE_URL}/simple/price",
                            params={"ids": coin_id, "vs_currencies": "usd"},
                            headers=headers, timeout=10)
        if coin_id not in resp.json():
            return jsonify({"error": f"Coin '{coin_id}' not found on CoinGecko. Use the coin ID (e.g. 'bitcoin')."}), 400
    except Exception:
        return jsonify({"error": "Could not validate coin. Check your API key or try again later."}), 400

    if Crypto.query.filter_by(user_id=current_user.id, coin_id=coin_id).first():
        return jsonify({"error": f"{coin_id} is already in your portfolio"}), 409

    db.session.add(Crypto(user_id=current_user.id, coin_id=coin_id, coin_name=coin_name,
                          amount=amount, avg_purchase_price=avg_price))
    db.session.commit()
    return jsonify({"message": f"{coin_name} added successfully"})


@app.route("/api/crypto/<coin_id>", methods=["DELETE"])
@login_required
def remove_crypto(coin_id):
    Crypto.query.filter_by(user_id=current_user.id, coin_id=coin_id).delete()
    db.session.commit()
    return jsonify({"message": f"{coin_id} removed"})


# ── Savings goal route ────────────────────────────────────────────────────────

@app.route("/api/savings-goal", methods=["POST"])
@login_required
def update_savings_goal():
    body = request.json or {}
    try:
        target = float(body.get("target", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Target must be a number"}), 400
    currency = body.get("currency", "USD")

    goal = SavingsGoal.query.filter_by(user_id=current_user.id).first()
    if goal:
        goal.target, goal.currency = target, currency
    else:
        db.session.add(SavingsGoal(user_id=current_user.id, target=target, currency=currency))
    db.session.commit()
    return jsonify({"message": "Savings goal updated"})


# ── Watchlist routes ──────────────────────────────────────────────────────────

@app.route("/api/watchlist", methods=["POST"])
@login_required
def add_watchlist():
    body = request.json or {}
    raw = body.get("ticker", "").upper().strip()
    if not raw:
        return jsonify({"error": "Ticker is required"}), 400

    ticker = _resolve_ticker(raw)
    if not ticker:
        return jsonify({"error": f"Could not find ticker '{raw}'. Check the symbol and try again."}), 400

    if WatchlistItem.query.filter_by(user_id=current_user.id, ticker=ticker).first():
        return jsonify({"error": f"{ticker.replace('.TO', '')} is already in your watchlist"}), 409

    db.session.add(WatchlistItem(user_id=current_user.id, ticker=ticker))
    db.session.commit()
    return jsonify({"message": f"{ticker.replace('.TO', '')} added to watchlist"})


@app.route("/api/watchlist/<ticker>", methods=["DELETE"])
@login_required
def remove_watchlist(ticker):
    WatchlistItem.query.filter_by(user_id=current_user.id, ticker=ticker.upper()).delete()
    db.session.commit()
    return jsonify({"message": f"{ticker} removed from watchlist"})


# ── Health check (no auth required) ──────────────────────────────────────────

@app.route("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
