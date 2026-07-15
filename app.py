import os
import hmac
import threading
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlsplit

import yfinance as yf
import requests
from flask import Flask, render_template, request, jsonify, redirect, url_for, abort, session
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
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
if os.environ.get("RENDER"):
    app.config["SESSION_COOKIE_SECURE"] = True

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


class PriceSimulatorSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True)
    holding_keys = db.Column(db.JSON, nullable=False, default=list)


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


class IncomeEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    pay_date = db.Column(db.Date, nullable=False)
    employer = db.Column(db.String(256), nullable=False)
    gross_income = db.Column(db.Float, nullable=False)
    net_income = db.Column(db.Float, nullable=False)
    deductions = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(512), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(64), nullable=False, default="other")
    source = db.Column(db.String(32), nullable=False, default="manual")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


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

def _safe_next_url(target: Optional[str]) -> bool:
    """Allow local post-login redirects, reject external URLs."""
    if not target:
        return False
    ref = urlsplit(request.host_url)
    test = urlsplit(target)
    return (not test.netloc or test.netloc == ref.netloc) and test.scheme in ("", ref.scheme)


@app.route("/login")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    return render_template("login.html", error=request.args.get("error"), next_url=request.args.get("next", ""))


@app.route("/auth/google")
def auth_google():
    if not os.environ.get("GOOGLE_CLIENT_ID") or not os.environ.get("GOOGLE_CLIENT_SECRET"):
        app.logger.error("[AUTH] Google OAuth is not configured: missing client id or secret")
        return redirect(url_for("login", error="google_not_configured"))

    next_url = request.args.get("next") or url_for("index")
    session["post_login_next"] = next_url if _safe_next_url(next_url) else url_for("index")
    redirect_uri = url_for("auth_callback", _external=True)
    app.logger.warning(
        "[AUTH] Starting Google OAuth redirect_uri=%s client_id_present=%s next=%s",
        redirect_uri,
        bool(os.environ.get("GOOGLE_CLIENT_ID")),
        session["post_login_next"],
    )
    return google.authorize_redirect(redirect_uri, prompt="select_account")


@app.route("/auth/callback")
def auth_callback():
    app.logger.warning("[AUTH] Google callback received")
    try:
        token = google.authorize_access_token()
    except Exception as exc:
        app.logger.exception("[AUTH] Google callback failed: %s", exc)
        return redirect(url_for("login", error="google_auth_failed"))

    userinfo = token.get("userinfo")
    if not userinfo:
        app.logger.error("[AUTH] Google callback returned no userinfo")
        return redirect(url_for("login", error="google_userinfo_missing"))

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
    next_url = session.pop("post_login_next", None)
    if not _safe_next_url(next_url):
        next_url = url_for("index")
    app.logger.warning("[AUTH] Google login succeeded for %s", user.email)
    return redirect(next_url)


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


def _holding_key(item: dict) -> str:
    if item.get("type") == "crypto":
        return f"crypto:{item['coin_id']}"
    return f"stock:{item['ticker']}"


def _effective_price_simulator_keys(uid: int, stocks_out: list, crypto_out: list) -> list[str]:
    holdings = [*stocks_out, *crypto_out]
    available = {_holding_key(item): item for item in holdings}
    settings = PriceSimulatorSettings.query.filter_by(user_id=uid).first()

    if settings is not None:
        seen = set()
        effective = []
        for key in settings.holding_keys or []:
            if key in available and key not in seen:
                effective.append(key)
                seen.add(key)
            if len(effective) == 10:
                break
        return effective

    ranked = sorted(
        available.items(),
        key=lambda pair: (-(pair[1].get("current_value_usd") or 0), pair[0]),
    )
    return [key for key, _ in ranked[:10]]


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
            "current_price_usd": cp_usd,
            "current_price_cad": cp_cad,
            "current_price_php": cp_usd * usd_to_php,
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
            "current_price_usd": cp,
            "current_price_cad": cp * usd_to_cad,
            "current_price_php": cp * usd_to_php,
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
            cutoff = now - timedelta(days=370)
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
        "price_simulator_holding_keys": _effective_price_simulator_keys(uid, stocks_out, crypto_out),
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


@app.route("/api/price-simulator/holdings", methods=["PUT"])
@login_required
def update_price_simulator_holdings():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or "holding_keys" not in body:
        return jsonify({"error": "holding_keys is required"}), 400

    holding_keys = body["holding_keys"]
    if not isinstance(holding_keys, list):
        return jsonify({"error": "holding_keys must be an array"}), 400
    if len(holding_keys) > 10:
        return jsonify({"error": "Choose no more than 10 holdings"}), 400
    if any(not isinstance(key, str) for key in holding_keys):
        return jsonify({"error": "Every holding key must be a string"}), 400
    if len(set(holding_keys)) != len(holding_keys):
        return jsonify({"error": "Holdings must be unique"}), 400
    if any(
        not (key.startswith("stock:") or key.startswith("crypto:"))
        or not key.split(":", 1)[1]
        for key in holding_keys
    ):
        return jsonify({"error": "One or more holdings are unavailable"}), 400

    owned_keys = {
        *(f"stock:{stock.ticker}" for stock in Stock.query.filter_by(user_id=current_user.id).all()),
        *(f"crypto:{crypto.coin_id}" for crypto in Crypto.query.filter_by(user_id=current_user.id).all()),
    }
    if any(key not in owned_keys for key in holding_keys):
        return jsonify({"error": "One or more holdings are unavailable"}), 400

    settings = PriceSimulatorSettings.query.filter_by(user_id=current_user.id).first()
    if settings is None:
        settings = PriceSimulatorSettings(user_id=current_user.id, holding_keys=list(holding_keys))
        db.session.add(settings)
    else:
        settings.holding_keys = list(holding_keys)
    db.session.commit()
    return jsonify({"holding_keys": list(holding_keys)})


@app.route("/api/portfolio/export")
def export_portfolio():
    _authorized_export_request()
    owner = _portfolio_owner_for_export()
    if not owner:
        abort(404, description="No portfolio owner found")
    payload = _build_portfolio_payload(owner.id, persist_history=False)
    payload["owner"] = {"name": owner.name}

    # Optionally include financial summary
    include_financial = request.headers.get("X-Include-Financial", "").lower() in ("1", "true", "yes")
    if include_financial:
        today = datetime.utcnow().date()
        six_months_ago = datetime(today.year - (today.month <= 6 and 1 or 0),
                                   ((today.month - 6 - 1) % 12) + 1, 1).date()
        uid = owner.id

        income_entries = IncomeEntry.query.filter(
            IncomeEntry.user_id == uid, IncomeEntry.pay_date >= six_months_ago
        ).all()
        transactions = Transaction.query.filter(
            Transaction.user_id == uid, Transaction.date >= six_months_ago, Transaction.amount < 0
        ).all()

        total_gross = sum(e.gross_income for e in income_entries)
        total_net = sum(e.net_income for e in income_entries)
        total_expenses = sum(abs(t.amount) for t in transactions)
        savings_rate = ((total_net - total_expenses) / total_net * 100) if total_net else 0.0

        payload["financial"] = {
            "monthly_income_avg": round(total_gross / 6, 2) if total_gross else 0.0,
            "monthly_expense_avg": round(total_expenses / 6, 2) if total_expenses else 0.0,
            "savings_rate": round(savings_rate, 1),
            "transaction_count": Transaction.query.filter_by(user_id=uid).count(),
        }

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


