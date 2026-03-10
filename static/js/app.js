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
  openModal("stockModal");
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

// ── rendering helpers ──────────────────────────────────────────────────────

function renderStocks(stocks) {
  const tbody = document.getElementById("stocksBody");
  if (!stocks.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="10">No stocks added yet</td></tr>';
    return;
  }
  tbody.innerHTML = stocks
    .map(
      (s) => `
    <tr>
      <td><span class="ticker-badge">${s.name}</span></td>
      <td>${fmt(s.shares, 4)}</td>
      <td>${fmtUsd(s.avg_purchase_price)}</td>
      <td>${fmtUsd(s.current_price)}</td>
      <td>${fmtUsd(s.current_value_usd)}</td>
      <td>${fmtCad(s.current_value_cad)}</td>
      <td class="${plClass(s.profit_loss_usd)}">${fmtUsd(s.profit_loss_usd)}</td>
      <td class="${plClass(s.profit_loss_cad)}">${fmtCad(s.profit_loss_cad)}</td>
      <td>${pillHtml(s.percent_change)}</td>
      <td><button class="btn-remove" onclick="removeStock('${s.ticker}')">Remove</button></td>
    </tr>`
    )
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
  // total P&L
  const allHoldings = [...data.stocks, ...data.crypto];
  const totalCost = allHoldings.reduce((sum, h) => {
    const qty = h.shares ?? h.amount;
    return sum + qty * h.avg_purchase_price;
  }, 0);
  const totalPl = data.total_usd - totalCost;
  const totalPlPct = totalCost > 0 ? (totalPl / totalCost) * 100 : 0;

  document.getElementById("totalUsd").textContent = fmtUsd(data.total_usd);
  document.getElementById("totalCad").textContent = fmtCad(data.total_cad);
  document.getElementById("stocksUsd").textContent = fmtUsd(data.total_stocks_usd);
  document.getElementById("stocksCad").textContent = fmtCad(data.total_stocks_cad);
  document.getElementById("cryptoUsd").textContent = fmtUsd(data.total_crypto_usd);
  document.getElementById("cryptoCad").textContent = fmtCad(data.total_crypto_cad);

  const plEl = document.getElementById("totalPl");
  plEl.textContent = fmtUsd(totalPl);
  plEl.className = "card-value " + plClass(totalPl);

  const plPctEl = document.getElementById("totalPlPct");
  plPctEl.textContent = fmtPct(totalPlPct);
  plPctEl.className = "card-sub " + plClass(totalPl);

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

// ── Chart.js palettes ──────────────────────────────────────────────────────

const PALETTE = [
  "#58a6ff", "#bc8cff", "#3fb950", "#f0883e", "#ff7b72",
  "#79c0ff", "#d2a8ff", "#56d364", "#ffa657", "#ffa198",
  "#1f6feb", "#8957e5", "#238636", "#d4a72c", "#da3633",
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
      datasets: [{ data: values, backgroundColor: colors, borderWidth: 2, borderColor: "#161b22" }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      plugins: {
        legend: {
          position: "bottom",
          labels: { color: "#8b949e", font: { size: 11 }, padding: 10, boxWidth: 12 },
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
        borderColor: "#58a6ff",
        backgroundColor: "rgba(88,166,255,.08)",
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
        x: { grid: { color: "#21262d" }, ticks: { color: "#8b949e", maxTicksLimit: 10 } },
        y: {
          grid: { color: "#21262d" },
          ticks: {
            color: "#8b949e",
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

    renderSummary(data);
    renderStocks(data.stocks);
    renderCrypto(data.crypto);
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

// ── init ───────────────────────────────────────────────────────────────────

fetchPortfolio();
startCountdown();
