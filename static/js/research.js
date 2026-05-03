const STAGE_LABELS = {
  transcript: "Transcript",
  1: "Signal Extraction",
  2: "Thematic Analysis",
  3: "Parallel Research",
  4: "Consolidation",
  5: "Equity Screen",
};

let currentRunId = null;
let eventSource = null;
let stageOutputs = {};

document.getElementById("researchForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await runPipeline();
});

async function runPipeline() {
  const url = document.getElementById("youtubeUrl").value.trim();
  if (!url) return showError("Paste a YouTube URL first.");
  resetUI();
  setRunning(true);

  try {
    const response = await fetch("/research/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Pipeline start failed.");
    currentRunId = payload.run_id;
    connectStream(currentRunId);
    if (payload.duplicate) {
      await refreshRun();
      setRunning(false);
    }
  } catch (error) {
    showError(error.message);
    setRunning(false);
  }
}

function connectStream(runId) {
  if (eventSource) eventSource.close();
  eventSource = new EventSource(`/research/stream/${runId}`);
  eventSource.onmessage = async (event) => handleEvent(JSON.parse(event.data));
  eventSource.onerror = () => {
    if (eventSource) eventSource.close();
    setTimeout(() => currentRunId && connectStream(currentRunId), 1800);
  };
}

async function handleEvent(event) {
  if (event.type === "stage_start") markStageActive(event.stage);
  if (event.type === "stage_done") {
    markStageDone(event.stage);
    await refreshRun();
  }
  if (event.type === "research_prompts_dispatched") showStage3Panel(event);
  if (event.type === "research_prompt_done") updatePromptStatus(event);
  if (event.type === "pipeline_complete") {
    await refreshRun();
    onComplete();
  }
  if (event.type === "error") {
    showError(event.message);
    setRunning(false);
  }
  if (event.type === "final" && eventSource) eventSource.close();
}

async function refreshRun() {
  if (!currentRunId) return;
  const response = await fetch(`/research/runs/${currentRunId}`);
  const run = await response.json();
  renderOutputs(run);
  renderReferenceRail(run);
}

function renderOutputs(run) {
  const fields = [
    [1, "stage1_output"],
    [2, "stage2_output"],
    [3, "stage3_plan_output"],
    [4, "stage4_output"],
    [5, "stage5_output"],
  ];
  for (const [stage, field] of fields) {
    if (run[field] && stageOutputs[stage] !== run[field]) {
      stageOutputs[stage] = run[field];
      renderStageCard(stage, run[field]);
    }
  }
}

function renderStageCard(stage, markdown) {
  const stack = document.getElementById("stageOutputs");
  const empty = stack.querySelector(".empty-research-state");
  if (empty) empty.remove();
  let card = document.getElementById(`stage-card-${stage}`);
  if (!card) {
    card = document.createElement("article");
    card.id = `stage-card-${stage}`;
    card.className = "stage-report";
    stack.appendChild(card);
  }
  const downloadName = stage === 3 ? "stage3-plan.md" : `stage${stage}.md`;
  card.innerHTML = `
    <header class="stage-report-header">
      <h2>Stage ${stage} - ${STAGE_LABELS[stage]}</h2>
      <div class="stage-report-actions">
        <button type="button" data-copy-stage="${stage}">Copy</button>
        <a href="/research/report/${currentRunId}/${downloadName}" download>MD</a>
      </div>
    </header>
    <div class="markdown-body">${marked.parse(markdown)}</div>
  `;
  card.querySelector("[data-copy-stage]").addEventListener("click", () => {
    navigator.clipboard.writeText(stageOutputs[stage] || "");
  });
}

function showStage3Panel(event) {
  const panel = document.getElementById("stage3Panel");
  const list = document.getElementById("promptList");
  panel.hidden = false;
  document.getElementById("parallelCount").textContent = `${event.count} prompts`;
  list.innerHTML = event.titles.map((title, index) => `
    <div class="prompt-row" id="prompt-${index + 1}">
      <span class="prompt-id">P${index + 1}</span>
      <span class="prompt-title">${escapeHtml(title)}</span>
    </div>
  `).join("");
}