# ── Financial routes ─────────────────────────────────────────────────────────

CATEGORY_KEYWORDS = {
    "groceries": ["grocery", "supermarket", "food", "walmart", "costco", "loblaws", "metro", "no frills", "shopify"],
    "gas": ["gas", "petrol", "shell", "esso", "petro", "fuel", "couche-tard"],
    "subscriptions": ["netflix", "spotify", "apple", "google", "amazon prime", "disney", "hulu", "disney+", "gym", "subscription"],
    "dining": ["restaurant", "cafe", "coffee", "tim horton", "starbucks", "mcdonald", "burger", "pizza", "dining", "doordash", "uber eats"],
    "utilities": ["hydro", "water", "gas", "electric", "utility", "bell", "rogers", "telus", "internet"],
    "insurance": ["insurance", "manulife", "sunlife", "blue cross", "ia insurance"],
    "other": [],
}

# Internal transfer keywords — these transactions are internalmovements, not real expenses
_INTERNAL_TRANSFER_KEYWORDS = [
    "transfer to", "transfer from", "internal transfer",
    "movimiento interno", "virement", "internal", "intrnl",
    "xfer to", "xfer from", "paypal transfer", "venmo transfer",
    "e-transfer", "e transfer",
]


def _auto_categorize(description: str) -> str:
    desc_lower = description.lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if cat == "other":
            continue
        for kw in keywords:
            if kw in desc_lower:
                return cat
    return "other"


def _is_internal_transfer(description: str) -> bool:
    """Return True if the description matches patterns for an internal bank transfer."""
    desc_lower = description.lower()
    # Match keyword patterns
    for kw in _INTERNAL_TRANSFER_KEYWORDS:
        if kw in desc_lower:
            return True
    # Pattern: account-number-like sequences in description (8-12 digit runs)
    import re
    if re.search(r"\b\d{8,12}\b", description):
        return True
    return False


def _scrub_pii(text: str) -> str:
    """Remove PII from a string: bank/routing/credit card numbers, SINs, full addresses.
    Returns the scrubbed string. Only the clean merchant/detail portion is stored."""
    import re

    # Routing numbers: exactly 9 digits
    text = re.sub(r"\b\d{9}\b", "[REDACTED]", text)
    # Credit card numbers: 13-19 consecutive digits (with optional spaces/dashes)
    text = re.sub(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{1,5}\b", "[REDACTED]", text)
    text = re.sub(r"\b\d{13,19}\b", "[REDACTED]", text)
    # Bank account numbers: 8-12 digit runs that look like account numbers
    # (avoid redacting dates, amounts, etc. by requiring word boundaries)
    text = re.sub(r"\b\d{8,12}\b", "[REDACTED]", text)
    # SIN numbers: 9-digit sequences (Canadian social insurance number)
    # redact only when standalone (not part of a larger number)
    text = re.sub(r"(?<!\d)\d{9}(?!\d)", "[REDACTED]", text)
    # Cleanup any double-redactions
    text = text.replace("[REDACTED] [REDACTED]", "[REDACTED]")
    return text


# ── File-type detection helpers ──────────────────────────────────────────────

ALLOWED_PAY_STUB_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/heic",
    "image/heif",
}
ALLOWED_BANK_STMT_MIME_TYPES = {
    "application/pdf",
    "text/csv",
    "application/csv",
    "text/comma-separated-values",
    "image/jpeg",
    "image/png",
    "image/heic",
    "image/heif",
}
ALLOWED_CREDIT_CARD_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/heic",
    "image/heif",
}


def _detect_mime_type(content_bytes: bytes) -> str:
    """Detect MIME type from file magic bytes."""
    if content_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content_bytes.startswith(b"RIFF") and content_bytes[8:12] == b"WEBP":
        return "image/webp"
    # HEIC/HEIF: major brand at offset 8
    heif_brands = [b"heic", b"heix", b"mif1", b"hevc", b"hevx"]
    if content_bytes[4:8] == b"ftyp":
        brand = content_bytes[8:12]
        if brand in heif_brands:
            return "image/heic"
        if brand in [b"jpeg"]:
            return "image/jpeg"
    if b"%PDF" in content_bytes[:8]:
        return "application/pdf"
    return "application/octet-stream"


def _allowed_mime(mime_type: str, allowed_set: set) -> bool:
    return mime_type in allowed_set or mime_type.startswith("image/")


# ── Image → text helpers ──────────────────────────────────────────────────────

def _image_to_text(file_stream) -> str:
    """Convert an image file (JPEG, PNG, HEIC) to text using pdfplumber on a converted JPEG."""
    import io
    import pdfplumber
    try:
        from PIL import Image
    except ImportError:
        return ""

    try:
        file_stream.seek(0)
        img = Image.open(file_stream)
        # Convert to RGB (pdfplumber needs RGB)
        if img.mode in ("RGBA", "P", "L"):
            img = img.convert("RGB")
        # Save as JPEG to a BytesIO buffer
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        buf.seek(0)
        text = ""
        with pdfplumber.open(buf) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                text += t + "\n"
        return text
    except Exception as exc:
        print(f"[image_to_text] error: {exc}")
        return ""


# ── Pay stub parser ───────────────────────────────────────────────────────────

