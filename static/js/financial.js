/* ============================================================
   Financial Page — frontend
   ============================================================ */

let barChart = null;
let finSummary = null;
let finTransactions = [];
let finEntries = [];
let currentMonthFilter = "all";   // "all" or "YYYY-MM"

// ── formatters ─────────────────────────────────────────────────────────────

const fmt = (n, digits = 2) =>
  new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);

const fmtCad = (n) => "CA$" + fmt(Math.abs(n));
const fmtPct = (n) => (n >= 0 ? "+" : "") + fmt(n, 1) + "%";

function catClass(cat) {
  const map = {
    groceries: "cat-groceries", gas: "cat-gas", subscriptions: "cat-subscriptions",
    dining: "cat-dining", utilities: "cat-utilities", insurance: "cat-insurance",
    internal_transfer: "cat-internal-transfer",
  };
  return `cat-pill ${map[cat] || "cat-other"}`;
}

function catLabel(cat) {
  const map = {
    groceries: "Groceries", gas: "Gas", subscriptions: "Subscriptions",
    dining: "Dining", utilities: "Utilities", insurance: "Insurance",
    internal_transfer: "Internal Transfer",
    maintenance: "Maintenance", meals_entertainment: "Meals & Ent",
    office_general: "Office & Gen", telephone_utilities: "Tel & Utils",
    travel_expense: "Travel", interest_bank: "Bank Charges",
    taxes_licenses: "Taxes & Lic", other: "Other",
  };
  return map[cat] || cat;
}

// ── modal helpers ──────────────────────────────────────────────────────────

function openModal(id) { document.getElementById(id).classList.add("open"); }
function closeModal(id) { document.getElementById(id).classList.remove("open"); }

document.querySelectorAll("[data-close]").forEach((btn) => {
  btn.addEventListener("click", () => closeModal(btn.dataset.close));
});
document.querySelectorAll(".modal-overlay").forEach((overlay) => {
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.classList.remove("open");
  });
});

// Set default dates to today
function setDefaultDate(id) {
  const input = document.getElementById(id);
  if (input) input.value = new Date().toISOString().split("T")[0];
}

// ── rendering ─────────────────────────────────────────────────────────────

function renderSummary(data) {
  finSummary = data;
  const s = data.monthly_series || [];
  const last = s[s.length - 1] || {};

  // Monthly gross/net — show last month values (sum, not average)
  document.getElementById("monthlyGross").textContent = fmtCad(last.gross_income || 0);
  document.getElementById("avgPayPeriod").textContent = last.entry_count
    ? `${last.entry_count} ${last.entry_count === 1 ? 'entry' : 'entries'} this month`
    : "total for month";
  document.getElementById("monthlyNet").textContent = fmtCad(last.net_income || 0);

  const expenses = last.expenses || 0;
  document.getElementById("monthlyExpenses").textContent = expenses ? "-" + fmtCad(expenses) : "$0.00";
  const topCat = (data.top_expense_categories || [])[0];
  document.getElementById("topCategory").textContent = topCat
    ? `top: ${catLabel(topCat.category)}`
    : "top: —";

  const rate = last.savings_rate || data.overall_savings_rate || 0;
  const rateEl = document.getElementById("savingsRate");
  rateEl.textContent = fmt(rate, 1) + "%";
  rateEl.className = "card-value " + (rate >= 0 ? "positive" : "negative");

  const savingsSub = document.getElementById("savingsSub");
  savingsSub.textContent = data.overall_savings_rate !== undefined
    ? `overall: ${fmt(data.overall_savings_rate, 1)}%`
    : "net - expenses";

  document.getElementById("transactionCount").textContent = data.transaction_count || 0;
  document.getElementById("incomeEntriesSub").textContent = `${data.income_entry_count || 0} income entries`;

  renderBarChart(s);
}

