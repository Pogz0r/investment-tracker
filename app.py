import os
import hmac
import threading
from datetime import datetime, timedelta
from typing import Optional

import yfinance as yf
import requests
from flask import Flask, render_template, request, jsonify, redirect, url_for, abort
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

_db_url = os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(_data_dir, 'portfolio.db')}")
# Render (and some other hosts) issue postgres:// URLs; SQLAlchemy requires postgresql://
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = _db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# Fail fast on bad DB connections instead of hanging gunicorn workers.
# connect_timeout is a psycopg2 kwarg; SQLite doesn't support it.
_engine_opts: dict = {"pool_pre_ping": True}
if _db_url.startswith("postgresql"):
    _engine_opts["connect_args"] = {"connect_timeout": 10}
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = _engine_opts
db = SQLAlchemy(app)

# ── Flask-Login ───────────────────────────────────────────────────────────────
login_manager = LoginManager(app)
login_manager.login_view = "login"

# ── External API config ───────────────────────────────────────────────────────
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "")
COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
FRANKFURTER_BASE_URL = "https://api.frankfurter.app"
PORTFOLIO_EXPORT_TOKEN = os.environ.get("PORTFOLIO_EXPORT_TOKEN", "")
PORTFOLIO_EXPORT_USER_EMAIL = os.environ.get("PORTFOLIO_EXPORT_USER_EMAIL", "")

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
    purchase_currency = db.Column(db.String(3), nullable=False, default="USD", server_default="USD")


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


class LiquidCash(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    label = db.Column(db.String(100), nullable=False, default="Cash on Hand")


class PortfolioHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False)
    value_usd = db.Column(db.Float)
    value_cad = db.Column(db.Float)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ── DB initialisation (background thread so gunicorn serves /health immediately)

def _init_db():
    """Create tables and run migrations. Runs in a background thread at startup
    so the gunicorn worker is ready to handle requests (especially /health)
    before the database connection is established."""
    try:
        with app.app_context():
            db.create_all()
            # Migration: add purchase_currency to existing stock rows
            try:
                with db.engine.connect() as _conn:
                    _conn.execute(db.text(
                        "ALTER TABLE stock ADD COLUMN purchase_currency VARCHAR(3) NOT NULL DEFAULT 'USD'"
                    ))
                    _conn.commit()
                print("[startup] migrated: added purchase_currency column", flush=True)
            except Exception:
                pass  # Column already exists — safe to ignore
        print("[startup] database tables ready", flush=True)
    except Exception as _db_err:
        print(f"[startup] db.create_all() failed: {_db_err}", flush=True)

threading.Thread(target=_init_db, daemon=True).start()


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


def get_watchlist_data(tickers: list[str], usd_to_cad: float, usd_to_php: float) -> list:
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
            "current_price_php": price * usd_to_php,
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


def get_exchange_rates() -> tuple:
    """Returns (usd_to_cad, usd_to_php)."""
    try:
        resp = requests.get(
            f"{FRANKFURTER_BASE_URL}/latest",
            params={"from": "USD", "to": "CAD,PHP"},
            timeout=10,
        )
        resp.raise_for_status()
        rates = resp.json()["rates"]
        return float(rates["CAD"]), float(rates["PHP"])
    except Exception as exc:
        print(f"[fx] {exc}")
        return 1.37, 55.8  # fallback rates


def _resolve_ticker(raw: str) -> Optional[str]:
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

def _portfolio_owner_for_export():
    if PORTFOLIO_EXPORT_USER_EMAIL:
        user = User.query.filter_by(email=PORTFOLIO_EXPORT_USER_EMAIL).first()
        if user:
            return user
    return User.query.order_by(User.id.asc()).first()


def _authorized_export_request():
    if not PORTFOLIO_EXPORT_TOKEN:
        abort(503, description="Portfolio export token is not configured")
    auth = request.headers.get("Authorization", "")
    token = ""
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1].strip()
    if not token:
        token = request.headers.get("X-Portfolio-Token", "").strip()
    if not token or not hmac.compare_digest(token, PORTFOLIO_EXPORT_TOKEN):
        abort(401)


