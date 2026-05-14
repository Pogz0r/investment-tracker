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
let historyRange = localStorage.getItem("historyRange") || "7d";
let allocationView = localStorage.getItem("allocationView") || "pie";
let simulatorAdjustments = {};

// ── helpers ────────────────────────────────────────────────────────────────

const fmt = (n, digits = 2) =>
  new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);

const fmtUsd = (n) => "$" + fmt(n);
const fmtCad = (n) => "CA$" + fmt(n);
const fmtPhp = (n) => "\u20b1" + fmt(n);
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

function getCurrencyValue(item) {
  const usd = item.current_value_usd || 0;
  if (currencyMode === "PHP") return item.current_value_php ?? usd * (lastData?.usd_to_php || 0);
  if (currencyMode === "CAD") return item.current_value_cad ?? usd * (lastData?.usd_to_cad || 0);
  return usd;
}

function getCurrencyFormatter() {
  if (currencyMode === "PHP") return fmtPhp;
  if (currencyMode === "CAD") return fmtCad;
  return fmtUsd;
}

function getCurrencyLabel() {
  return currencyMode === "PHP" ? "PHP" : currencyMode === "CAD" ? "CAD" : "USD";
}

function getPortfolioHoldings(data) {
  const allHoldings = [...(data?.stocks || []), ...(data?.crypto || [])];
  if ((data?.total_liquid_usd || 0) > 0) {
    allHoldings.unshift({
      name: "Liquid Cash",
      current_value_usd: data.total_liquid_usd,
      current_value_cad: data.total_liquid_cad,
      current_value_php: data.total_liquid_usd * (data.usd_to_php || 0),
      type: "cash",
    });
  }
  return allHoldings;
}

function getHoldingLabel(item) {
  return item.name || item.ticker || item.coin_id || "Holding";
}

function getAllocationRows(holdings) {
  const total = holdings.reduce((sum, item) => sum + getCurrencyValue(item), 0);
  return [...holdings]
    .map((item, index) => {
      const value = getCurrencyValue(item);
      return {
        label: getHoldingLabel(item),
        value,
        pct: total > 0 ? (value / total) * 100 : 0,
        color: PALETTE[index % PALETTE.length],
      };
    })
    .filter((row) => row.value > 0)
    .sort((a, b) => b.value - a.value);
}

function getHoldingKey(item) {
  return `${item.coin_id ? "crypto" : "stock"}:${item.ticker || item.coin_id || item.name}`;
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

function openEditStockModal(ticker) {
  const stock = (lastData?.stocks || []).find((s) => s.ticker === ticker);
  if (!stock) return;
  document.getElementById('editStockError').textContent = '';
  document.getElementById('editStockTicker').value = stock.ticker;
  document.getElementById('editStockTickerDisplay').value = stock.name;
  document.getElementById('editStockShares').value = stock.shares;
  document.getElementById('editStockAvgPrice').value = stock.avg_purchase_price;
  document.getElementById('editStockAvgPriceLabel').textContent = `Average Purchase Price (${stock.purchase_currency || 'USD'})`;
  openModal('editStockModal');
}
window.openEditStockModal = openEditStockModal;

function openEditCryptoModal(coinId) {
  const crypto = (lastData?.crypto || []).find((c) => c.coin_id === coinId);
  if (!crypto) return;
  document.getElementById('editCryptoError').textContent = '';
  document.getElementById('editCryptoCoinId').value = crypto.coin_id;
  document.getElementById('editCryptoCoinName').value = crypto.name;
  document.getElementById('editCryptoAmount').value = crypto.amount;
  document.getElementById('editCryptoAvgPrice').value = crypto.avg_purchase_price;
  openModal('editCryptoModal');
}
window.openEditCryptoModal = openEditCryptoModal;
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
  if (lastData) {
    renderSummary(lastData);
    renderGoal(lastData.savings_goal, lastData.total_usd, lastData.total_cad, lastData.total_php, lastData.usd_to_cad, lastData.usd_to_php);
    renderAllocation(lastData);
    renderLineChart(lastData.portfolio_history || []);
    renderPriceSimulator(lastData);
  }
}

// Initialise toggle state from localStorage before first fetch
applyCurrencyMode(currencyMode);

document.getElementById("currencyToggle").addEventListener("click", (e) => {
  const btn = e.target.closest(".currency-opt");
  if (btn) applyCurrencyMode(btn.dataset.currency);
});