function renderFilteredExpenses() {
  /* Recompute monthly expenses from finTransactions for the selected month.
     Updates the Monthly Expenses card and top category pill accordingly. */
  const filter = currentMonthFilter;
  let filtered = finTransactions;
  if (filter !== "all") {
    filtered = finTransactions.filter(t => t.date && t.date.startsWith(filter));
  }

  // Compute totals from filtered expense transactions (amount < 0, not internal_transfer)
  const expenses = filtered
    .filter(t => t.amount < 0 && t.category !== "internal_transfer")
    .reduce((sum, t) => sum + Math.abs(t.amount), 0);

  document.getElementById("monthlyExpenses").textContent = expenses ? "-" + fmtCad(expenses) : "$0.00";

  // Top category from filtered set
  const catMap = {};
  filtered.filter(t => t.amount < 0 && t.category !== "internal_transfer").forEach(t => {
    catMap[t.category] = (catMap[t.category] || 0) + Math.abs(t.amount);
  });
  const topEntry = Object.entries(catMap).sort((a, b) => b[1] - a[1])[0];
  document.getElementById("topCategory").textContent = topEntry
    ? `top: ${catLabel(topEntry[0])}`
    : "top: —";
}

function populateMonthFilter() {
  /* Build list of unique YYYY-MM months present in finTransactions and
     populate the <select id="monthFilter"> dropdown. */
  const select = document.getElementById("monthFilter");
  if (!select) return;

  // Collect months
  const monthSet = new Set();
  finTransactions.forEach(t => {
    if (t.date && t.date.length >= 7) {
      monthSet.add(t.date.substring(0, 7)); // "YYYY-MM"
    }
  });

  // Sort descending (newest first)
  const sorted = Array.from(monthSet).sort((a, b) => b.localeCompare(a));

  // Build options
  select.innerHTML = '<option value="all">All months</option>';
  sorted.forEach(m => {
    const [yr, mo] = m.split("-");
    const label = new Date(parseInt(yr), parseInt(mo) - 1, 1)
      .toLocaleString("en-US", { month: "short", year: "numeric" });
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = label;
    select.appendChild(opt);
  });

  // Restore selection if still valid
  if (currentMonthFilter !== "all" && ![...select.options].some(o => o.value === currentMonthFilter)) {
    currentMonthFilter = "all";
  }
  select.value = currentMonthFilter;
}

function onMonthFilterChange() {
  const select = document.getElementById("monthFilter");
  currentMonthFilter = select ? select.value : "all";
  renderFilteredExpenses();
  renderFilteredTransactions();
}

