/* ============================================================
   Investment Tracker — frontend
   ============================================================ */

// ── chart instances ────────────────────────────────────────────────────────
let pieChart = null;
let lineChart = null;

// ── auto-refresh state ─────────────────────────────────────────────────────
let countdownValue = 60;
let countdownInterval = null;
let refreshInterval = null;

// ── last fetched data (for instant re-render on toggle) ────────────────────
let lastData = null;

// ── currency display mode ──────────────────────────────────────────────────
let currencyMode = localStorage.getItem("currencyMode") || "USD";

// ── helpers ────────────────────────────────────────────────────────────────

const fmt = (n, digits = 2) =>
  new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);

const fmtUsd = (n) => "$" + fmt(n);
const fmtCad = (n) => "CA$" + fmt(n);
const fmtPct = (n) => (n >= 0 ? "+" : "") + fmt(n, 2) + "%";

function plClass(n) {
  if (n > 0) return "positive";
  if (n < 0) return "negative";
  return "neutral";
}

function pillHtml(pct) {
  const cls = plClass(pct);
  return `<span class="change-pill ${cls}">${fmtPct(pct)}</span>`;
}

// ── modal helpers ──────────────────────────────────────────────────────────

function openModal(id) {
  document.getElementById(id).classList.add("open");
}
function closeModal(id) {
  document.getElementById(id).classList.remove("open");
}

document.querySelectorAll("[data-close]").forEach((btn) => {
  btn.addEventListener("click", () => closeModal(btn.dataset.close));
});
document.querySelectorAll(".modal-overlay").forEach((overlay) => {
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.classList.remove("open");
  });
});

document.getElementById("openStockModal").addEventListener("click", () => {
  document.getElementById("stockError").textContent = "";
  document.getElementById("stockForm").reset();
  document.getElementById("stockAvgPriceLabel").textContent = "Average Purchase Price (USD)";
  openModal("stockModal");
});

// Live label update: switch USD ↔ CAD as user types the ticker
document.getElementById("stockTicker").addEventListener("input", function () {
  const isCAD = this.value.trim().toUpperCase().endsWith(".TO");
  document.getElementById("stockAvgPriceLabel").textContent =
    "Average Purchase Price (" + (isCAD ? "CAD" : "USD") + ")";
});
document.getElementById("openCryptoModal").addEventListener("click", () => {
  document.getElementById("cryptoError").textContent = "";
  document.getElementById("cryptoForm").reset();
  openModal("cryptoModal");
});
document.getElementById("openGoalModal").addEventListener("click", () => {
  document.getElementById("goalError").textContent = "";
  openModal("goalModal");
});
document.getElementById("openWatchlistModal").addEventListener("click", () => {
  document.getElementById("watchlistError").textContent = "";
  document.getElementById("watchlistForm").reset();
  openModal("watchlistModal");
});

// ── currency toggle ────────────────────────────────────────────────────────

function applyCurrencyMode(mode) {
  currencyMode = mode;
  localStorage.setItem("currencyMode", mode);
  document.querySelectorAll(".currency-opt").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.currency === mode);
  });
  if (lastData) renderSummary(lastData);
}

// Initialise toggle state from localStorage before first fetch
applyCurrencyMode(currencyMode);

document.getElementById("currencyToggle").addEventListener("click", (e) => {
  const btn = e.target.closest(".currency-opt");
  if (btn) applyCurrencyMode(btn.dataset.currency);
});

// ── compact mode ───────────────────────────────────────────────────────────

const compactToggle = document.getElementById("compactToggle");

function applyCompact(on) {
  document.body.classList.toggle("compact", on);
  compactToggle.textContent = on ? "⊞ Normal" : "⊟ Compact";
}

applyCompact(localStorage.getItem("compactMode") === "1");

compactToggle.addEventListener("click", () => {
  const next = !document.body.classList.contains("compact");
  localStorage.setItem("compactMode", next ? "1" : "0");
  applyCompact(next);
});

// ── rendering helpers ──────────────────────────────────────────────────────

function renderStocks(stocks) {
  const tbody = document.getElementById("stocksBody");
  if (!stocks.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="10">No stocks added yet</td></tr>';
    return;
  }
  tbody.innerHTML = stocks
    .map((s) => {
      const isCAD = s.purchase_currency === "CAD";
      const fmtNative = isCAD ? fmtCad : fmtUsd;
      return `
    <tr>
      <td><span class="ticker-badge">${s.name}</span></td>
      <td>${fmt(s.shares, 4)}</td>
      <td>${fmtNative(s.avg_purchase_price)}</td>
      <td>${fmtNative(s.current_price)}</td>
      <td>${fmtUsd(s.current_value_usd)}</td>
      <td>${fmtCad(s.current_value_cad)}</td>
      <td class="${plClass(s.profit_loss_usd)}">${fmtUsd(s.profit_loss_usd)}</td>
      <td class="${plClass(s.profit_loss_cad)}">${fmtCad(s.profit_loss_cad)}</td>
      <td>${pillHtml(s.percent_change)}</td>
      <td><button class="btn-remove" onclick="removeStock('${s.ticker}')">Remove</button></td>
    </tr>`;
    })
    .join("");
}