def _build_portfolio_payload(uid: int, persist_history: bool = True):
    stocks = Stock.query.filter_by(user_id=uid).all()
    cryptos = Crypto.query.filter_by(user_id=uid).all()
    watchlist_items = WatchlistItem.query.filter_by(user_id=uid).all()
    goal = SavingsGoal.query.filter_by(user_id=uid).first()
    liquid_cash_entries = LiquidCash.query.filter_by(user_id=uid).all()

    stock_prices = get_stock_prices([s.ticker for s in stocks])
    crypto_prices = get_crypto_prices([c.coin_id for c in cryptos])
    usd_to_cad, usd_to_php = get_exchange_rates()

    stocks_out, total_stocks_usd = [], 0.0
    for s in stocks:
        cp = stock_prices.get(s.ticker, 0)
        currency = s.purchase_currency or "USD"

        if currency == "CAD":
            cp_cad = cp
            cp_usd = cp / usd_to_cad if usd_to_cad else 0
            avg_cad = s.avg_purchase_price
            avg_usd = s.avg_purchase_price / usd_to_cad if usd_to_cad else 0
        else:
            cp_usd = cp
            cp_cad = cp * usd_to_cad
            avg_usd = s.avg_purchase_price
            avg_cad = s.avg_purchase_price * usd_to_cad

        cv_usd = cp_usd * s.shares
        cv_cad = cp_cad * s.shares
        pl_usd = (cp_usd - avg_usd) * s.shares
        pl_cad = (cp_cad - avg_cad) * s.shares
        avg_native = avg_cad if currency == "CAD" else avg_usd
        cp_native  = cp_cad  if currency == "CAD" else cp_usd
        pct = ((cp_native - avg_native) / avg_native * 100) if avg_native else 0

        stocks_out.append({
            "ticker": s.ticker,
            "name": s.ticker.replace(".TO", ""),
            "shares": s.shares,
            "avg_purchase_price": s.avg_purchase_price,
            "purchase_currency": currency,
            "current_price": cp_cad if currency == "CAD" else cp_usd,
            "current_value_usd": cv_usd,
            "current_value_cad": cv_cad,
            "profit_loss_usd": pl_usd,
            "profit_loss_cad": pl_cad,
            "percent_change": pct,
            "type": "stock",
        })
        total_stocks_usd += cv_usd

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

    # Liquid cash is stored in CAD; convert to USD
    total_liquid_cad = sum(e.amount for e in liquid_cash_entries)
    total_liquid_usd = total_liquid_cad / usd_to_cad if usd_to_cad else 0

    total_usd = total_stocks_usd + total_crypto_usd + total_liquid_usd
    total_cad = total_usd * usd_to_cad
    total_php = total_usd * usd_to_php
    watchlist_out = get_watchlist_data([w.ticker for w in watchlist_items], usd_to_cad, usd_to_php)
    now = datetime.utcnow()

    if persist_history and total_usd > 0:
        last = (PortfolioHistory.query
                .filter_by(user_id=uid)
                .order_by(PortfolioHistory.timestamp.desc())
                .first())
        if not last or (now - last.timestamp).total_seconds() >= 3600:
            db.session.add(PortfolioHistory(user_id=uid, timestamp=now, value_usd=total_usd, value_cad=total_cad))
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

    return {
        "stocks": stocks_out,
        "crypto": crypto_out,
        "watchlist": watchlist_out,
        "liquid_cash": [
            {"id": e.id, "amount": e.amount, "label": e.label}
            for e in liquid_cash_entries
        ],
        "total_usd": total_usd,
        "total_cad": total_cad,
        "total_php": total_php,
        "total_stocks_usd": total_stocks_usd,
        "total_stocks_cad": total_stocks_usd * usd_to_cad,
        "total_crypto_usd": total_crypto_usd,
        "total_crypto_cad": total_crypto_usd * usd_to_cad,
        "total_liquid_usd": total_liquid_usd,
        "total_liquid_cad": total_liquid_cad,
        "usd_to_cad": usd_to_cad,
        "usd_to_php": usd_to_php,
        "savings_goal": {
            "target": goal.target if goal else 0,
            "currency": goal.currency if goal else "USD",
        },
        "portfolio_history": [
            {"timestamp": h.timestamp.isoformat(), "value_usd": h.value_usd, "value_cad": h.value_cad}
            for h in history
        ],
        "last_updated": now.isoformat(),
    }


@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/api/portfolio")
@login_required
def get_portfolio():
    return jsonify(_build_portfolio_payload(current_user.id, persist_history=True))


