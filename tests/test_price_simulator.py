import os
from pathlib import Path

import pytest


os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import app as tracker


@pytest.fixture()
def simulator_app(monkeypatch):
    tracker.app.config.update(TESTING=True, LOGIN_DISABLED=False)
    tracker.login_manager.session_protection = None

    with tracker.app.app_context():
        tracker.db.drop_all()
        tracker.db.create_all()

        first = tracker.User(google_id="user-one", name="One", email="one@example.com")
        second = tracker.User(google_id="user-two", name="Two", email="two@example.com")
        tracker.db.session.add_all([first, second])
        tracker.db.session.commit()

        monkeypatch.setattr(tracker, "get_exchange_rates", lambda: (1.25, 50.0))
        monkeypatch.setattr(
            tracker,
            "get_stock_prices",
            lambda tickers: {
                ticker: {
                    "AAA": 100.0,
                    "BBB": 50.0,
                    "ZERO": 0.0,
                    "FOREIGN": 30.0,
                }.get(ticker, 10.0)
                for ticker in tickers
            },
        )
        monkeypatch.setattr(
            tracker,
            "get_crypto_prices",
            lambda coin_ids: {
                coin_id: {"bitcoin": 20.0, "ether": 100.0}.get(coin_id, 5.0)
                for coin_id in coin_ids
            },
        )

        yield tracker.app, first.id, second.id

        tracker.db.session.remove()
        tracker.db.drop_all()


def login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def add_holding(user_id, *, ticker=None, coin_id=None, shares=1.0, amount=1.0):
    if ticker:
        tracker.db.session.add(
            tracker.Stock(
                user_id=user_id,
                ticker=ticker,
                shares=shares,
                avg_purchase_price=1.0,
                purchase_currency="CAD" if ticker.endswith(".TO") else "USD",
            )
        )
    else:
        tracker.db.session.add(
            tracker.Crypto(
                user_id=user_id,
                coin_id=coin_id,
                coin_name=coin_id,
                amount=amount,
                avg_purchase_price=1.0,
            )
        )
    tracker.db.session.commit()


def test_unauthenticated_selection_update_redirects_to_login(simulator_app):
    app, _, _ = simulator_app
    response = app.test_client().put(
        "/api/price-simulator/holdings",
        json={"holding_keys": []},
    )

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_default_selection_is_value_ranked_with_deterministic_ties(simulator_app):
    app, user_id, _ = simulator_app
    with app.app_context():
        add_holding(user_id, ticker="AAA", shares=1.0)
        add_holding(user_id, ticker="BBB", shares=2.0)
        add_holding(user_id, ticker="ZERO", shares=10.0)
        add_holding(user_id, coin_id="bitcoin", amount=2.0)

        payload = tracker._build_portfolio_payload(user_id, persist_history=False)

    assert payload["price_simulator_holding_keys"] == [
        "stock:AAA",
        "stock:BBB",
        "crypto:bitcoin",
        "stock:ZERO",
    ]


def test_saved_empty_selection_does_not_fall_back_to_defaults(simulator_app):
    app, user_id, _ = simulator_app
    client = app.test_client()
    login(client, user_id)

    with app.app_context():
        add_holding(user_id, ticker="AAA")

    response = client.put("/api/price-simulator/holdings", json={"holding_keys": []})
    assert response.status_code == 200
    assert response.get_json() == {"holding_keys": []}

    with app.app_context():
        payload = tracker._build_portfolio_payload(user_id, persist_history=False)
        settings = tracker.PriceSimulatorSettings.query.filter_by(user_id=user_id).one()

    assert settings.holding_keys == []
    assert payload["price_simulator_holding_keys"] == []