function renderCrypto(crypto) {
  const tbody = document.getElementById("cryptoBody");
  if (!crypto.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="10">No crypto added yet</td></tr>';
    return;
  }
  tbody.innerHTML = crypto
    .map(
      (c) => `
    <tr>
      <td><span class="ticker-badge">${c.name}</span></td>
      <td>${fmt(c.amount, 6)}</td>
      <td>${fmtUsd(c.avg_purchase_price)}</td>
      <td>${fmtUsd(c.current_price)}</td>
      <td>${fmtUsd(c.current_value_usd)}</td>
      <td>${fmtCad(c.current_value_cad)}</td>
      <td class="${plClass(c.profit_loss_usd)}">${fmtUsd(c.profit_loss_usd)}</td>
      <td class="${plClass(c.profit_loss_cad)}">${fmtCad(c.profit_loss_cad)}</td>
      <td>${pillHtml(c.percent_change)}</td>
      <td><button class="btn-remove" onclick="removeCrypto('${c.coin_id}')">Remove</button></td>
    </tr>`
    )
    .join("");
}

function renderSummary(data) {
  const allHoldings = [...data.stocks, ...data.crypto];

  // P&L totals in both currencies
  const totalPlUsd = allHoldings.reduce((sum, h) => sum + h.profit_loss_usd, 0);
  const totalPlCad = allHoldings.reduce((sum, h) => sum + h.profit_loss_cad, 0);
  const totalCost = data.total_usd - totalPlUsd;
  const totalPlPct = totalCost > 0 ? (totalPlUsd / totalCost) * 100 : 0;

  const cad = currencyMode === "CAD";

  // Total Portfolio
  document.getElementById("totalUsd").textContent = cad ? fmtCad(data.total_cad)         : fmtUsd(data.total_usd);
  document.getElementById("totalCad").textContent = cad ? fmtUsd(data.total_usd)          : fmtCad(data.total_cad);

  // Stocks Value
  document.getElementById("stocksUsd").textContent = cad ? fmtCad(data.total_stocks_cad) : fmtUsd(data.total_stocks_usd);
  document.getElementById("stocksCad").textContent = cad ? fmtUsd(data.total_stocks_usd) : fmtCad(data.total_stocks_cad);

  // Crypto Value
  document.getElementById("cryptoUsd").textContent = cad ? fmtCad(data.total_crypto_cad) : fmtUsd(data.total_crypto_usd);
  document.getElementById("cryptoCad").textContent = cad ? fmtUsd(data.total_crypto_usd) : fmtCad(data.total_crypto_cad);

  // Total P&L — primary flips currency, secondary always shows %
  const plEl = document.getElementById("totalPl");
  plEl.textContent = cad ? fmtCad(totalPlCad) : fmtUsd(totalPlUsd);
  plEl.className = "card-value " + plClass(totalPlUsd);

  const plPctEl = document.getElementById("totalPlPct");
  plPctEl.textContent = fmtPct(totalPlPct);
  plPctEl.className = "card-sub " + plClass(totalPlUsd);

  // FX
  document.getElementById("fxRate").textContent = `USD/CAD: ${fmt(data.usd_to_cad, 4)}`;

  // timestamp
  const ts = new Date(data.last_updated + "Z");
  document.getElementById("lastUpdated").textContent =
    "Updated " + ts.toLocaleTimeString();
}

function renderGoal(goal, totalUsd, totalCad, usdToCad) {
  const { target, currency } = goal;
  if (!target) {
    document.getElementById("goalMeta").textContent = "Set a target to track your progress";
    document.getElementById("goalBar").style.width = "0%";
    document.getElementById("goalCurrent").textContent = fmtUsd(totalUsd);
    document.getElementById("goalTarget").textContent = "Goal: not set";
    return;
  }
  const current = currency === "CAD" ? totalCad : totalUsd;
  const pct = Math.min((current / target) * 100, 100);
  const fmtFn = currency === "CAD" ? fmtCad : fmtUsd;
  document.getElementById("goalBar").style.width = pct.toFixed(2) + "%";
  document.getElementById("goalMeta").textContent =
    `${fmt(pct, 1)}% of your ${fmtFn(target)} ${currency} goal reached`;
  document.getElementById("goalCurrent").textContent = fmtFn(current);
  document.getElementById("goalTarget").textContent = `Goal: ${fmtFn(target)}`;
}