def _parse_driver_payment_form(text: str) -> dict:
    """Extract income data from a driver payment / trip settlement form.

    Format example:
        Company   1000944163 ONTARIO INC.
        From   08-Dec-2025   To   21-Dec-2025   Trip#   484170
        Item   Service   Qty   U/M   Rate   Per   Amount   Currency
        1   H   0.0   0.0   $356.54
        2   INSURANCE   1.0   -50.0   $-50.00
        3   SAFETY BONUS   5106.7   0.02   $102.13
        4   DRIVER PAY   5106.7   0.52   $2,655.48
        5   EXTRA DROP   1.0   35.0   $35.00
        Total Trip: $3,099.15   CAD

    gross_income = sum of all positive pay line items (DRIVER PAY, SAFETY BONUS, H, EXTRA DROP)
    net_income   = gross_income - |INSURANCE deduction|
    """
    import re

    result = {"employer": "", "pay_date": "", "gross_income": 0.0, "net_income": 0.0, "deductions": {}}

    # Employer: numeric corp number + company name ending in INC, LTD, LLC, CORP, etc.
    # e.g. "Company   1000944163 ONTARIO INC."
    company_match = re.search(
        r"Company\s+([0-9]+\s+[A-Za-z\s]+?(?:INC\.|INC|LLC|LTD|CORP))", text, re.IGNORECASE
    )
    if company_match:
        result["employer"] = company_match.group(1).strip()[:100]

    # Pay date: trip end date from "To   21-Dec-2025" (dash-separated day-mon-year)
    to_date_match = re.search(r"To\s+(\d{1,2})-([A-Za-z]{3})-(\d{4})", text)
    if to_date_match:
        day, mon, year = to_date_match.groups()
        mon_map = {"jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
                   "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12"}
        mon_key = mon[:3].lower()
        if mon_key in mon_map:
            result["pay_date"] = f"{year}-{mon_map[mon_key]}-{day.zfill(2)}"

    # Insurance deduction: $-50.00 (negative) — take absolute value as a deduction
    insurance_match = re.search(r"INSURANCE.*?\$(-?[\d,]+\.\d{2})", text, re.IGNORECASE)
    insurance_amount = 0.0
    if insurance_match:
        try:
            raw = insurance_match.group(1).replace(",", "")
            insurance_amount = abs(float(raw))
            result["deductions"]["insurance"] = -insurance_amount
        except ValueError:
            pass

    # All item table rows have their dollar amount near the end:
    # e.g. "1   H   0.0   0.0   $356.54" or "4   DRIVER PAY   5106.7   0.52   $2,655.48"
    all_amounts = re.findall(r"^\d+\s+\S+.*?\$([\d,]+\.\d{2})", text, re.MULTILINE)

    gross = 0.0
    for amt_str in all_amounts:
        try:
            val = float(amt_str.replace(",", ""))
            if val > 0:
                gross += val
        except ValueError:
            pass

    result["gross_income"] = round(gross, 2)
    result["net_income"] = round(gross - insurance_amount, 2)

    return result


def _parse_pay_stub(file_stream, filename: str) -> dict:
    """Extract employer, pay_date, gross_income, net_income from a PDF or image file."""
    import re
    import io
    import pdfplumber

    extracted = {"employer": "", "pay_date": "", "gross_income": 0.0, "net_income": 0.0, "deductions": {}}

    file_stream.seek(0)
    content = file_stream.read()
    mime = _detect_mime_type(content)

    text = ""
    try:
        if mime == "application/pdf":
            file_stream.seek(0)
            with pdfplumber.open(file_stream) as pdf:
                for page in pdf.pages:
                    t = page.extract_text() or ""
                    text += t + "\n"
        elif mime in ("image/jpeg", "image/png"):
            file_stream.seek(0)
            text = _image_to_text(file_stream)
        elif mime == "image/heic":
            # Convert HEIC → JPEG via pillow-heif, then extract text
            file_stream.seek(0)
            try:
                import pillow_heif
                heif_file = pillow_heif.open_heif(file_stream.read())
                img = heif_file.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                buf.seek(0)
                with pdfplumber.open(buf) as pdf:
                    for page in pdf.pages:
                        t = page.extract_text() or ""
                        text += t + "\n"
            except Exception as exc:
                print(f"[pay_stub] HEIC parse error: {exc}")
                # Fallback: try plain PIL
                file_stream.seek(0)
                from PIL import Image
                img = Image.open(file_stream).convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                buf.seek(0)
                text = _image_to_text(buf)
    except Exception as exc:
        print(f"[pay_stub] parse error: {exc}")
        return extracted

    # ── Driver payment form / trip settlement detection ─────────────────────
    if re.search(r'Trip#|DRIVER PAY|Total Trip:|SAFETY BONUS', text, re.IGNORECASE):
        # Parse as driver payment form: per-trip settlement from trucking company
        # Format: Company | From/To dates | Trip# | Item/Service/Amount lines | Total Trip
        driver_extracted = _parse_driver_payment_form(text)
        if driver_extracted["employer"] or driver_extracted["gross_income"] > 0:
            return driver_extracted

    lines = text.split("\n")
    for line in lines:
        line_stripped = line.strip()
        # Employer heuristics
        if not extracted["employer"] and len(line_stripped) > 2 and len(line_stripped) < 80:
            if any(w in line_stripped.lower() for w in ["inc", "ltd", "llc", "corp", "motor", "transport", "logistics"]):
                extracted["employer"] = line_stripped[:100]

        # Gross income
        gross_match = re.search(r"gross\s*(?:pay|income|amount)?[:\s]*\$?\s*([\d,]+\.?\d*)", line_stripped, re.IGNORECASE)
        if gross_match and not extracted["gross_income"]:
            try:
                extracted["gross_income"] = float(gross_match.group(1).replace(",", ""))
            except ValueError:
                pass

        # Net income
        net_match = re.search(r"net\s*(?:pay|income|amount|earnings)?[:\s]*\$?\s*([\d,]+\.?\d*)", line_stripped, re.IGNORECASE)
        if net_match and not extracted["net_income"]:
            try:
                extracted["net_income"] = float(net_match.group(1).replace(",", ""))
            except ValueError:
                pass

        # Pay date — also handle "21-Dec-2025" format
        date_match = re.search(r"(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})", line_stripped)
        if date_match and not extracted["pay_date"]:
            extracted["pay_date"] = date_match.group(1)
        # "21-Dec-2025" or "21-December2025" style dates
        if not extracted["pay_date"]:
            mon_map = {"jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
                       "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12"}
            alt_match = re.search(r"(\d{1,2})[\s-]([A-Za-z]+)[\s-](\d{4})", line_stripped)
            if alt_match:
                day, mon, year = alt_match.groups()
                mon_key = mon[:3].lower()
                if mon_key in mon_map:
                    extracted["pay_date"] = f"{year}-{mon_map[mon_key]}-{day.zfill(2)}"

    return extracted


# ── Bank statement parser ─────────────────────────────────────────────────────