@app.route("/api/portfolio/export")
def export_portfolio():
    _authorized_export_request()
    owner = _portfolio_owner_for_export()
    if not owner:
        abort(404, description="No portfolio owner found")
    payload = _build_portfolio_payload(owner.id, persist_history=False)
    payload["owner"] = {"name": owner.name}
    return jsonify(payload)


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

    purchase_currency = "CAD" if ticker.endswith(".TO") else "USD"
    db.session.add(Stock(user_id=current_user.id, ticker=ticker, shares=shares,
                         avg_purchase_price=avg_price, purchase_currency=purchase_currency))
    db.session.commit()
    return jsonify({"message": f"{ticker.replace('.TO', '')} added successfully",
                    "purchase_currency": purchase_currency})


@app.route("/api/stocks/<ticker>", methods=["PUT"])
@login_required
def update_stock(ticker):
    stock = Stock.query.filter_by(user_id=current_user.id, ticker=ticker.upper()).first()
    if not stock:
        return jsonify({"error": f"{ticker} not found"}), 404

    body = request.json or {}
    try:
        shares = float(body.get("shares", stock.shares))
        avg_price = float(body.get("avg_purchase_price", stock.avg_purchase_price))
    except (TypeError, ValueError):
        return jsonify({"error": "Shares and price must be numbers"}), 400

    if shares <= 0 or avg_price <= 0:
        return jsonify({"error": "Shares and purchase price must be greater than 0"}), 400

    stock.shares = shares
    stock.avg_purchase_price = avg_price
    db.session.commit()
    return jsonify({"message": f"{ticker} updated successfully"})


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


@app.route("/api/crypto/<coin_id>", methods=["PUT"])
@login_required
def update_crypto(coin_id):
    crypto = Crypto.query.filter_by(user_id=current_user.id, coin_id=coin_id.lower()).first()
    if not crypto:
        return jsonify({"error": f"{coin_id} not found"}), 404

    body = request.json or {}
    try:
        amount = float(body.get("amount", crypto.amount))
        avg_price = float(body.get("avg_purchase_price", crypto.avg_purchase_price))
    except (TypeError, ValueError):
        return jsonify({"error": "Amount and price must be numbers"}), 400

    if amount <= 0 or avg_price <= 0:
        return jsonify({"error": "Amount and purchase price must be greater than 0"}), 400

    crypto.amount = amount
    crypto.avg_purchase_price = avg_price
    db.session.commit()
    return jsonify({"message": f"{coin_id} updated successfully"})


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


# ── Liquid cash routes ────────────────────────────────────────────────────────

@app.route("/api/liquid-cash", methods=["POST"])
@login_required
def add_liquid_cash():
    body = request.json or {}
    try:
        amount = float(body.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Amount must be a number"}), 400
    label = (body.get("label") or "").strip() or "Cash on Hand"

    if amount <= 0:
        return jsonify({"error": "Amount must be greater than 0"}), 400

    db.session.add(LiquidCash(user_id=current_user.id, amount=amount, label=label))
    db.session.commit()
    return jsonify({"message": f"{label} added successfully"})


@app.route("/api/liquid-cash/<int:entry_id>", methods=["PUT"])
@login_required
def update_liquid_cash(entry_id):
    entry = LiquidCash.query.filter_by(id=entry_id, user_id=current_user.id).first()
    if not entry:
        return jsonify({"error": "Entry not found"}), 404

    body = request.json or {}
    try:
        amount = float(body.get("amount", entry.amount))
    except (TypeError, ValueError):
        return jsonify({"error": "Amount must be a number"}), 400

    if amount <= 0:
        return jsonify({"error": "Amount must be greater than 0"}), 400

    entry.amount = amount
    entry.label = (body.get("label") or "").strip() or entry.label
    db.session.commit()
    return jsonify({"message": "Entry updated successfully"})


@app.route("/api/liquid-cash/<int:entry_id>", methods=["DELETE"])
@login_required
def delete_liquid_cash(entry_id):
    entry = LiquidCash.query.filter_by(id=entry_id, user_id=current_user.id).first()
    if not entry:
        return jsonify({"error": "Entry not found"}), 404
    db.session.delete(entry)
    db.session.commit()
    return jsonify({"message": "Entry deleted"})


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