function applyHistoryRange(range) {
  historyRange = range;
  localStorage.setItem("historyRange", range);
  document.querySelectorAll(".range-opt").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.range === range);
  });
  if (lastData) renderLineChart(lastData.portfolio_history || []);
}

document.getElementById("historyRangeToggle").addEventListener("click", (e) => {
  const btn = e.target.closest(".range-opt");
  if (btn) applyHistoryRange(btn.dataset.range);
});

applyHistoryRange(historyRange);

function applyAllocationView(view) {
  allocationView = view === "bar" ? "bar" : "pie";
  localStorage.setItem("allocationView", allocationView);
  document.querySelectorAll(".allocation-opt").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === allocationView);
  });
  const pieView = document.getElementById("allocationPieView");
  const barView = document.getElementById("allocationBarView");
  if (pieView) pieView.hidden = allocationView !== "pie";
  if (barView) barView.hidden = allocationView !== "bar";
  if (lastData) renderAllocation(lastData);
  if (allocationView === "pie" && pieChart) requestAnimationFrame(() => pieChart.resize());
}

document.getElementById("allocationToggle")?.addEventListener("click", (e) => {
  const btn = e.target.closest(".allocation-opt");
  if (btn) applyAllocationView(btn.dataset.view);
});

applyAllocationView(allocationView);

const priceSimulatorToggle = document.getElementById("priceSimulatorToggle");
if (priceSimulatorToggle) {
  priceSimulatorToggle.addEventListener("click", () => {
    const card = document.getElementById("priceSimulator");
    const body = document.getElementById("priceSimulatorBody");
    const expanded = priceSimulatorToggle.getAttribute("aria-expanded") === "true";
    priceSimulatorToggle.setAttribute("aria-expanded", String(!expanded));
    card.classList.toggle("is-collapsed", expanded);
    card.classList.toggle("is-expanded", !expanded);
    body.hidden = expanded;
    if (!expanded && lastData) renderPriceSimulator(lastData);
  });
}

document.getElementById("simulatorRows")?.addEventListener("input", (e) => {
  const input = e.target.closest(".simulator-slider");
  if (!input) return;
  simulatorAdjustments[input.dataset.key] = Number(input.value);
  updateSimulatorDisplay();
});