function renderWatchlist(items) {
  const tbody = document.getElementById("watchlistBody");
  if (!items.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="5">No tickers in watchlist yet</td></tr>';
    return;
  }
  tbody.innerHTML = items.map((w) => `
    <tr>
      <td><span class="ticker-badge">${w.name}</span></td>
      <td>${fmtUsd(w.current_price)}</td>
      <td>${fmtCad(w.current_price_cad)}</td>
      <td>${pillHtml(w.day_change_pct)}</td>
      <td><button class="btn-remove" onclick="removeWatchlistItem('${w.ticker}')">Remove</button></td>
    </tr>`).join("");
}

// ── Chart.js palettes ──────────────────────────────────────────────────────

const PALETTE = [
  "#d4890a", "#00c87a", "#4d9eff", "#ff3d4f", "#a78bfa",
  "#f0a020", "#00d97e", "#60a5fa", "#fb7185", "#c4b5fd",
  "#b36800", "#00a865", "#3b82f6", "#e11d48", "#8b5cf6",
];

function renderPieChart(allHoldings) {
  const pieEmpty = document.getElementById("pieEmpty");
  const canvas = document.getElementById("pieChart");

  if (!allHoldings.length) {
    pieEmpty.style.display = "block";
    canvas.style.display = "none";
    if (pieChart) { pieChart.destroy(); pieChart = null; }
    return;
  }
  pieEmpty.style.display = "none";
  canvas.style.display = "block";

  const labels = allHoldings.map((h) => h.name || h.ticker);
  const values = allHoldings.map((h) => h.current_value_usd);
  const colors = PALETTE.slice(0, labels.length);

  if (pieChart) {
    pieChart.data.labels = labels;
    pieChart.data.datasets[0].data = values;
    pieChart.data.datasets[0].backgroundColor = colors;
    pieChart.update();
    return;
  }
  pieChart = new Chart(canvas, {
    type: "doughnut",
    data: {
      labels,
      datasets: [{ data: values, backgroundColor: colors, borderWidth: 2, borderColor: "#07090d" }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      plugins: {
        legend: {
          position: "bottom",
          labels: { color: "#6a7d96", font: { size: 11, family: "'Syne', sans-serif" }, padding: 12, boxWidth: 10 },
        },
        tooltip: {
          callbacks: {
            label: (ctx) => ` ${ctx.label}: ${fmtUsd(ctx.parsed)} (${fmt((ctx.parsed / values.reduce((a, b) => a + b, 0)) * 100, 1)}%)`,
          },
        },
      },
    },
  });
}

function renderLineChart(history) {
  const lineEmpty = document.getElementById("lineEmpty");
  const canvas = document.getElementById("lineChart");

  if (history.length < 2) {
    lineEmpty.style.display = "block";
    canvas.style.display = "none";
    if (lineChart) { lineChart.destroy(); lineChart = null; }
    return;
  }
  lineEmpty.style.display = "none";
  canvas.style.display = "block";

  const labels = history.map((h) => {
    const d = new Date(h.timestamp + "Z");
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  });
  const values = history.map((h) => h.value_usd);

  if (lineChart) {
    lineChart.data.labels = labels;
    lineChart.data.datasets[0].data = values;
    lineChart.update();
    return;
  }
  lineChart = new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: "Portfolio Value (USD)",
        data: values,
        borderColor: "#00c87a",
        backgroundColor: "rgba(0,200,122,.05)",
        borderWidth: 2,
        pointRadius: 3,
        pointHoverRadius: 5,
        fill: true,
        tension: 0.3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => ` ${fmtUsd(ctx.parsed.y)}`,
          },
        },
      },
      scales: {
        x: { grid: { color: "#1c2535" }, ticks: { color: "#3d4f63", maxTicksLimit: 10, font: { family: "'JetBrains Mono', monospace", size: 11 } } },
        y: {
          grid: { color: "#1c2535" },
          ticks: {
            color: "#3d4f63",
            font: { family: "'JetBrains Mono', monospace", size: 11 },
            callback: (v) => "$" + (v >= 1000 ? (v / 1000).toFixed(1) + "k" : v),
          },
        },
      },
    },
  });
}

// ── main data fetch ────────────────────────────────────────────────────────

async function fetchPortfolio() {
  try {
    const res = await fetch("/api/portfolio");
    const data = await res.json();

    lastData = data;
    renderSummary(data);
    renderStocks(data.stocks);
    renderCrypto(data.crypto);
    renderWatchlist(data.watchlist || []);
    renderGoal(data.savings_goal, data.total_usd, data.total_cad, data.usd_to_cad);

    const allHoldings = [...data.stocks, ...data.crypto];
    renderPieChart(allHoldings);
    renderLineChart(data.portfolio_history || []);
  } catch (err) {
    console.error("Failed to fetch portfolio:", err);
  }
}