def _parse_bank_statement(file_stream, filename: str) -> list:
    """Extract transactions from CSV, PDF, JPEG, PNG, or HEIC bank statements.
    Each transaction is PII-scrubbed before being returned."""
    import re
    import io
    import pdfplumber

    transactions = []

    file_stream.seek(0)
    content = file_stream.read()
    mime = _detect_mime_type(content)

    # ── CSV ──────────────────────────────────────────────────────────────────
    if b"," in content or b"\t" in content:
        try:
            text = content.decode("utf-8", errors="ignore")
            for line in text.split("\n"):
                parts = [p.strip().strip('"') for p in re.split(r"[,;\t]", line)]
                if len(parts) < 2:
                    continue
                amt_match = None
                for p in parts:
                    m = re.search(r"-?\$?([\d,]+\.?\d*)", p)
                    if m:
                        amt_str = m.group(1).replace(",", "")
                        try:
                            val = float(amt_str)
                            amt_match = -val if val > 0 else val
                            break
                        except ValueError:
                            pass
                if amt_match is not None:
                    desc = parts[1] if len(parts) > 1 else parts[0]
                    date_str = parts[0] if len(parts) > 0 else ""
                    if len(desc) > 2 and len(desc) < 200:
                        try:
                            trans_date = datetime.strptime(date_str[:10], "%Y-%m-%d").date() if "-" in date_str else datetime.today().date()
                        except ValueError:
                            trans_date = datetime.today().date()
                        desc_scrubbed = _scrub_pii(desc[:200])
                        cat = "internal_transfer" if _is_internal_transfer(desc) else _auto_categorize(desc_scrubbed)
                        transactions.append({
                            "date": trans_date.isoformat(),
                            "description": desc_scrubbed,
                            "amount": round(amt_match, 2),
                            "category": cat,
                        })
            return transactions
        except Exception:
            pass

    # ── PDF ──────────────────────────────────────────────────────────────────
    if mime == "application/pdf":
        file_stream.seek(0)
        try:
            with pdfplumber.open(file_stream) as pdf:
                text = ""
                for page in pdf.pages:
                    text += (page.extract_text() or "") + "\n"
        except Exception as exc:
            print(f"[bank_statement] PDF parse error: {exc}")
            text = ""
    else:
        # Image: JPEG, PNG, HEIC
        text = ""
        try:
            if mime in ("image/jpeg", "image/png"):
                file_stream.seek(0)
                text = _image_to_text(file_stream)
            elif mime == "image/heic":
                file_stream.seek(0)
                try:
                    import pillow_heif
                    heif_file = pillow_heif.open_heif(file_stream.read())
                    img = heif_file.convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=85)
                    buf.seek(0)
                    with pdfplumber.open(buf) as pdf:
                        for page in pdf.pages:
                            t = page.extract_text() or ""
                            text += t + "\n"
                except Exception as exc:
                    print(f"[bank_statement] HEIC parse error: {exc}")
                    file_stream.seek(0)
                    from PIL import Image
                    img = Image.open(file_stream).convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=85)
                    buf.seek(0)
                    text = _image_to_text(buf)
        except Exception as exc:
            print(f"[bank_statement] image parse error: {exc}")

    # Extract date + description + amount rows from text
    lines = text.split("\n")
    for line in lines:
        line_s = line.strip()
        if len(line_s) < 5:
            continue
        date_m = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})", line_s)
        amt_m = re.search(r"-?\$?\s*([\d,]+\.\d{2})\b", line_s)
        if date_m and amt_m:
            try:
                raw_date = date_m.group(1)
                for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%m-%d-%Y", "%d-%m-%Y"):
                    try:
                        trans_date = datetime.strptime(raw_date, fmt).date()
                        break
                    except ValueError:
                        continue
                else:
                    trans_date = datetime.today().date()
                raw_amt = amt_m.group(1).replace(",", "")
                amount = round(float(raw_amt), 2)
                remaining = re.sub(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})", "", line_s)
                remaining = re.sub(r"-?\$?\s*[\d,]+\.\d{2}", "", remaining)
                remaining = re.sub(r"^\s*[-_.*|]+\s*", "", remaining)
                desc_raw = remaining.strip()[:200]
                if desc_raw and len(desc_raw) > 2:
                    desc_scrubbed = _scrub_pii(desc_raw)
                    cat = "internal_transfer" if _is_internal_transfer(desc_raw) else _auto_categorize(desc_scrubbed)
                    transactions.append({
                        "date": trans_date.isoformat(),
                        "description": desc_scrubbed,
                        "amount": amount,
                        "category": cat,
                    })
            except (ValueError, IndexError):
                pass

    return transactions


# ── Credit card statement parser ─────────────────────────────────────────────

def _parse_credit_card_statement(file_stream, filename: str) -> list:
    """Extract transactions from a TD Platinum Travel Visa or Home Trust Visa
    credit card PDF/image statement.

    TD Visa format (Mon DD, YYYY | DESCRIPTION | $AMOUNT)
    Home Trust format: multi-column table with Trans Date | Description | Amount
      - ACCOUNT ACTIVITY section: purchases/debits
      - Payments, Adjustments and Others section: credits/payments
      - Dates: DD-MMM-YYYY or MM/DD/YY (OCR: "O" instead of "0", "O0" = "00")
      - Negative amounts use "-" suffix (OCR: "$0.70-" or "0.70-")

    Image-based PDFs are OCR'd via pdfplumber page-to-image + pytesseract.
    Disputes are skipped (already reconciled in Payments section)."""
    import re
    import io
    import pdfplumber

    transactions = []

    file_stream.seek(0)
    content = file_stream.read()
    mime = _detect_mime_type(content)

    text = ""
    try:
        if mime == "application/pdf":
            file_stream.seek(0)
            with pdfplumber.open(file_stream) as pdf:
                for page in pdf.pages:
                    t = page.extract_text() or ""
                    text += t + "\n"

            # Image-based PDF — no text extracted; fall back to OCR
            if not text.strip():
                print("[credit_card] PDF has no extractable text; running OCR...")
                file_stream.seek(0)
                with pdfplumber.open(file_stream) as pdf:
                    for page in pdf.pages:
                        img = page.to_image(resolution=200)
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        buf.seek(0)
                        try:
                            from PIL import Image
                            import pytesseract
                            ocr_img = Image.open(buf)
                            text += pytesseract.image_to_string(ocr_img) + "\n"
                        except ImportError:
                            # pytesseract or PIL not available — skip OCR
                            print("[credit_card] OCR libs not available; cannot parse image PDF")
                            break
        elif mime in ("image/jpeg", "image/png"):
            file_stream.seek(0)
            text = _image_to_text(file_stream)
        elif mime == "image/heic":
            file_stream.seek(0)
            try:
                import pillow_heif
                heif_file = pillow_heif.open_heif(file_stream.read())
                img = heif_file.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                buf.seek(0)
                with pdfplumber.open(buf) as pdf:
                    for page in pdf.pages:
                        t = page.extract_text() or ""
                        text += t + "\n"
            except Exception as exc:
                print(f"[credit_card] HEIC parse error: {exc}")
                file_stream.seek(0)
                from PIL import Image
                img = Image.open(file_stream).convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                buf.seek(0)
                text = _image_to_text(buf)
    except Exception as exc:
        print(f"[credit_card] parse error: {exc}")
        return transactions

    # ── Detect card type ─────────────────────────────────────────────────────
    upper_text = text.upper()
    is_hometrust = bool(
        re.search(r"HOMETRUST|HOME\s+TRUST", upper_text)
        and re.search(r"ACCOUNT\s+ACTIVITY", upper_text)
    )

    if is_hometrust:
        transactions = _parse_hometrust_transactions(text)
    else:
        transactions = _parse_td_visa_transactions(text)

    return transactions