document.getElementById("resetSimulator")?.addEventListener("click", () => {
  simulatorAdjustments = {};
  if (lastData) renderPriceSimulator(lastData);
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
      <td class="row-actions"><button class="btn-action btn-edit" onclick="openEditStockModal('${s.ticker}')">Edit</button><button class="btn-remove" onclick="removeStock('${s.ticker}')">Remove</button></td>
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
      <td class="row-actions"><button class="btn-action btn-edit" onclick="openEditCryptoModal('${c.coin_id}')">Edit</button><button class="btn-remove" onclick="removeCrypto('${c.coin_id}')">Remove</button></td>
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
  const php = currencyMode === "PHP";

  // Total Portfolio
  document.getElementById("totalUsd").textContent = php ? fmtPhp(data.total_php)         : cad ? fmtCad(data.total_cad)         : fmtUsd(data.total_usd);
  document.getElementById("totalCad").textContent = php ? fmtUsd(data.total_usd)          : cad ? fmtUsd(data.total_usd)          : fmtCad(data.total_cad);

  // Stocks Value
  document.getElementById("stocksUsd").textContent = php ? fmtPhp(data.total_stocks_usd * (data.usd_to_php || 55.8)) : cad ? fmtCad(data.total_stocks_cad) : fmtUsd(data.total_stocks_usd);
  document.getElementById("stocksCad").textContent = php ? fmtUsd(data.total_stocks_usd) : cad ? fmtUsd(data.total_stocks_usd) : fmtCad(data.total_stocks_cad);

  // Crypto Value
  document.getElementById("cryptoUsd").textContent = php ? fmtPhp(data.total_crypto_usd * (data.usd_to_php || 55.8)) : cad ? fmtCad(data.total_crypto_cad) : fmtUsd(data.total_crypto_usd);
  document.getElementById("cryptoCad").textContent = php ? fmtUsd(data.total_crypto_usd) : cad ? fmtUsd(data.total_crypto_usd) : fmtCad(data.total_crypto_cad);

  // Liquid Cash
  document.getElementById("liquidCashCad").textContent = fmtCad(data.total_liquid_cad || 0);
  document.getElementById("liquidCashUsd").textContent = fmtUsd(data.total_liquid_usd || 0);

  // Total P&L — primary flips currency, secondary always shows %
  const plEl = document.getElementById("totalPl");
  const plPhp = totalPlUsd * (data.usd_to_php || 55.8);
  plEl.textContent = php ? fmtPhp(plPhp) : cad ? fmtCad(totalPlCad) : fmtUsd(totalPlUsd);
  plEl.className = "card-value " + plClass(totalPlUsd);

  const plPctEl = document.getElementById("totalPlPct");
  plPctEl.textContent = fmtPct(totalPlPct);
  plPctEl.className = "card-sub " + plClass(totalPlUsd);

  // FX
  const cadRate = fmt(data.usd_to_cad, 4);
  const phpRate = fmt(data.usd_to_php || 55.8, 4);
  document.getElementById("fxRate").textContent = php ? `USD/PHP: ${phpRate}` : `USD/CAD: ${cadRate}`;

  // timestamp
  const ts = new Date(data.last_updated + "Z");
  document.getElementById("lastUpdated").textContent =
    "Updated " + ts.toLocaleTimeString();
}

function renderGoal(goal, totalUsd, totalCad, totalPhp, usdToCad, usdToPhp) {
  const { target, currency } = goal;
  if (!target) {
    document.getElementById("goalMeta").textContent = "Set a target to track your progress";
    document.getElementById("goalBar").style.width = "0%";
    document.getElementById("goalCurrent").textContent = currencyMode === "PHP" ? fmtPhp(totalPhp || totalUsd * (usdToPhp || 55.8)) : currencyMode === "CAD" ? fmtCad(totalCad) : fmtUsd(totalUsd);
    document.getElementById("goalTarget").textContent = "Goal: not set";
    return;
  }
  const current = currency === "CAD" ? totalCad : totalUsd;
  const currentPhp = totalPhp || totalUsd * (usdToPhp || 55.8);
  const pct = Math.min((current / target) * 100, 100);
  let fmtFn, displayCurrent, displayTarget;
  if (currencyMode === "PHP") {
    fmtFn = fmtPhp;
    displayCurrent = currentPhp;
    displayTarget = target * (usdToPhp || 55.8);
  } else {
    fmtFn = currency === "CAD" ? fmtCad : fmtUsd;
    displayCurrent = current;
    displayTarget = target;
  }
  document.getElementById("goalBar").style.width = pct.toFixed(2) + "%";
  document.getElementById("goalMeta").textContent =
    `${fmt(pct, 1)}% of your ${fmtFn(displayTarget)} ${currency} goal reached`;
  document.getElementById("goalCurrent").textContent = fmtFn(displayCurrent);
  document.getElementById("goalTarget").textContent = `Goal: ${fmtFn(displayTarget)}`;
}

function renderWatchlist(items) {
  const tbody = document.getElementById("watchlistBody");
  if (!items.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="6">No tickers in watchlist yet</td></tr>';
    return;
  }
  const php = currencyMode === "PHP";
  const usdToPhp = lastData?.usd_to_php || 55.8;
  tbody.innerHTML = items.map((w) => `
    <tr>
      <td><span class="ticker-badge">${w.name}</span></td>
      <td>${php ? fmtPhp(w.current_price * usdToPhp) : fmtUsd(w.current_price)}</td>
      <td>${php ? fmtUsd(w.current_price) : fmtCad(w.current_price_cad)}</td>
      ${php ? `<td>${fmtPhp((w.current_price_php || w.current_price * usdToPhp))}</td>` : ""}
      <td>${pillHtml(w.day_change_pct)}</td>
      <td><button class="btn-remove" onclick="removeWatchlistItem('${w.ticker}')">Remove</button></td>
    </tr>`).join("");
}

function renderLiquidCash(entries, usdToCad) {
  const tbody = document.getElementById("liquidCashBody");
  if (!entries || !entries.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="4">No cash entries yet</td></tr>';
    return;
  }
  tbody.innerHTML = entries.map((e) => `
    <tr>
      <td><span class="ticker-badge">${e.label}</span></td>
      <td>${fmtCad(e.amount)}</td>
      <td>${fmtUsd(e.amount / usdToCad)}</td>
      <td class="row-actions">
        <button class="btn-action btn-edit" onclick="openEditLiquidCashModal(${e.id})">Edit</button>
        <button class="btn-remove" onclick="removeLiquidCash(${e.id})">Remove</button>
      </td>
    </tr>`).join("");
}

function getSimulatorBaseTotal(data) {
  if (currencyMode === "PHP") return data.total_php || data.total_usd * (data.usd_to_php || 55.8);
  if (currencyMode === "CAD") return data.total_cad || data.total_usd * (data.usd_to_cad || 1);
  return data.total_usd || 0;
}

function getSimulatorHoldings(data) {
  return [...(data?.stocks || []), ...(data?.crypto || [])]
    .map((item) => ({
      key: getHoldingKey(item),
      label: getHoldingLabel(item),
      value: getCurrencyValue(item),
    }))
    .filter((item) => item.value > 0)
    .sort((a, b) => b.value - a.value)
    .slice(0, 10);
}

function renderPriceSimulator(data) {
  const rowsEl = document.getElementById("simulatorRows");
  if (!rowsEl) return;

  const holdings = getSimulatorHoldings(data);
  if (!holdings.length) {
    rowsEl.innerHTML = '<div class="simulator-empty">Add holdings to enable the simulator.</div>';
    updateSimulatorDisplay();
    return;
  }

  const fmtValue = getCurrencyFormatter();
  rowsEl.innerHTML = holdings.map((holding) => {
    const pct = simulatorAdjustments[holding.key] ?? 0;
    return `
      <div class="simulator-row" data-key="${holding.key}" data-base-value="${holding.value}">
        <div class="simulator-holding">
          <span class="ticker-badge">${holding.label}</span>
          <small>${fmtValue(holding.value)}</small>
        </div>
        <input class="simulator-slider" type="range" min="-100" max="200" step="1" value="${pct}" data-key="${holding.key}" aria-label="${holding.label} price change">
        <div class="simulator-result">
          <span class="simulator-row-value">${fmtValue(holding.value * (1 + pct / 100))}</span>
          <strong class="simulator-row-pct ${plClass(pct)}">${fmtPct(pct)}</strong>
        </div>
      </div>
    `;
  }).join("");

  updateSimulatorDisplay();
}

function updateSimulatorDisplay() {
  if (!lastData) return;
  const rows = [...document.querySelectorAll(".simulator-row")];
  const fmtValue = getCurrencyFormatter();
  const baseTotal = getSimulatorBaseTotal(lastData);
  let delta = 0;

  rows.forEach((row) => {
    const key = row.dataset.key;
    const baseValue = Number(row.dataset.baseValue || 0);
    const pct = simulatorAdjustments[key] ?? 0;
    const simulatedValue = baseValue * (1 + pct / 100);
    delta += simulatedValue - baseValue;

    const valueEl = row.querySelector(".simulator-row-value");
    const pctEl = row.querySelector(".simulator-row-pct");
    if (valueEl) valueEl.textContent = fmtValue(simulatedValue);
    if (pctEl) {
      pctEl.textContent = fmtPct(pct);
      pctEl.className = `simulator-row-pct ${plClass(pct)}`;
    }
  });

  const simulatedTotal = baseTotal + delta;
  const deltaPct = baseTotal > 0 ? (delta / baseTotal) * 100 : 0;
  const totalEl = document.getElementById("simulatedPortfolioValue");
  const deltaEl = document.getElementById("simulatedPortfolioDelta");
  if (totalEl) totalEl.textContent = fmtValue(simulatedTotal);
  if (deltaEl) {
    deltaEl.textContent = `${delta >= 0 ? "+" : ""}${fmtValue(delta)} (${fmtPct(deltaPct)})`;
    deltaEl.className = plClass(delta);
  }
}

function openEditLiquidCashModal(id) {
  const entry = (lastData?.liquid_cash || []).find((e) => e.id === id);
  if (!entry) return;
  document.getElementById("editLiquidCashError").textContent = "";
  document.getElementById("editLiquidCashId").value = entry.id;
  document.getElementById("editLiquidCashLabel").value = entry.label;
  document.getElementById("editLiquidCashAmount").value = entry.amount;
  openModal("editLiquidCashModal");
}
window.openEditLiquidCashModal = openEditLiquidCashModal;

async function removeLiquidCash(id) {
  if (!confirm("Remove this cash entry?")) return;
  await fetch(`/api/liquid-cash/${id}`, { method: "DELETE" });
  fetchPortfolio();
}
window.removeLiquidCash = removeLiquidCash;

// ── Chart.js palettes ──────────────────────────────────────────────────────

const PALETTE = [
  "#d4890a", "#00c87a", "#4d9eff", "#ff3d4f", "#a78bfa",
  "#f0a020", "#00d97e", "#60a5fa", "#fb7185", "#c4b5fd",
  "#b36800", "#00a865", "#3b82f6", "#e11d48", "#8b5cf6",
];

function renderAllocation(data) {
  renderPieChart(getPortfolioHoldings(data));
  renderAllocationBars(data.stocks || [], data.crypto || []);
}

function renderAllocationLegend(rows, total) {
  const legend = document.getElementById("allocationLegend");
  if (!legend) return;
  const fmtValue = getCurrencyFormatter();
  if (!rows.length) {
    legend.innerHTML = '<div class="allocation-empty-note">No holdings yet</div>';
    return;
  }

  legend.innerHTML = rows.slice(0, 10).map((row) => `
    <div class="allocation-legend-row">
      <span class="allocation-dot" style="background:${row.color}"></span>
      <span class="allocation-name">${row.label}</span>
      <span class="allocation-pct">${fmt(row.pct, row.pct >= 10 ? 0 : 1)}%</span>
      <span class="allocation-value">${fmtValue(row.value)}</span>
    </div>
  `).join("");

  const remaining = rows.length - 10;
  if (remaining > 0) {
    const remainingValue = rows.slice(10).reduce((sum, row) => sum + row.value, 0);
    legend.insertAdjacentHTML("beforeend", `
      <div class="allocation-legend-row allocation-legend-row--muted">
        <span class="allocation-dot"></span>
        <span class="allocation-name">${remaining} more</span>
        <span class="allocation-pct">${fmt(total > 0 ? (remainingValue / total) * 100 : 0, 1)}%</span>
        <span class="allocation-value">${fmtValue(remainingValue)}</span>
      </div>
    `);
  }
}

function renderAllocationBars(stocks, crypto) {
  renderAllocationBarGroup("stock", stocks);
  renderAllocationBarGroup("crypto", crypto);
}

function renderAllocationBarGroup(kind, holdings) {
  const container = document.getElementById(kind === "stock" ? "stockAllocationBars" : "cryptoAllocationBars");
  const totalEl = document.getElementById(kind === "stock" ? "stockAllocationTotal" : "cryptoAllocationTotal");
  if (!container || !totalEl) return;

  const rows = getAllocationRows(holdings);
  const total = rows.reduce((sum, row) => sum + row.value, 0);
  const fmtValue = getCurrencyFormatter();
  totalEl.textContent = fmtValue(total);

  if (!rows.length) {
    container.innerHTML = `<div class="allocation-empty-note">No ${kind === "stock" ? "stock" : "crypto"} holdings yet</div>`;
    return;
  }

  container.innerHTML = rows.slice(0, 8).map((row) => `
    <div class="allocation-bar-row">
      <div class="allocation-bar-label">
        <span>${row.label}</span>
        <strong>${fmt(row.pct, row.pct >= 10 ? 0 : 1)}%</strong>
      </div>
      <div class="allocation-bar-track" title="${row.label}: ${fmtValue(row.value)}">
        <span class="allocation-bar-fill" style="width:${Math.max(row.pct, 1.5)}%; background:${row.color}"></span>
      </div>
      <div class="allocation-bar-value">${fmtValue(row.value)}</div>
    </div>
  `).join("");
}

function renderPieChart(allHoldings) {
  const pieEmpty = document.getElementById("pieEmpty");
  const canvas = document.getElementById("pieChart");

  if (!allHoldings.length) {
    pieEmpty.style.display = "block";
    canvas.style.display = "none";
    document.getElementById("allocationTotal").textContent = getCurrencyFormatter()(0);
    renderAllocationLegend([], 0);
    if (pieChart) { pieChart.destroy(); pieChart = null; }
    return;
  }
  pieEmpty.style.display = "none";
  canvas.style.display = "block";

  const labels = allHoldings.map((h) => h.name || h.ticker);
  const values = allHoldings.map((h) => getCurrencyValue(h));
  const colors = PALETTE.slice(0, labels.length);
  const fmtValue = getCurrencyFormatter();
  const currencyLabel = getCurrencyLabel();
  const total = values.reduce((a, b) => a + b, 0);
  const rows = getAllocationRows(allHoldings);
  const totalEl = document.getElementById("allocationTotal");
  if (totalEl) totalEl.textContent = fmtValue(total);
  renderAllocationLegend(rows, total);

  if (pieChart) {
    pieChart.data.labels = labels;
    pieChart.data.datasets[0].data = values;
    pieChart.data.datasets[0].backgroundColor = colors;
    pieChart.data.datasets[0].label = `Allocation (${currencyLabel})`;
    pieChart.options.plugins.tooltip.callbacks.label = (ctx) => {
      const pct = total > 0 ? (ctx.parsed / total) * 100 : 0;
      return ` ${ctx.label}: ${fmtValue(ctx.parsed)} (${fmt(pct, 1)}%)`;
    };
    pieChart.update();
    return;
  }
  pieChart = new Chart(canvas, {
    type: "doughnut",
    data: {
      labels,
      datasets: [{ label: `Allocation (${currencyLabel})`, data: values, backgroundColor: colors, borderWidth: 2, borderColor: "#07090d" }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "62%",
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const pct = total > 0 ? (ctx.parsed / total) * 100 : 0;
              return ` ${ctx.label}: ${fmtValue(ctx.parsed)} (${fmt(pct, 1)}%)`;
            },
          },
        },
      },
    },
  });
}