// ── auto-refresh ───────────────────────────────────────────────────────────

function startCountdown() {
  countdownValue = 60;
  document.getElementById("countdown").textContent = countdownValue;
  clearInterval(countdownInterval);
  countdownInterval = setInterval(() => {
    countdownValue -= 1;
    document.getElementById("countdown").textContent = countdownValue;
    if (countdownValue <= 0) {
      countdownValue = 60;
      fetchPortfolio();
    }
  }, 1000);
}

// ── remove actions ─────────────────────────────────────────────────────────

async function removeStock(ticker) {
  if (!confirm(`Remove ${ticker} from your portfolio?`)) return;
  await fetch(`/api/stocks/${ticker}`, { method: "DELETE" });
  fetchPortfolio();
}
window.removeStock = removeStock;

async function removeWatchlistItem(ticker) {
  if (!confirm(`Remove ${ticker.replace(".TO", "")} from your watchlist?`)) return;
  await fetch(`/api/watchlist/${ticker}`, { method: "DELETE" });
  fetchPortfolio();
}
window.removeWatchlistItem = removeWatchlistItem;

async function removeCrypto(coinId) {
  if (!confirm(`Remove ${coinId} from your portfolio?`)) return;
  await fetch(`/api/crypto/${coinId}`, { method: "DELETE" });
  fetchPortfolio();
}
window.removeCrypto = removeCrypto;

// ── form submissions ───────────────────────────────────────────────────────

document.getElementById("stockForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = document.getElementById("stockError");
  const submitBtn = document.getElementById("stockSubmitBtn");
  errEl.textContent = "";
  submitBtn.classList.add("loading");
  submitBtn.textContent = "Adding…";

  const payload = {
    ticker: document.getElementById("stockTicker").value,
    shares: document.getElementById("stockShares").value,
    avg_purchase_price: document.getElementById("stockAvgPrice").value,
  };

  try {
    const res = await fetch("/api/stocks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      errEl.textContent = data.error;
    } else {
      closeModal("stockModal");
      fetchPortfolio();
      startCountdown();
    }
  } catch {
    errEl.textContent = "Network error. Please try again.";
  } finally {
    submitBtn.classList.remove("loading");
    submitBtn.textContent = "Add Stock";
  }
});

document.getElementById("cryptoForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = document.getElementById("cryptoError");
  const submitBtn = document.getElementById("cryptoSubmitBtn");
  errEl.textContent = "";
  submitBtn.classList.add("loading");
  submitBtn.textContent = "Adding…";

  const payload = {
    coin_id: document.getElementById("cryptoCoinId").value,
    coin_name: document.getElementById("cryptoCoinName").value,
    amount: document.getElementById("cryptoAmount").value,
    avg_purchase_price: document.getElementById("cryptoAvgPrice").value,
  };

  try {
    const res = await fetch("/api/crypto", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      errEl.textContent = data.error;
    } else {
      closeModal("cryptoModal");
      fetchPortfolio();
      startCountdown();
    }
  } catch {
    errEl.textContent = "Network error. Please try again.";
  } finally {
    submitBtn.classList.remove("loading");
    submitBtn.textContent = "Add Crypto";
  }
});

document.getElementById("goalForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = document.getElementById("goalError");
  errEl.textContent = "";

  const payload = {
    target: document.getElementById("goalAmount").value,
    currency: document.getElementById("goalCurrency").value,
  };

  try {
    const res = await fetch("/api/savings-goal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      errEl.textContent = data.error;
    } else {
      closeModal("goalModal");
      fetchPortfolio();
    }
  } catch {
    errEl.textContent = "Network error. Please try again.";
  }
});

document.getElementById("watchlistForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = document.getElementById("watchlistError");
  const submitBtn = document.getElementById("watchlistSubmitBtn");
  errEl.textContent = "";
  submitBtn.classList.add("loading");
  submitBtn.textContent = "Adding…";

  try {
    const res = await fetch("/api/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker: document.getElementById("watchlistTicker").value }),
    });
    const data = await res.json();
    if (!res.ok) {
      errEl.textContent = data.error;
    } else {
      closeModal("watchlistModal");
      fetchPortfolio();
      startCountdown();
    }
  } catch {
    errEl.textContent = "Network error. Please try again.";
  } finally {
    submitBtn.classList.remove("loading");
    submitBtn.textContent = "Add to Watchlist";
  }
});

// ── init ───────────────────────────────────────────────────────────────────

fetchPortfolio();
startCountdown();