function renderBarChart(monthlySeries) {
  const canvas = document.getElementById("monthlyBarChart");
  const emptyEl = document.getElementById("barEmpty");
  const hasData = monthlySeries && monthlySeries.some(m => m.gross_income > 0 || m.expenses > 0);

  if (!hasData) {
    emptyEl.style.display = "block";
    canvas.style.display = "none";
    if (barChart) { barChart.destroy(); barChart = null; }
    return;
  }
  emptyEl.style.display = "none";
  canvas.style.display = "block";

  const labels = monthlySeries.map(m => m.label);
  const grossData = monthlySeries.map(m => m.gross_income);
  const netData = monthlySeries.map(m => m.net_income);
  const expenseData = monthlySeries.map(m => m.expenses);

  if (barChart) {
    barChart.data.labels = labels;
    barChart.data.datasets[0].data = grossData;
    barChart.data.datasets[1].data = netData;
    barChart.data.datasets[2].data = expenseData;
    barChart.update();
    return;
  }

  barChart = new Chart(canvas, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "Gross Income",
          data: grossData,
          backgroundColor: "rgba(0, 200, 122, 0.7)",
          borderColor: "#00c87a",
          borderWidth: 1,
          borderRadius: 4,
        },
        {
          label: "Net Income",
          data: netData,
          backgroundColor: "rgba(212, 137, 10, 0.7)",
          borderColor: "#d4890a",
          borderWidth: 1,
          borderRadius: 4,
        },
        {
          label: "Expenses",
          data: expenseData,
          backgroundColor: "rgba(255, 61, 79, 0.65)",
          borderColor: "#ff3d4f",
          borderWidth: 1,
          borderRadius: 4,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: "bottom",
          labels: {
            color: "#6a7d96",
            font: { size: 11, family: "'Syne', sans-serif" },
            padding: 12,
            boxWidth: 10,
          },
        },
        tooltip: {
          callbacks: {
            label: (ctx) => ` ${ctx.dataset.label}: CA$${fmt(ctx.parsed.y)}`,
          },
        },
      },
      scales: {
        x: {
          grid: { color: "#1c2535" },
          ticks: { color: "#3d4f63", font: { family: "'JetBrains Mono', monospace", size: 11 } },
        },
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

function renderIncomeEntries(entries) {
  finEntries = entries;
  const tbody = document.getElementById("incomeBody");
  if (!entries || !entries.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="6">No income entries yet</td></tr>';
    return;
  }
  tbody.innerHTML = entries.map(e => {
    const deductions = e.deductions || {};
    const dedTotal = Object.values(deductions).reduce((s, v) => s + (parseFloat(v) || 0), 0);
    return `
    <tr data-entry-id="${e.id}">
      <td>${e.pay_date}</td>
      <td><span class="ticker-badge">${e.employer}</span></td>
      <td class="positive">${fmtCad(e.gross_income)}</td>
      <td>${fmtCad(e.net_income)}</td>
      <td class="income-sub">${dedTotal > 0 ? "CA$" + fmt(dedTotal) : "—"}</td>
      <td class="row-actions">
        <button class="btn-edit" onclick="openIncomeEditModal(${e.id})">Edit</button>
        <button class="btn-remove" onclick="deleteIncomeEntry(${e.id})">Delete</button>
      </td>
    </tr>`;
  }).join("");
}

function renderTransactions(transactions) {
  finTransactions = transactions;
  populateMonthFilter();
  renderFilteredTransactions();
}

function renderFilteredTransactions() {
  const tbody = document.getElementById("transactionsBody");
  const filter = currentMonthFilter;
  let display = finTransactions;
  if (filter !== "all") {
    display = finTransactions.filter(t => t.date && t.date.startsWith(filter));
  }
  if (!display || !display.length) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="6">${filter === "all" ? "No transactions yet" : "No transactions for selected month"}</td></tr>`;
    return;
  }
  tbody.innerHTML = display.map(t => `
    <tr data-trans-id="${t.id}">
      <td>${t.date}</td>
      <td style="text-align:left;font-family:var(--font-ui)">${t.description}</td>
      <td class="cat-cell" data-trans-id="${t.id}"><span class="${catClass(t.category)} cat-editable" data-cat="${t.category}" title="Click to change category">${catLabel(t.category)}</span></td>
      <td class="${t.amount >= 0 ? "positive" : "negative"}">${t.amount >= 0 ? "+" : ""}${fmtCad(t.amount)}</td>
      <td><span class="income-sub">${t.source === "bank_statement" ? "Bank" : "Manual"}</span></td>
      <td class="row-actions">
        <button class="btn-remove" onclick="deleteTransaction(${t.id})">Delete</button>
      </td>
    </tr>`).join("");
}

// ── fetch functions ───────────────────────────────────────────────────────

async function fetchFinancialSummary() {
  try {
    const res = await fetch("/api/financial/summary");
    if (!res.ok) return;
    const data = await res.json();
    renderSummary(data);
  } catch (err) {
    console.error("Failed to fetch financial summary:", err);
  }
}

async function fetchIncomeEntries() {
  try {
    const res = await fetch("/api/financial/entries");
    if (!res.ok) return;
    const data = await res.json();
    renderIncomeEntries(data);
  } catch (err) {
    console.error("Failed to fetch income entries:", err);
  }
}

async function fetchTransactions() {
  try {
    const res = await fetch("/api/financial/transactions?per_page=50");
    if (!res.ok) return;
    const data = await res.json();
    renderTransactions(data.transactions || []);
  } catch (err) {
    console.error("Failed to fetch transactions:", err);
  }
}

async function fetchAll() {
  await Promise.all([fetchFinancialSummary(), fetchIncomeEntries(), fetchTransactions()]);
}

// ── delete actions ─────────────────────────────────────────────────────────

async function deleteIncomeEntry(id) {
  if (!confirm("Delete this income entry?")) return;
  try {
    await fetch(`/api/financial/entries/${id}`, { method: "DELETE" });
    await fetchAll();
  } catch (err) {
    console.error("Failed to delete income entry:", err);
  }
}
window.deleteIncomeEntry = deleteIncomeEntry;

async function deleteTransaction(id) {
  if (!confirm("Delete this transaction?")) return;
  try {
    await fetch(`/api/financial/transactions/${id}`, { method: "DELETE" });
    await Promise.all([fetchFinancialSummary(), fetchTransactions()]);
  } catch (err) {
    console.error("Failed to delete transaction:", err);
  }
}
window.deleteTransaction = deleteTransaction;

// ── file uploads ───────────────────────────────────────────────────────────

document.getElementById("uploadPayStubBtn").addEventListener("click", () => {
  document.getElementById("payStubInput").click();
});

document.getElementById("uploadBankStmtBtn").addEventListener("click", () => {
  document.getElementById("bankStmtInput").click();
});

document.getElementById("payStubInput").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const statusEl = document.getElementById("payStubStatus");
  statusEl.textContent = "Processing...";
  statusEl.className = "upload-status";

  const formData = new FormData();
  formData.append("file", file);
  formData.append("type", "pay_stub");

  try {
    const res = await fetch("/api/financial/upload", { method: "POST", body: formData });
    const data = await res.json();

    if (res.ok) {
      statusEl.textContent = "✓ Pay stub saved!";
      setTimeout(() => { statusEl.textContent = ""; }, 3000);
      await fetchAll();
    } else {
      statusEl.textContent = data.error || "Upload failed";
      statusEl.className = "upload-status error";
    }
  } catch (err) {
    statusEl.textContent = "Network error";
    statusEl.className = "upload-status error";
  }
  e.target.value = "";
});

document.getElementById("bankStmtInput").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const statusEl = document.getElementById("bankStmtStatus");
  statusEl.textContent = "Processing...";
  statusEl.className = "upload-status";

  const formData = new FormData();
  formData.append("file", file);
  formData.append("type", "bank_statement");

  try {
    const res = await fetch("/api/financial/upload", { method: "POST", body: formData });
    const data = await res.json();

    if (res.ok) {
      const msg = data.count ? `${data.count} transactions imported` : "Bank statement processed";
      statusEl.textContent = "✓ " + msg;
      setTimeout(() => { statusEl.textContent = ""; }, 3000);
      await fetchAll();
    } else {
      statusEl.textContent = data.error || "Upload failed";
      statusEl.className = "upload-status error";
    }
  } catch (err) {
    statusEl.textContent = "Network error";
    statusEl.className = "upload-status error";
  }
  e.target.value = "";
});

// Credit card statement upload
document.getElementById("uploadCreditCardBtn").addEventListener("click", () => {
  document.getElementById("creditCardInput").click();
});

document.getElementById("creditCardInput").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const statusEl = document.getElementById("creditCardStatus");
  statusEl.textContent = "Processing...";
  statusEl.className = "upload-status";

  const formData = new FormData();
  formData.append("file", file);
  formData.append("type", "credit_card");

  try {
    const res = await fetch("/api/financial/upload", { method: "POST", body: formData });
    const data = await res.json();

    if (res.ok) {
      const msg = data.count ? `${data.count} transactions imported` : "Credit card statement processed";
      statusEl.textContent = "✓ " + msg;
      setTimeout(() => { statusEl.textContent = ""; }, 3000);
      await fetchAll();
    } else {
      statusEl.textContent = data.error || "Upload failed";
      statusEl.className = "upload-status error";
    }
  } catch (err) {
    statusEl.textContent = "Network error";
    statusEl.className = "upload-status error";
  }
  e.target.value = "";
});

// ── manual add buttons ─────────────────────────────────────────────────────

document.getElementById("addIncomeManualBtn").addEventListener("click", () => {
  document.getElementById("incomeError").textContent = "";
  document.getElementById("incomeForm").reset();
  document.getElementById("incomeEditId").value = "";
  document.getElementById("incomeModalTitle").textContent = "Add Income Entry";
  document.getElementById("incomeSubmitBtn").textContent = "Add Entry";
  setDefaultDate("incomePayDate");
  openModal("incomeModal");
});

document.getElementById("addIncomeEntryBtn").addEventListener("click", () => {
  document.getElementById("incomeError").textContent = "";
  document.getElementById("incomeForm").reset();
  document.getElementById("incomeEditId").value = "";
  document.getElementById("incomeModalTitle").textContent = "Add Income Entry";
  document.getElementById("incomeSubmitBtn").textContent = "Add Entry";
  setDefaultDate("incomePayDate");
  openModal("incomeModal");
});

// ── open income edit modal ─────────────────────────────────────────────────

function openIncomeEditModal(entryId) {
  const entry = finEntries.find(e => e.id === entryId);
  if (!entry) return;
  document.getElementById("incomeError").textContent = "";
  document.getElementById("incomeEditId").value = entryId;
  document.getElementById("incomeModalTitle").textContent = "Edit Income Entry";
  document.getElementById("incomeSubmitBtn").textContent = "Save Changes";
  document.getElementById("incomePayDate").value = entry.pay_date;
  document.getElementById("incomeEmployer").value = entry.employer;
  document.getElementById("incomeGross").value = entry.gross_income;
  document.getElementById("incomeNet").value = entry.net_income;
  openModal("incomeModal");
}
window.openIncomeEditModal = openIncomeEditModal;

document.getElementById("addTransactionBtn").addEventListener("click", () => {
  document.getElementById("transError").textContent = "";
  document.getElementById("transactionForm").reset();
  setDefaultDate("transDate");
  openModal("transactionModal");
});

document.getElementById("addExpenseManualBtn").addEventListener("click", () => {
  document.getElementById("transError").textContent = "";
  document.getElementById("transactionForm").reset();
  setDefaultDate("transDate");
  // Pre-fill with negative amount hint
  openModal("transactionModal");
});

// ── income form ────────────────────────────────────────────────────────────

document.getElementById("incomeForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = document.getElementById("incomeError");
  const submitBtn = document.getElementById("incomeSubmitBtn");
  errEl.textContent = "";
  submitBtn.classList.add("loading");
  submitBtn.textContent = "Saving…";

  const editId = document.getElementById("incomeEditId").value;
  const payload = {
    pay_date: document.getElementById("incomePayDate").value,
    employer: document.getElementById("incomeEmployer").value.trim(),
    gross_income: parseFloat(document.getElementById("incomeGross").value),
    net_income: parseFloat(document.getElementById("incomeNet").value),
  };

  try {
    const url = editId ? `/api/income/edit/${editId}` : "/api/financial/entries";
    const method = editId ? "PUT" : "POST";
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      errEl.textContent = data.error || "Failed to save entry";
    } else {
      closeModal("incomeModal");
      // Reset to add mode
      document.getElementById("incomeEditId").value = "";
      document.getElementById("incomeModalTitle").textContent = "Add Income Entry";
      document.getElementById("incomeSubmitBtn").textContent = "Add Entry";
      await fetchAll();
    }
  } catch {
    errEl.textContent = "Network error. Please try again.";
  } finally {
    submitBtn.classList.remove("loading");
    submitBtn.textContent = editId ? "Save Changes" : "Add Entry";
  }
});

// ── transaction form ─────────────────────────────────────────────────────

document.getElementById("transactionForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = document.getElementById("transError");
  const submitBtn = document.getElementById("transSubmitBtn");
  errEl.textContent = "";
  submitBtn.classList.add("loading");
  submitBtn.textContent = "Adding…";

  const payload = {
    date: document.getElementById("transDate").value,
    description: document.getElementById("transDesc").value.trim(),
    amount: parseFloat(document.getElementById("transAmount").value),
    category: document.getElementById("transCategory").value,
  };

  if (isNaN(payload.amount)) {
    errEl.textContent = "Amount must be a number";
    submitBtn.classList.remove("loading");
    submitBtn.textContent = "Add Transaction";
    return;
  }

  try {
    const res = await fetch("/api/financial/transactions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      errEl.textContent = data.error || "Failed to add transaction";
    } else {
      closeModal("transactionModal");
      await Promise.all([fetchFinancialSummary(), fetchTransactions()]);
    }
  } catch {
    errEl.textContent = "Network error. Please try again.";
  } finally {
    submitBtn.classList.remove("loading");
    submitBtn.textContent = "Add Transaction";
  }
});

// ── month filter ─────────────────────────────────────────────────────────

document.getElementById("monthFilter")?.addEventListener("change", onMonthFilterChange);

// ── init ─────────────────────────────────────────────────────────────────

fetchAll();

// ── category inline edit ──────────────────────────────────────────────────

// CRA-aligned category options for the inline dropdown
const CRA_CATEGORIES = [
  { value: "maintenance",        label: "Maintenance",        short: "Maintenance" },
  { value: "meals_entertainment",label: "Meals & Entertainment", short: "Meals & Ent" },
  { value: "office_general",     label: "Office & General",   short: "Office & Gen" },
  { value: "telephone_utilities",label: "Telephone & Utilities", short: "Tel & Utils" },
  { value: "travel_expense",     label: "Travel Expense",      short: "Travel" },
  { value: "insurance",          label: "Insurance",           short: "Insurance" },
  { value: "interest_bank",      label: "Interest & Bank Charges", short: "Bank Charges" },
  { value: "taxes_licenses",     label: "Business Taxes & Licenses", short: "Taxes & Lic" },
  { value: "other",              label: "Other",               short: "Other" },
  // Legacy categories still used by existing transactions
  { value: "groceries",          label: "Groceries",           short: "Groceries" },
  { value: "gas",                label: "Gas / Fuel",          short: "Gas" },
  { value: "subscriptions",      label: "Subscriptions",       short: "Subscriptions" },
  { value: "dining",             label: "Dining",               short: "Dining" },
  { value: "utilities",          label: "Utilities",            short: "Utilities" },
];

function buildCatSelect(currentCat) {
  const sel = document.createElement("select");
  sel.className = "cat-edit-select";
  sel.style.cssText = "background:var(--bg-card-alt);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-family:var(--font-mono);font-size:.75rem;padding:2px 6px;outline:none;cursor:pointer;";
  CRA_CATEGORIES.forEach(c => {
    const opt = document.createElement("option");
    opt.value = c.value;
    opt.textContent = c.short;
    if (c.value === currentCat) opt.selected = true;
    sel.appendChild(opt);
  });
  return sel;
}

// Attach click handler to category cells via event delegation
document.getElementById("transactionsBody").addEventListener("click", async (e) => {
  const span = e.target.closest(".cat-editable");
  if (!span) return;

  const transId = parseInt(span.dataset.transId || span.closest("tr")?.dataset.transId);
  if (!transId) return;

  // Already editing?
  if (span.closest(".cat-cell").querySelector(".cat-edit-select")) return;

  const currentCat = span.dataset.cat;
  const sel = buildCatSelect(currentCat);
  const cell = span.closest(".cat-cell");

  // Replace span with select
  cell.innerHTML = "";
  cell.appendChild(sel);
  sel.focus();

  async function saveAndRestore() {
    const newCat = sel.value;
    cell.innerHTML = "";
    const newSpan = document.createElement("span");
    newSpan.className = `${catClass(newCat)} cat-editable`;
    newSpan.dataset.cat = newCat;
    newSpan.title = "Click to change category";
    newSpan.textContent = catLabel(newCat);
    cell.appendChild(newSpan);

    if (newCat !== currentCat) {
      try {
        await fetch(`/api/transaction/edit/${transId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ category: newCat }),
        });
        // Update local state
        const t = finTransactions.find(t => t.id === transId);
        if (t) t.category = newCat;
        renderFilteredExpenses();
      } catch (err) {
        console.error("Failed to update category:", err);
      }
    }
  }

  sel.addEventListener("change", saveAndRestore);
  sel.addEventListener("blur", saveAndRestore);
  sel.addEventListener("keydown", (e2) => {
    if (e2.key === "Escape") {
      sel.blur();
    }
  });
});