def _parse_hometrust_transactions(text: str) -> list:
    """Parse Home Trust Visa statement — multi-column OCR output.

    ACCOUNT ACTIVITY section: Trans Date | Description | Amount
    Payments, Adjustments and Others section: same columns, amounts are credits.

    OCR artifacts handled:
      - "O" -> "0" in dates (e.g. "O14" = "01/14")
      - "O0" -> "00"
      - "$" may be missing before amounts
      - Negative amounts: "0.70-" (trailing dash) instead of "-$0.70"
      - Garbled text in description field
    """
    import re
    transactions = []

    month_map = {
        "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    }

    # Extract statement period so we can handle YY year format
    period_m = re.search(
        r"Statement\s+Period[:\s]+(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\s*[-�E]\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})",
        text, re.IGNORECASE
    )
    stmt_year = None
    if period_m:
        yr_part = re.search(r"\d{4}|\d{2}$", period_m.group(2))
        if yr_part:
            raw_yr = yr_part.group()
            stmt_year = int(raw_yr) if len(raw_yr) == 4 else 2000 + int(raw_yr)

    # Split text into ACCOUNT ACTIVITY and Payments sections
    # OCR garbles "Adjustments" → "iments" or partial chars; be flexible
    payments_marker = re.compile(
        r"Payments?\s*,?\s*[A-Za-z]*ments?\s*(?:and\s*Others)?",
        re.IGNORECASE
    )
    ac_marker = re.compile(r"ACCOUNT\s+ACTIVITY", re.IGNORECASE)

    sections = {}
    current_section = None
    for line in text.split("\n"):
        if ac_marker.search(line):
            current_section = "activity"
            sections[current_section] = []
        elif payments_marker.search(line):
            current_section = "payments"
            sections[current_section] = []
        elif current_section:
            sections[current_section].append(line)

    def fix_date(s: str):
        """Fix OCR 'O'->'0' artifacts in date fields."""
        return s.replace("O", "0").replace("Q", "0")

    def parse_date(date_str: str, stmt_year=None):
        """Parse DD-MMM-YYYY or MM/DD date strings, handling OCR artifacts."""
        date_str = fix_date(date_str.strip())
        # Try DD-MMM-YYYY (e.g. "28-FEB-2026")
        m = re.match(r"(\d{1,2})-([A-Za-z]{3})-(\d{2,4})", date_str)
        if m:
            day, mon_str, yr_str = m.groups()
            mon = month_map.get(mon_str.upper())
            yr = int(yr_str) if len(yr_str) == 4 else 2000 + int(yr_str)
            if mon:
                try:
                    return datetime(int(yr), mon, int(day)).date()
                except ValueError:
                    pass
        # Try MM/DD or MM/DD/YY (year is optional — transactions only show MM/DD)
        m = re.match(r"(\d{1,2})/(\d{1,2})(/(\d{2,4}))?$", date_str)
        if m:
            mon_int = int(m.group(1))
            day = int(m.group(2))
            yr_raw = m.group(4)
            if yr_raw is None:
                yr = stmt_year or 2026
            else:
                yr = int(yr_raw) if len(yr_raw) == 4 else 2000 + int(yr_raw)
            if 1 <= mon_int <= 12 and 1 <= day <= 31:
                try:
                    return datetime(yr, mon_int, day).date()
                except ValueError:
                    pass
        # Handle MMDD OCR artifact (e.g. "0114" → 01/14, "015" → 01/05)
        m = re.match(r"(\d{1,2})(\d{2})$", date_str)
        if m:
            first, second = int(m.group(1)), int(m.group(2))
            # Try MM = first, DD = second
            if 1 <= first <= 12 and 1 <= second <= 31:
                try:
                    return datetime(stmt_year or 2026, first, second).date()
                except ValueError:
                    pass
            # Try MM = second, DD = first (less common)
            if 1 <= second <= 12 and 1 <= first <= 31:
                try:
                    return datetime(stmt_year or 2026, second, first).date()
                except ValueError:
                    pass
        return None

    def parse_amount(amt_str: str):
        """Parse amount string: handles '$', commas, trailing '-' for credit."""
        amt_str = amt_str.strip().replace(",", "").replace("$", "")
        is_credit = amt_str.endswith("-")
        if is_credit:
            amt_str = amt_str[:-1]
        try:
            return float(amt_str), is_credit
        except ValueError:
            return None, False

    def parse_transaction_row(line: str, section: str):
        """Parse a Home Trust transaction row (multi-column OCR layout)."""
        line_s = line.strip()
        if len(line_s) < 5:
            return None

        # Skip header/metadata lines
        if re.match(r"^(Trans|Post|Reference|Description|Amount|Credit|Charge|Category|Balance|Interest|Minimum|Payment|Total|Statement|Page|ACCOUNT|PAYMENT|Summary|Cash|Finance)", line_s, re.IGNORECASE):
            return None
        if re.match(r"^\s*[-=]{3,}", line_s):
            return None
        if re.search(r"FINANCE CHARGE|MINIMUM PAYMENT|PAYMENT DUE|CREDIT LIMIT|AVAILABLE|Reduit|CHARGE SUMMARY|PURCHASE.*RATE|SCORECARD EARNINGS|KEE|BEGINNING|Ending|CASHBACK PAYOUT", line_s, re.IGNORECASE):
            return None

        # Amount: look for $X.XX or X.XX anywhere on line, prefer at end
        # \$ ? handles "$ 0.70" OCR artifact (space between $ and number)
        amt_m = re.search(r"\$ ?([\d,]+\.\d{2})(-?)\s*$", line_s)
        if not amt_m:
            amt_m = re.search(r"\$ ?([\d,]+\.\d{2})(-?)\b", line_s)
        if not amt_m:
            amt_m = re.search(r"\b([\d,]+\.\d{2})(-?)\b", line_s)
        if not amt_m:
            return None

        raw_amt_str = amt_m.group(1)
        trailing_dash = bool(amt_m.group(2)) or line_s.rstrip().endswith("-")
        is_credit = trailing_dash or (section == "payments")

        # Extract date — first token(s) are transaction/post dates (may have / or -)
        # Check first 4 tokens to find a valid date
        tokens = line_s.split()
        date_parsed = None
        date_token_len = 0
        for i, tok in enumerate(tokens[:4]):
            dp = parse_date(tok, stmt_year)
            if dp:
                date_parsed = dp
                date_token_len = i + 1
                break
        if not date_parsed:
            return None

        # Build description: skip date tokens, remove reference numbers
        before_amt = line_s[:amt_m.start()].strip()
        desc_tokens = before_amt.split()[date_token_len:]
        # Filter out long reference numbers (6+ digits — these are ref numbers)
        desc_tokens = [t for t in desc_tokens if not re.match(r"^\d{6,}$", t)]
        desc_clean = re.sub(r"\s+", " ", " ".join(desc_tokens)).strip()
        # Clean up common OCR artifacts in descriptions
        desc_clean = (desc_clean
            .replace(" PURCH:", " | USD").replace(" PURCH", " | USD")
            .replace("PURCHASE:", "| USD").replace("PURCH: ", "| USD "))
        desc_clean = re.sub(r"\b(PAGE|Pg)\s+\d+\s+OF\s+\d+\b", "", desc_clean, flags=re.IGNORECASE)
        desc_clean = desc_clean.strip("|_- ").strip()

        if len(desc_clean) < 2:
            return None

        # Skip dispute lines (already reconciled in Payments section)
        if re.search(r"\bDISPUTE\b", desc_clean, re.IGNORECASE):
            return None

        desc_scrubbed = _scrub_pii(desc_clean[:200])
        amount_val, _ = parse_amount(raw_amt_str)
        if amount_val is None:
            return None

        if is_credit or section == "payments":
            amount = amount_val
            category = "payment"
        else:
            amount = -amount_val
            category = _auto_categorize(desc_scrubbed)

        return {
            "date": date_parsed.isoformat(),
            "description": desc_scrubbed,
            "amount": round(amount, 2),
            "category": category,
        }

    # Parse ACCOUNT ACTIVITY rows
    for line in sections.get("activity", []):
        tx = parse_transaction_row(line, "activity")
        if tx:
            transactions.append(tx)

    # Parse Payments, Adjustments and Others rows
    for line in sections.get("payments", []):
        tx = parse_transaction_row(line, "payments")
        if tx:
            transactions.append(tx)

    return transactions


def _parse_td_visa_transactions(text: str) -> list:
    """Parse TD Visa format: Mon DD, YYYY | DESCRIPTION | $AMOUNT [credit]."""
    import re
    transactions = []

    month_map = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
        "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
    }

    DATE_RE = re.compile(r"^([A-Z][a-z]{2})\s+(\d{1,2}),?\s+(\d{4})\b")
    AMT_RE = re.compile(r"\$([\d,]+\.\d{2})((?:\s*\(credit\))?)")

    for line in text.split("\n"):
        line_s = line.strip()
        if len(line_s) < 8:
            continue

        date_m = DATE_RE.match(line_s)
        if not date_m:
            continue

        mon_str, day_str, yr_str = date_m.groups()
        mon = month_map.get(mon_str)
        if not mon:
            continue

        remaining = line_s[date_m.end() :]
        if "TEMP AUTH" in remaining.upper() or "PENDING" in remaining.upper():
            continue

        amt_m = AMT_RE.search(remaining)
        if not amt_m:
            continue

        raw_amount = float(amt_m.group(1).replace(",", ""))
        is_credit = bool(amt_m.group(2)) or "(credit)" in remaining

        try:
            trans_date = datetime(int(yr_str), mon, int(day_str)).date()
        except ValueError:
            continue

        before_dollar = remaining[: amt_m.start()].strip()
        desc_raw = re.sub(r"\s+", " ", before_dollar).strip()[:200]

        if not desc_raw or len(desc_raw) < 2:
            continue

        desc_scrubbed = _scrub_pii(desc_raw)

        if is_credit or "PAYMENT" in desc_raw.upper():
            amount = raw_amount
            category = "payment"
        else:
            amount = -raw_amount
            category = _auto_categorize(desc_scrubbed)

        transactions.append({
            "date": trans_date.isoformat(),
            "description": desc_scrubbed,
            "amount": round(amount, 2),
            "category": category,
        })

    return transactions