def test_selection_persists_order_and_is_isolated_per_user(simulator_app):
    app, first_id, second_id = simulator_app
    first_client = app.test_client()
    second_client = app.test_client()
    login(first_client, first_id)
    login(second_client, second_id)

    with app.app_context():
        add_holding(first_id, ticker="AAA")
        add_holding(first_id, coin_id="bitcoin")
        add_holding(second_id, ticker="FOREIGN")

    response = first_client.put(
        "/api/price-simulator/holdings",
        json={"holding_keys": ["crypto:bitcoin", "stock:AAA"]},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "holding_keys": ["crypto:bitcoin", "stock:AAA"]
    }

    with app.app_context():
        first_payload = tracker._build_portfolio_payload(first_id, persist_history=False)
        second_payload = tracker._build_portfolio_payload(second_id, persist_history=False)

    assert first_payload["price_simulator_holding_keys"] == [
        "crypto:bitcoin",
        "stock:AAA",
    ]
    assert second_payload["price_simulator_holding_keys"] == ["stock:FOREIGN"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"holding_keys": "stock:AAA"},
        {"holding_keys": [1]},
        {"holding_keys": ["stock:AAA", "stock:AAA"]},
        {"holding_keys": [f"stock:T{i}" for i in range(11)]},
        {"holding_keys": ["cash:wallet"]},
    ],
)
def test_invalid_selection_bodies_return_stable_json_error(simulator_app, body):
    app, user_id, _ = simulator_app
    client = app.test_client()
    login(client, user_id)

    with app.app_context():
        add_holding(user_id, ticker="AAA")

    response = client.put("/api/price-simulator/holdings", json=body)

    assert response.status_code == 400
    assert set(response.get_json()) == {"error"}
    assert isinstance(response.get_json()["error"], str)


def test_unowned_holding_is_rejected_without_disclosure(simulator_app):
    app, first_id, second_id = simulator_app
    client = app.test_client()
    login(client, first_id)

    with app.app_context():
        add_holding(second_id, ticker="FOREIGN")

    response = client.put(
        "/api/price-simulator/holdings",
        json={"holding_keys": ["stock:FOREIGN"]},
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "One or more holdings are unavailable"
    }


def test_deleted_saved_holding_is_omitted_without_rewriting_settings(simulator_app):
    app, user_id, _ = simulator_app
    client = app.test_client()
    login(client, user_id)

    with app.app_context():
        add_holding(user_id, ticker="AAA")
        add_holding(user_id, coin_id="bitcoin")

    client.put(
        "/api/price-simulator/holdings",
        json={"holding_keys": ["stock:AAA", "crypto:bitcoin"]},
    )

    with app.app_context():
        tracker.Stock.query.filter_by(user_id=user_id, ticker="AAA").delete()
        tracker.db.session.commit()
        payload = tracker._build_portfolio_payload(user_id, persist_history=False)
        settings = tracker.PriceSimulatorSettings.query.filter_by(user_id=user_id).one()

    assert payload["price_simulator_holding_keys"] == ["crypto:bitcoin"]
    assert settings.holding_keys == ["stock:AAA", "crypto:bitcoin"]


def test_payload_exposes_normalized_unit_prices_in_all_currencies(simulator_app):
    app, user_id, _ = simulator_app
    with app.app_context():
        add_holding(user_id, ticker="AAA")
        add_holding(user_id, coin_id="ether")
        payload = tracker._build_portfolio_payload(user_id, persist_history=False)

    stock = payload["stocks"][0]
    crypto = payload["crypto"][0]
    assert (stock["current_price_usd"], stock["current_price_cad"], stock["current_price_php"]) == (
        100.0,
        125.0,
        5000.0,
    )
    assert (crypto["current_price_usd"], crypto["current_price_cad"], crypto["current_price_php"]) == (
        100.0,
        125.0,
        5000.0,
    )


def test_simulator_template_and_styles_expose_approved_controls():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates" / "index.html").read_text(encoding="utf-8")
    script = (root / "static" / "js" / "app.js").read_text(encoding="utf-8")
    styles = (root / "static" / "css" / "style.css").read_text(encoding="utf-8")

    assert 'id="addSimulatorHolding"' in template
    assert 'id="simulatorSaveStatus"' in template
    assert "simulator-holding-select" in script
    assert 'min="-100" max="500"' in script
    assert "min-height: 44px" in styles
    assert "simulator-holding-control:hover" in styles
    assert "simulator-holding-control:focus-within" in styles