function getDailyCloses(history) {
  const closes = new Map();
  history.forEach((h) => {
    const d = new Date(h.timestamp + "Z");
    if (Number.isNaN(d.getTime())) return;
    const key = d.toISOString().slice(0, 10);
    const current = closes.get(key);
    if (!current || d > current.date) closes.set(key, { ...h, date: d, key });
  });
  return [...closes.values()].sort((a, b) => a.date - b.date);
}

function filterHistoryRange(points) {
  if (!points.length) return [];
  const last = points[points.length - 1].date;
  if (historyRange === "ytd") {
    const yearStart = new Date(Date.UTC(last.getUTCFullYear(), 0, 1));
    return points.filter((p) => p.date >= yearStart);
  }
  const days = historyRange === "30d" ? 30 : 7;
  const start = new Date(last);
  start.setUTCDate(start.getUTCDate() - (days - 1));
  return points.filter((p) => p.date >= start);
}

function renderLineChart(history) {
  const lineEmpty = document.getElementById("lineEmpty");
  const canvas = document.getElementById("lineChart");
  const cad = currencyMode === "CAD";
  const php = currencyMode === "PHP";
  const usdToPhp = lastData?.usd_to_php || 55.8;
  const fmtValue = php ? fmtPhp : cad ? fmtCad : fmtUsd;
  const axisPrefix = php ? "\u20b1" : cad ? "CA$" : "$";
  const currencyLabel = php ? "PHP" : cad ? "CAD" : "USD";
  const points = filterHistoryRange(getDailyCloses(history));

  if (points.length < 2) {
    lineEmpty.style.display = "block";
    canvas.style.display = "none";
    if (lineChart) { lineChart.destroy(); lineChart = null; }
    return;
  }
  lineEmpty.style.display = "none";
  canvas.style.display = "block";

  const labels = points.map((h) => {
    return h.date.toLocaleDateString("en-US", { timeZone: "UTC", month: "short", day: "numeric" });
  });
  const values = points.map((h) => php ? h.value_usd * usdToPhp : cad ? h.value_cad : h.value_usd);

  if (lineChart) {
    lineChart.data.labels = labels;
    lineChart.data.datasets[0].label = `Portfolio Value (${currencyLabel})`;
    lineChart.data.datasets[0].data = values;
    lineChart.options.plugins.tooltip.callbacks.label = (ctx) => ` ${fmtValue(ctx.parsed.y)}`;
    lineChart.options.scales.y.ticks.callback = (v) => axisPrefix + (v >= 1000 ? (v / 1000).toFixed(1) + "k" : v);
    lineChart.update();
    return;
  }
  lineChart = new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: `Portfolio Value (${currencyLabel})`,
        data: values,
        borderColor: "#00c87a",
        backgroundColor: "transparent",
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 5,
        fill: false,
        tension: 0.18,
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
            label: (ctx) => ` ${fmtValue(ctx.parsed.y)}`,
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
            callback: (v) => axisPrefix + (v >= 1000 ? (v / 1000).toFixed(1) + "k" : v),
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
    renderLiquidCash(data.liquid_cash || [], data.usd_to_cad);
    renderGoal(data.savings_goal, data.total_usd, data.total_cad, data.total_php, data.usd_to_cad, data.usd_to_php);

    renderAllocation(data);
    renderLineChart(data.portfolio_history || []);
    renderPriceSimulator(data);
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

document.getElementById("editStockForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = document.getElementById("editStockError");
  const submitBtn = document.getElementById("editStockSubmitBtn");
  errEl.textContent = "";
  submitBtn.classList.add("loading");
  submitBtn.textContent = "Saving…";

  const ticker = document.getElementById("editStockTicker").value;
  const payload = {
    shares: document.getElementById("editStockShares").value,
    avg_purchase_price: document.getElementById("editStockAvgPrice").value,
  };

  try {
    const res = await fetch(`/api/stocks/${ticker}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      errEl.textContent = data.error;
    } else {
      closeModal("editStockModal");
      fetchPortfolio();
      startCountdown();
    }
  } catch {
    errEl.textContent = "Network error. Please try again.";
  } finally {
    submitBtn.classList.remove("loading");
    submitBtn.textContent = "Save Changes";
  }
});

document.getElementById("editCryptoForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = document.getElementById("editCryptoError");
  const submitBtn = document.getElementById("editCryptoSubmitBtn");
  errEl.textContent = "";
  submitBtn.classList.add("loading");
  submitBtn.textContent = "Saving…";

  const coinId = document.getElementById("editCryptoCoinId").value;
  const payload = {
    amount: document.getElementById("editCryptoAmount").value,
    avg_purchase_price: document.getElementById("editCryptoAvgPrice").value,
  };

  try {
    const res = await fetch(`/api/crypto/${coinId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      errEl.textContent = data.error;
    } else {
      closeModal("editCryptoModal");
      fetchPortfolio();
      startCountdown();
    }
  } catch {
    errEl.textContent = "Network error. Please try again.";
  } finally {
    submitBtn.classList.remove("loading");
    submitBtn.textContent = "Save Changes";
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

document.getElementById("openLiquidCashModal").addEventListener("click", () => {
  document.getElementById("liquidCashError").textContent = "";
  document.getElementById("liquidCashForm").reset();
  openModal("liquidCashModal");
});

document.getElementById("liquidCashForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = document.getElementById("liquidCashError");
  const submitBtn = document.getElementById("liquidCashSubmitBtn");
  errEl.textContent = "";
  submitBtn.classList.add("loading");
  submitBtn.textContent = "Adding…";

  const payload = {
    label: document.getElementById("liquidCashLabel").value,
    amount: document.getElementById("liquidCashAmount").value,
  };

  try {
    const res = await fetch("/api/liquid-cash", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      errEl.textContent = data.error;
    } else {
      closeModal("liquidCashModal");
      fetchPortfolio();
    }
  } catch {
    errEl.textContent = "Network error. Please try again.";
  } finally {
    submitBtn.classList.remove("loading");
    submitBtn.textContent = "Add Cash";
  }
});

document.getElementById("editLiquidCashForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = document.getElementById("editLiquidCashError");
  const submitBtn = document.getElementById("editLiquidCashSubmitBtn");
  errEl.textContent = "";
  submitBtn.classList.add("loading");
  submitBtn.textContent = "Saving…";

  const id = document.getElementById("editLiquidCashId").value;
  const payload = {
    label: document.getElementById("editLiquidCashLabel").value,
    amount: document.getElementById("editLiquidCashAmount").value,
  };

  try {
    const res = await fetch(`/api/liquid-cash/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      errEl.textContent = data.error;
    } else {
      closeModal("editLiquidCashModal");
      fetchPortfolio();
    }
  } catch {
    errEl.textContent = "Network error. Please try again.";
  } finally {
    submitBtn.classList.remove("loading");
    submitBtn.textContent = "Save Changes";
  }
});



// ── table sorting helpers ──────────────────────────────────────────────────

function getSortIndicator(th) {
  return th.querySelector('.sort-indicator') || null;
}

function applySortIndicator(th, dir) {
  const el = getSortIndicator(th);
  if (el) el.textContent = dir === 'asc' ? '▲' : dir === 'desc' ? '▼' : '';
}

function clearSortIndicators(selector) {
  document.querySelectorAll(selector + ' th .sort-indicator').forEach(el => { el.textContent = ''; });
}

// ── sort state ──────────────────────────────────────────────────────────────

let stocksSortKey = null;
let stocksSortDir = 'desc';

let cryptoSortKey = null;
let cryptoSortDir = 'desc';

let watchlistSortKey = null;
let watchlistSortDir = 'desc';

// ── sort functions ──────────────────────────────────────────────────────────

function sortStocks(stocks, key, dir) {
  return [...stocks].sort((a, b) => {
    let av = a[key], bv = b[key];
    if (typeof av === 'string') { av = av.toLowerCase(); bv = bv.toLowerCase(); }
    else { av = parseFloat(av) || 0; bv = parseFloat(bv) || 0; }
    if (av < bv) return dir === 'asc' ? -1 : 1;
    if (av > bv) return dir === 'asc' ? 1 : -1;
    return 0;
  });
}

function sortCrypto(crypto, key, dir) {
  return [...crypto].sort((a, b) => {
    let av = a[key], bv = b[key];
    if (typeof av === 'string') { av = av.toLowerCase(); bv = bv.toLowerCase(); }
    else { av = parseFloat(av) || 0; bv = parseFloat(bv) || 0; }
    if (av < bv) return dir === 'asc' ? -1 : 1;
    if (av > bv) return dir === 'asc' ? 1 : -1;
    return 0;
  });
}

function sortWatchlist(items, key, dir) {
  return [...items].sort((a, b) => {
    let av = a[key], bv = b[key];
    if (key === 'day_change_pct') { av = parseFloat(av) || 0; bv = parseFloat(bv) || 0; }
    else if (typeof av === 'string') { av = av.toLowerCase(); bv = bv.toLowerCase(); }
    else { av = parseFloat(av) || 0; bv = parseFloat(bv) || 0; }
    if (av < bv) return dir === 'asc' ? -1 : 1;
    if (av > bv) return dir === 'asc' ? 1 : -1;
    return 0;
  });
}

// ── attach sort handlers ────────────────────────────────────────────────────

document.querySelectorAll('#stocksTable th[data-sort]').forEach(th => {
  th.style.cursor = 'pointer';
  th.addEventListener('click', () => {
    const key = th.dataset.sort;
    if (stocksSortKey === key) {
      stocksSortDir = stocksSortDir === 'asc' ? 'desc' : 'asc';
    } else {
      stocksSortKey = key;
      stocksSortDir = th.dataset.dir || 'asc';
      clearSortIndicators('#stocksTable');
    }
    const sorted = sortStocks(lastData?.stocks || [], key, stocksSortDir);
    renderStocks(sorted);
    applySortIndicator(th, stocksSortDir);
  });
});

document.querySelectorAll('#cryptoTable th[data-sort]').forEach(th => {
  th.style.cursor = 'pointer';
  th.addEventListener('click', () => {
    const key = th.dataset.sort;
    if (cryptoSortKey === key) {
      cryptoSortDir = cryptoSortDir === 'asc' ? 'desc' : 'asc';
    } else {
      cryptoSortKey = key;
      cryptoSortDir = th.dataset.dir || 'asc';
      clearSortIndicators('#cryptoTable');
    }
    const sorted = sortCrypto(lastData?.crypto || [], key, cryptoSortDir);
    renderCrypto(sorted);
    applySortIndicator(th, cryptoSortDir);
  });
});

document.querySelectorAll('.watchlist-table th[data-sort]').forEach(th => {
  th.style.cursor = 'pointer';
  th.addEventListener('click', () => {
    const key = th.dataset.sort;
    if (watchlistSortKey === key) {
      watchlistSortDir = watchlistSortDir === 'asc' ? 'desc' : 'asc';
    } else {
      watchlistSortKey = key;
      watchlistSortDir = th.dataset.dir || 'asc';
      clearSortIndicators('.watchlist-table');
    }
    const sorted = sortWatchlist(lastData?.watchlist || [], key, watchlistSortDir);
    renderWatchlist(sorted);
    applySortIndicator(th, watchlistSortDir);
  });
});


// ── init ───────────────────────────────────────────────────────────────────

fetchPortfolio();
startCountdown();