@app.route("/financial")
@login_required
def financial():
    return render_template("financial.html")


@app.route("/api/financial/upload", methods=["POST"])
@login_required
def financial_upload():
    try:
        return _handle_financial_upload()
    except Exception as exc:
        print(f"[financial_upload] unhandled error: {exc}")
        return jsonify({"error": f"Upload processing failed: {str(exc)}"}), 500


def _handle_financial_upload():
    file = request.files.get("file")
    upload_type = request.form.get("type", "manual")

    if not file:
        return jsonify({"error": "No file provided"}), 400

    filename = file.filename or ""

    if upload_type == "pay_stub":
        # MIME type validation
        file.stream.seek(0)
        header = file.stream.read(512)
        mime = _detect_mime_type(header)
        if not _allowed_mime(mime, ALLOWED_PAY_STUB_MIME_TYPES):
            return jsonify({"error": f"Unsupported file type '{mime}' for pay stub. Please upload a PDF, JPEG, PNG, or HEIC image."}), 400
        file.stream.seek(0)

        extracted = _parse_pay_stub(file.stream, filename)

        # Fallback: if nothing was extracted, return raw data so user can fill in
        if not extracted["employer"] and extracted["gross_income"] == 0:
            extracted["_parse_failed"] = True
            return jsonify({"type": "pay_stub", "data": extracted, "raw_preview": "Could not auto-parse. Please fill in manually."}), 200

        # Store the entry directly
        pay_date = datetime.today().date()
        if extracted["pay_date"]:
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y"):
                try:
                    pay_date = datetime.strptime(extracted["pay_date"], fmt).date()
                    break
                except ValueError:
                    pass

        entry = IncomeEntry(
            user_id=current_user.id,
            pay_date=pay_date,
            employer=extracted["employer"] or "Unknown Employer",
            gross_income=extracted["gross_income"],
            net_income=extracted["net_income"] or extracted["gross_income"],
            deductions=extracted.get("deductions") or {},
        )
        db.session.add(entry)
        db.session.commit()
        return jsonify({"message": "Pay stub saved", "entry": {
            "id": entry.id,
            "employer": entry.employer,
            "pay_date": entry.pay_date.isoformat(),
            "gross_income": entry.gross_income,
            "net_income": entry.net_income,
        }}), 200

    elif upload_type == "bank_statement":
        # MIME type validation
        file.stream.seek(0)
        header = file.stream.read(512)
        mime = _detect_mime_type(header)
        if not _allowed_mime(mime, ALLOWED_BANK_STMT_MIME_TYPES):
            return jsonify({"error": f"Unsupported file type '{mime}' for bank statement. Please upload a PDF, CSV, JPEG, PNG, or HEIC image."}), 400
        file.stream.seek(0)

        transactions = _parse_bank_statement(file.stream, filename)

        if not transactions:
            return jsonify({"type": "bank_statement", "data": [], "raw_preview": "Could not auto-parse. Please add transactions manually."}), 200

        saved = []
        for t in transactions:
            entry = Transaction(
                user_id=current_user.id,
                date=datetime.strptime(t["date"], "%Y-%m-%d").date(),
                description=t["description"],
                amount=t["amount"],
                category=t.get("category", "other"),
                source="bank_statement",
            )
            db.session.add(entry)
            saved.append(entry)

        db.session.commit()
        return jsonify({"message": f"{len(saved)} transactions imported", "count": len(saved)}), 200

    elif upload_type == "credit_card":
        # MIME type validation
        file.stream.seek(0)
        header = file.stream.read(512)
        mime = _detect_mime_type(header)
        if not _allowed_mime(mime, ALLOWED_CREDIT_CARD_MIME_TYPES):
            return jsonify({"error": f"Unsupported file type '{mime}' for credit card statement. Please upload a PDF, JPEG, PNG, or HEIC image."}), 400
        file.stream.seek(0)

        transactions = _parse_credit_card_statement(file.stream, filename)

        if not transactions:
            return jsonify({"type": "credit_card", "data": [], "raw_preview": "Could not auto-parse. Please add transactions manually."}), 200

        saved = []
        for t in transactions:
            entry = Transaction(
                user_id=current_user.id,
                date=datetime.strptime(t["date"], "%Y-%m-%d").date(),
                description=t["description"],
                amount=t["amount"],
                category=t.get("category", "other"),
                source="credit_card",
            )
            db.session.add(entry)
            saved.append(entry)

        db.session.commit()
        return jsonify({"message": f"{len(saved)} transactions imported", "count": len(saved)}), 200

    return jsonify({"error": "Invalid upload type"}), 400