function updatePromptStatus(event) {
  const row = document.getElementById(`prompt-${String(event.prompt_id).replace("P", "")}`);
  if (row) row.classList.add("done");
  document.getElementById("promptProgress").style.width =
    `${Math.round((event.completed / event.total) * 100)}%`;
}

function renderReferenceRail(run) {
  const research = run.stage3_research || {};
  const citations = [];
  Object.values(research).forEach((result) => {
    (result.citations || []).forEach((citation) => citations.push(citation));
  });
  document.getElementById("citationList").innerHTML = citations.length
    ? citations.map(formatCitation).join("")
    : "Waiting for Stage 3.";

  const market = run.live_market_data || {};
  document.getElementById("marketData").innerHTML = Object.keys(market).length
    ? Object.entries(market).map(([ticker, data]) => `
      <div class="reference-item"><strong>${ticker}</strong><br />Last: ${formatNumber(data.last_price)} | 52W: ${formatNumber(data.year_low)}-${formatNumber(data.year_high)}</div>
    `).join("")
    : "Waiting for Stage 5.";

  const portfolio = run.portfolio_snapshot || {};
  const stockCount = (portfolio.stocks || []).length;
  const watchCount = (portfolio.watchlist || []).length;
  document.getElementById("portfolioObservations").innerHTML =
    portfolio.error ? `<div class="reference-item">${escapeHtml(portfolio.error)}</div>` :
    (stockCount || watchCount)
      ? `<div class="reference-item">${stockCount} stocks and ${watchCount} watchlist names available for Stage 5.</div>`
      : "Waiting for portfolio export.";
}

function formatCitation(citation) {
  if (typeof citation === "string") {
    return `<div class="reference-item"><a href="${escapeHtml(citation)}" target="_blank" rel="noreferrer">${escapeHtml(citation)}</a></div>`;
  }
  const url = citation.url || "#";
  const title = citation.title || url;
  return `<div class="reference-item"><a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(title)}</a></div>`;
}

function markStageActive(stage) {
  const el = document.getElementById(stage === "transcript" ? "pill-transcript" : `pill-${stage}`);
  if (!el) return;
  el.classList.add("active");
}

function markStageDone(stage) {
  const el = document.getElementById(stage === "transcript" ? "pill-transcript" : `pill-${stage}`);
  if (!el) return;
  el.classList.remove("active");
  el.classList.add("done");
}

function onComplete() {
  setRunning(false);
  const actions = document.getElementById("finalActions");
  actions.hidden = false;
  document.getElementById("downloadFull").href = `/research/report/${currentRunId}/full.md`;
}

function resetUI() {
  document.querySelectorAll(".stage-step").forEach((step) => step.classList.remove("active", "done"));
  document.getElementById("stage3Panel").hidden = true;
  document.getElementById("promptProgress").style.width = "0";
  document.getElementById("stageOutputs").innerHTML = `
    <div class="empty-research-state">
      <div class="card-label">Running</div>
      <p>The pipeline is starting. Stage outputs will appear here as they complete.</p>
    </div>`;
  document.getElementById("finalActions").hidden = true;
  document.getElementById("errorMsg").hidden = true;
  stageOutputs = {};
}

function setRunning(isRunning) {
  const btn = document.getElementById("runBtn");
  btn.disabled = isRunning;
  btn.textContent = isRunning ? "Running..." : "Run Pipeline";
}

function showError(message) {
  const el = document.getElementById("errorMsg");
  el.textContent = message;
  el.hidden = false;
}

function formatNumber(value) {
  const num = Number(value || 0);
  return num ? num.toLocaleString("en-US", { maximumFractionDigits: 2 }) : "-";
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}
