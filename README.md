# Investment Tracker

A web-based portfolio tracker built with Python and Flask that shows real-time prices for your stocks and crypto holdings, with USD/CAD currency conversion, savings goal tracking, and live charts.

---

## Features

- **Stocks** — real-time prices via [yfinance](https://github.com/ranaroussi/yfinance)
- **Crypto** — real-time prices via [CoinGecko API](https://www.coingecko.com/en/api)
- **Currency conversion** — live USD → CAD via [Frankfurter API](https://www.frankfurter.dev)
- **Portfolio allocation** — doughnut chart showing your asset breakdown
- **Value history** — line chart of portfolio value over the last 90 days
- **Savings goal** — set a target amount and track progress with a visual progress bar
- **Auto-refresh** — prices update automatically every 60 seconds
- **Persistent storage** — all holdings saved to a local JSON file
- **Dark theme UI** — responsive dashboard that works on desktop and mobile

---

## Setup

### 1. Clone or download the project

```bash
git clone <your-repo-url>
cd investment-tracker
```

### 2. Create a virtual environment

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and add your CoinGecko API key:

```
COINGECKO_API_KEY=your_coingecko_demo_api_key_here
```

**Getting a CoinGecko API key:**
1. Go to [https://www.coingecko.com/en/api](https://www.coingecko.com/en/api)
2. Sign up for a free Demo account
3. Copy your Demo API key into `.env`

> The app will still work without an API key, but you may hit CoinGecko's free-tier rate limits.

### 5. Run the app

```bash
python app.py
```

Open [http://localhost:5000](http://localhost:5000) in your browser.

---

## Usage

### Adding a Stock
1. Click **+ Add Stock**
2. Enter the ticker symbol (e.g. `AAPL`, `TSLA`, `MSFT`)
3. Enter the number of shares you own
4. Enter your average purchase price in USD
5. Click **Add Stock**

### Adding Crypto
1. Click **+ Add Crypto**
2. Enter the CoinGecko coin ID — this is the lowercase slug used by CoinGecko:
   - Bitcoin → `bitcoin`
   - Ethereum → `ethereum`
   - Solana → `solana`
   - Dogecoin → `dogecoin`

   You can find any coin's ID by searching on [coingecko.com](https://www.coingecko.com) and looking at the URL: `coingecko.com/en/coins/`**`bitcoin`**
3. Enter a display name (e.g. `Bitcoin`)
4. Enter the amount you hold
5. Enter your average purchase price in USD
6. Click **Add Crypto**

### Setting a Savings Goal
1. Click **Set Goal**
2. Enter your target amount
3. Choose whether the target is in USD or CAD
4. Click **Save Goal**

The progress bar will show how close your total portfolio value is to the goal.

---

## Project Structure

```
investment-tracker/
├── app.py                # Flask backend — routes and price-fetching logic
├── requirements.txt      # Python dependencies
├── .env                  # Your secrets (not committed)
├── .env.example          # Template showing required environment variables
├── .gitignore
├── README.md
├── data/
│   └── portfolio.json    # Persisted holdings and history (auto-created)
├── templates/
│   └── index.html        # Dashboard HTML
└── static/
    ├── css/
    │   └── style.css     # Dark theme styles
    └── js/
        └── app.js        # Frontend logic and Chart.js charts
```

---

## APIs Used

| API | Purpose | Rate Limit (free) |
|-----|---------|-------------------|
| [yfinance](https://github.com/ranaroussi/yfinance) | Stock prices | Generous (Yahoo Finance) |
| [CoinGecko Demo](https://www.coingecko.com/en/api) | Crypto prices | 30 calls/min |
| [Frankfurter](https://www.frankfurter.dev) | USD/CAD rate | Unlimited |

---

## Notes

- Portfolio data is stored in `data/portfolio.json`. This file is excluded from git by default via `.gitignore`. Remove that line if you want to commit your holdings.
- History snapshots are taken at most once per hour to avoid bloating the JSON file.
- History is kept for 90 days.
- All prices are in USD; CAD values are calculated using the live Frankfurter rate.