@app.route("/api/financial/entries", methods=["GET", "POST"])
@login_required
def financial_entries():
    if request.method == "POST":
        body = request.json or {}
        try:
            pay_date = datetime.strptime(body.get("pay_date", ""), "%Y-%m-%d").date()
            gross = float(body.get("gross_income", 0))
            net = float(body.get("net_income", 0))
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid data"}), 400

        if gross <= 0 or net <= 0:
            return jsonify({"error": "Amounts must be positive"}), 400

        entry = IncomeEntry(
            user_id=current_user.id,
            pay_date=pay_date,
            employer=body.get("employer", "Unknown"),
            gross_income=gross,
            net_income=net,
            deductions=body.get("deductions") or {},
        )
        db.session.add(entry)
        db.session.commit()
        return jsonify({"message": "Income entry added", "entry": {
            "id": entry.id,
            "employer": entry.employer,
            "pay_date": entry.pay_date.isoformat(),
            "gross_income": entry.gross_income,
            "net_income": entry.net_income,
        }}), 201

    entries = (IncomeEntry.query
               .filter_by(user_id=current_user.id)
               .order_by(IncomeEntry.pay_date.desc())
               .all())
    return jsonify([{
        "id": e.id,
        "employer": e.employer,
        "pay_date": e.pay_date.isoformat(),
        "gross_income": e.gross_income,
        "net_income": e.net_income,
        "deductions": e.deductions or {},
        "created_at": e.created_at.isoformat() if e.created_at else None,
    } for e in entries])


@app.route("/api/financial/transactions", methods=["GET", "POST"])
@login_required
def financial_transactions():
    if request.method == "POST":
        body = request.json or {}
        try:
            trans_date = datetime.strptime(body.get("date", ""), "%Y-%m-%d").date()
            amount = float(body.get("amount", 0))
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid data"}), 400

        entry = Transaction(
            user_id=current_user.id,
            date=trans_date,
            description=body.get("description", "").strip()[:512],
            amount=amount,
            category=body.get("category", "other"),
            source="manual",
        )
        db.session.add(entry)
        db.session.commit()
        return jsonify({"message": "Transaction added", "transaction": {
            "id": entry.id,
            "date": entry.date.isoformat(),
            "description": entry.description,
            "amount": entry.amount,
            "category": entry.category,
        }}), 201

    # GET with optional pagination
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    page = max(1, page)
    per_page = min(200, max(1, per_page))

    query = Transaction.query.filter_by(user_id=current_user.id)
    total = query.count()
    entries = (query.order_by(Transaction.date.desc())
               .offset((page - 1) * per_page)
               .limit(per_page)
               .all())
    return jsonify({
        "transactions": [{
            "id": t.id,
            "date": t.date.isoformat(),
            "description": t.description,
            "amount": t.amount,
            "category": t.category,
            "source": t.source,
        } for t in entries],
        "total": total,
        "page": page,
        "per_page": per_page,
    })


@app.route("/api/financial/summary", methods=["GET"])
@login_required
def financial_summary():
    """Monthly income, expenses, savings rate for current user."""
    uid = current_user.id
    today = datetime.utcnow().date()
    current_year = today.year
    six_months_ago = datetime(today.year - (today.month <= 6 and 1 or 0),
                               ((today.month - 6 - 1) % 12) + 1, 1).date()

    # Monthly income (last 6 months)
    income_entries = (IncomeEntry.query
                      .filter(IncomeEntry.user_id == uid,
                              IncomeEntry.pay_date >= six_months_ago)
                      .order_by(IncomeEntry.pay_date.desc())
                      .all())

    monthly_income = {}
    for e in income_entries:
        key = (e.pay_date.year, e.pay_date.month)
        if key not in monthly_income:
            monthly_income[key] = {"gross": 0.0, "net": 0.0, "count": 0}
        monthly_income[key]["gross"] += e.gross_income
        monthly_income[key]["net"] += e.net_income
        monthly_income[key]["count"] += 1

    # Monthly expenses (last 6 months)
    transactions = (Transaction.query
                    .filter(Transaction.user_id == uid,
                            Transaction.date >= six_months_ago,
                            Transaction.amount < 0)
                    .order_by(Transaction.date.desc())
                    .all())

    monthly_expenses = {}
    category_totals = {}
    for t in transactions:
        key = (t.date.year, t.date.month)
        if key not in monthly_expenses:
            monthly_expenses[key] = 0.0
        monthly_expenses[key] += abs(t.amount)
        category_totals[t.category] = category_totals.get(t.category, 0.0) + abs(t.amount)

    # Build last-6-months series
    months = []
    cur_year, cur_month = today.year, today.month
    for _ in range(6):
        months.insert(0, (cur_year, cur_month))
        cur_month -= 1
        if cur_month == 0:
            cur_month = 12
            cur_year -= 1

    monthly_series = []
    total_gross = 0.0
    total_net = 0.0
    total_expenses = 0.0

    for (yr, mo) in months:
        gross = monthly_income.get((yr, mo), {}).get("gross", 0.0)
        net = monthly_income.get((yr, mo), {}).get("net", 0.0)
        exp = monthly_expenses.get((yr, mo), 0.0)
        total_gross += gross
        total_net += net
        total_expenses += exp
        savings = net - exp
        rate = (savings / net * 100) if net > 0 else 0.0
        monthly_series.append({
            "year": yr,
            "month": mo,
            "label": datetime(yr, mo, 1).strftime("%b %Y"),
            "gross_income": round(gross, 2),
            "net_income": round(net, 2),
            "expenses": round(exp, 2),
            "savings": round(savings, 2),
            "savings_rate": round(rate, 1),
            "entry_count": monthly_income.get((yr, mo), {}).get("count", 0),
        })

    # Top 3 expense categories
    top_categories = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)[:3]
    transaction_count = Transaction.query.filter_by(user_id=uid).count()

    avg_monthly_income = total_gross / 6 if total_gross else 0.0
    avg_monthly_expenses = total_expenses / 6 if total_expenses else 0.0
    overall_savings_rate = ((total_net - total_expenses) / total_net * 100) if total_net else 0.0

    # ── YTD averages (all entries in current calendar year) ───────────────
    ytd_income_entries = (IncomeEntry.query
                           .filter(IncomeEntry.user_id == uid,
                                   db.extract("year", IncomeEntry.pay_date) == current_year)
                           .all())
    ytd_transactions = (Transaction.query
                         .filter(Transaction.user_id == uid,
                                 db.extract("year", Transaction.date) == current_year,
                                 Transaction.amount < 0)
                         .all())

    # Group YTD income by (year, month) to count months-with-entries
    ytd_monthly = {}
    for e in ytd_income_entries:
        key = (e.pay_date.year, e.pay_date.month)
        if key not in ytd_monthly:
            ytd_monthly[key] = {"gross": 0.0, "net": 0.0}
        ytd_monthly[key]["gross"] += e.gross_income
        ytd_monthly[key]["net"] += e.net_income

    ytd_expenses_monthly = {}
    for t in ytd_transactions:
        key = (t.date.year, t.date.month)
        if key not in ytd_expenses_monthly:
            ytd_expenses_monthly[key] = 0.0
        ytd_expenses_monthly[key] += abs(t.amount)

    months_with_entries = len(ytd_monthly)
    ytd_total_gross = sum(v["gross"] for v in ytd_monthly.values())
    ytd_total_net = sum(v["net"] for v in ytd_monthly.values())
    ytd_total_expenses = sum(ytd_expenses_monthly.values())

    ytd_avg_gross = ytd_total_gross / months_with_entries if months_with_entries else 0.0
    ytd_avg_net = ytd_total_net / months_with_entries if months_with_entries else 0.0
    ytd_avg_expenses = ytd_total_expenses / months_with_entries if months_with_entries else 0.0
    ytd_avg_savings_rate = ((ytd_avg_net - ytd_avg_expenses) / ytd_avg_net * 100) if ytd_avg_net else 0.0

    # Current calendar month values (for secondary display)
    curr_key = (today.year, today.month)
    current_month_gross = ytd_monthly.get(curr_key, {}).get("gross", 0.0)
    current_month_net = ytd_monthly.get(curr_key, {}).get("net", 0.0)
    current_month_expenses = ytd_expenses_monthly.get(curr_key, 0.0)

    return jsonify({
        "monthly_series": monthly_series,
        "total_gross_income": round(total_gross, 2),
        "total_net_income": round(total_net, 2),
        "total_expenses": round(total_expenses, 2),
        "avg_monthly_gross": round(avg_monthly_income, 2),
        "avg_monthly_net": round((total_net / 6), 2) if total_net else 0.0,
        "avg_monthly_expenses": round(avg_monthly_expenses, 2),
        "overall_savings_rate": round(overall_savings_rate, 1),
        "top_expense_categories": [{"category": c, "amount": round(a, 2)} for c, a in top_categories],
        "transaction_count": transaction_count,
        "income_entry_count": len(income_entries),
        # YTD averages
        "ytd_avg_gross": round(ytd_avg_gross, 2),
        "ytd_avg_net": round(ytd_avg_net, 2),
        "ytd_avg_expenses": round(ytd_avg_expenses, 2),
        "ytd_avg_savings_rate": round(ytd_avg_savings_rate, 1),
        "current_month_gross": round(current_month_gross, 2),
        "current_month_net": round(current_month_net, 2),
        "current_month_expenses": round(current_month_expenses, 2),
        "months_with_entries": months_with_entries,
    })


@app.route("/api/income/edit/<int:entry_id>", methods=["PUT"])
@login_required
def edit_income_entry(entry_id):
    entry = IncomeEntry.query.filter_by(id=entry_id, user_id=current_user.id).first_or_404()
    data = request.get_json()
    if "pay_date" in data:
        entry.pay_date = datetime.strptime(data["pay_date"], "%Y-%m-%d").date()
    if "employer" in data:
        entry.employer = data["employer"][:256]
    if "gross_income" in data:
        entry.gross_income = float(data["gross_income"])
    if "net_income" in data:
        entry.net_income = float(data["net_income"])
    db.session.commit()
    return jsonify({"success": True, "entry": {
        "id": entry.id,
        "pay_date": entry.pay_date.isoformat(),
        "employer": entry.employer,
        "gross_income": entry.gross_income,
        "net_income": entry.net_income,
        "deductions": entry.deductions,
    }})


@app.route("/api/transaction/edit/<int:transaction_id>", methods=["PUT"])
@login_required
def edit_transaction(transaction_id):
    t = Transaction.query.filter_by(id=transaction_id, user_id=current_user.id).first_or_404()
    data = request.get_json()
    if "date" in data:
        t.date = datetime.strptime(data["date"], "%Y-%m-%d").date()
    if "description" in data:
        t.description = data["description"][:512]
    if "amount" in data:
        t.amount = float(data["amount"])
    if "category" in data:
        t.category = data["category"]
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/financial/entries/<int:entry_id>", methods=["DELETE"])
@login_required
def financial_delete_entry(entry_id):
    entry = IncomeEntry.query.filter_by(id=entry_id, user_id=current_user.id).first()
    if not entry:
        return jsonify({"error": "Not found"}), 404
    db.session.delete(entry)
    db.session.commit()
    return jsonify({"message": "Income entry deleted"})


@app.route("/api/financial/transactions/<int:trans_id>", methods=["DELETE"])
@login_required
def financial_delete_transaction_by_id(trans_id):
    entry = Transaction.query.filter_by(id=trans_id, user_id=current_user.id).first()
    if not entry:
        return jsonify({"error": "Not found"}), 404
    db.session.delete(entry)
    db.session.commit()
    return jsonify({"message": "Transaction deleted"})


# ── Health check (no auth required) ──────────────────────────────────────────

@app.route("/health")
def health():
    return "ok", 200


from research import research_bp
app.register_blueprint(research_bp)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
