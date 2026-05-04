const STAGE_LABELS = {
  transcript: "Transcript",
  1: "Signal Extraction",
  2: "Thematic Analysis",
  3: "Parallel Research",
  4: "Consolidation",
  5: "Equity Screen",
};

const STAGE_META = {
  transcript: { label: "Fetching transcript", model: "youtube-transcript-api / Supadata", emoji: "📄" },
  1: { label: "Extracting investment signals from transcript", model: "Gemini 2.5 Pro", emoji: "🎯" },
  2: { label: "Building thematic deep-dive (TAM, competitors, beneficiaries)", model: "Claude Opus 4.6", emoji: "🧠" },
  3: { label: "Designing research plan and dispatching parallel queries", model: "Claude Opus 4.6 + Perplexity Sonar Pro", emoji: "🔍" },
  4: { label: "Consolidating evidence into thesis with confidence scoring", model: "Gemini 2.5 Pro", emoji: "📊" },
  5: { label: "Running equity screen with personalized portfolio observations", model: "Claude Opus 4.7 (extended thinking)", emoji: "💡" },
  enrichment: { label: "Fetching live market data via yfinance", model: "yfinance", emoji: "💹" },
};

let currentRunId = null;
let eventSource = null;
let stageOutputs = {};
let pollTimer = null;
let runStartTime = null;
let stageStartTime = null;
let currentStage = null;
let elapsedTimerInterval = null;
let consecutiveRefreshFailures = 0;

const POLL_INTERVAL_MS = 5000;
const MAX_CONSECUTIVE_REFRESH_FAILURES = 3;

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
    startElapsedTimer();
    setActiveStage("transcript");
    connectStream(currentRunId);
    startPolling();
    await refreshRun();
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

function startPolling() {
  stopPolling();
  pollTimer = setInterval(() => {
    refreshRun();
  }, POLL_INTERVAL_MS);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function formatElapsed(seconds) {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${minutes}m ${remainder.toString().padStart(2, "0")}s`;
}

function formatTimer(seconds) {
  const minutes = Math.floor(seconds / 60).toString().padStart(2, "0");
  const remainder = (seconds % 60).toString().padStart(2, "0");
  return `${minutes}:${remainder}`;
}

function startElapsedTimer(startedAt = null) {
  runStartTime = startedAt ? new Date(startedAt).getTime() : Date.now();
  if (Number.isNaN(runStartTime)) runStartTime = Date.now();
  stageStartTime = Date.now();
  if (elapsedTimerInterval) clearInterval(elapsedTimerInterval);
  elapsedTimerInterval = setInterval(updateElapsedDisplay, 1000);
  updateElapsedDisplay();
}

function stopElapsedTimer() {
  if (elapsedTimerInterval) {
    clearInterval(elapsedTimerInterval);
    elapsedTimerInterval = null;
  }
}

function updateElapsedDisplay() {
  if (!runStartTime) return;
  const totalSeconds = Math.max(0, Math.floor((Date.now() - runStartTime) / 1000));
  const stageSeconds = stageStartTime ? Math.max(0, Math.floor((Date.now() - stageStartTime) / 1000)) : 0;
  const totalEl = document.getElementById("total-elapsed");
  const stageEl = document.getElementById("current-stage-elapsed");
  if (totalEl) totalEl.textContent = formatTimer(totalSeconds);
  if (stageEl) stageEl.textContent = formatElapsed(stageSeconds);
}

function setActiveStage(stage) {
  if (currentStage !== stage) {
    currentStage = stage;
    stageStartTime = Date.now();
  }
  const meta = STAGE_META[stage] || { label: `Stage ${stage}`, model: "—", emoji: "⏳" };
  const descEl = document.getElementById("current-stage-description");
  const modelEl = document.getElementById("current-stage-model");
  const emojiEl = document.getElementById("current-stage-emoji");
  if (descEl) descEl.textContent = meta.label;
  if (modelEl) modelEl.textContent = meta.model;
  if (emojiEl) emojiEl.textContent = meta.emoji;
  markStageActive(stage);
  updateElapsedDisplay();
}

async function handleEvent(event) {
  if (!runStartTime) startElapsedTimer();
  if (event.type === "stage_start") setActiveStage(event.stage);
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
    setLiveStatusError();
    setRunning(false);
    stopPolling();
    stopElapsedTimer();
  }
  if (event.type === "final" && eventSource) {
    eventSource.close();
    if (event.status !== "complete") stopPolling();
  }
}

async function refreshRun() {
  if (!currentRunId) return;
  try {
    const response = await fetch(`/research/runs/${currentRunId}`, {
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const run = await response.json();
    consecutiveRefreshFailures = 0;
    hideRefreshError();
    applyRunState(run);
    return run;
  } catch (error) {
    consecutiveRefreshFailures += 1;
    if (consecutiveRefreshFailures >= MAX_CONSECUTIVE_REFRESH_FAILURES) {
      showRefreshError(
        `Connection unstable (${consecutiveRefreshFailures} failed refreshes). Pipeline may still be running - refresh the page in a moment.`
      );
    }
    return null;
  }
}

function applyRunState(run) {
  if (!runStartTime && run.started_at) startElapsedTimer(run.started_at);
  reconcileRunState(run);
  renderOutputs(run);
  renderReferenceRail(run);
  if (run.status === "complete") onComplete();
  if (run.status === "error") {
    showError(run.error_message || "Pipeline failed.");
    setLiveStatusError();
    setRunning(false);
    stopPolling();
    stopElapsedTimer();
  }
}

function showRefreshError(message) {
  const el = document.getElementById("errorMsg") || document.querySelector(".refresh-error");
  if (el) {
    el.textContent = message;
    el.hidden = false;
    el.style.display = "block";
  }
}

function hideRefreshError() {
  const el = document.getElementById("errorMsg") || document.querySelector(".refresh-error");
  if (el && el.textContent.startsWith("Connection unstable")) {
    el.hidden = true;
    el.style.display = "none";
  }
}

function reconcileRunState(run) {
  const stage = currentStageNumber(run.current_stage);
  if (stage && stage !== "transcript") markStageDone("transcript");
  for (const previousStage of completedStagesBefore(stage)) {
    markStageDone(previousStage);
  }

  if (run.stage1_output) markStageDone(1);
  if (run.stage2_output) markStageDone(2);
  if (run.stage3_research) markStageDone(3);
  if (run.stage4_output) markStageDone(4);
  if (run.stage5_output) markStageDone(5);

  if (run.status === "running" && stage) markStageActive(stage);
  if (run.status === "running" && stage) setActiveStage(stage);
}

function currentStageNumber(currentStage) {
  if (currentStage === "transcript") return "transcript";
  if (currentStage === "stage1") return 1;
  if (currentStage === "stage2") return 2;
  if (currentStage === "stage3") return 3;
  if (currentStage === "stage4") return 4;
  if (currentStage === "stage5") return 5;
  return null;
}

function completedStagesBefore(stage) {
  if (stage === "transcript" || !stage) return [];
  const order = [1, 2, 3, 4, 5];
  return order.filter((item) => item < stage);
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
  if (el.classList.contains("done")) return;
  document.querySelectorAll(".stage-step.active").forEach((step) => {
    if (step !== el) step.classList.remove("active");
  });
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
  stopPolling();
  setLiveStatusComplete();
  stopElapsedTimer();
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  const actions = document.getElementById("finalActions");
  actions.hidden = false;
  document.getElementById("downloadFull").href = `/research/report/${currentRunId}/full.md`;
}

function resetLiveStatusPanel() {
  const panel = document.getElementById("live-status");
  if (!panel) return;
  panel.hidden = false;
  panel.classList.remove("complete", "error");
  const labelEl = panel.querySelector(".status-label");
  if (labelEl) labelEl.textContent = "RUNNING";
  const descEl = document.getElementById("current-stage-description");
  const modelEl = document.getElementById("current-stage-model");
  const emojiEl = document.getElementById("current-stage-emoji");
  const totalEl = document.getElementById("total-elapsed");
  const stageEl = document.getElementById("current-stage-elapsed");
  if (descEl) descEl.textContent = "Initializing pipeline...";
  if (modelEl) modelEl.textContent = "—";
  if (emojiEl) emojiEl.textContent = "⏳";
  if (totalEl) totalEl.textContent = "00:00";
  if (stageEl) stageEl.textContent = "0s";
}

function setLiveStatusComplete() {
  const panel = document.getElementById("live-status");
  if (!panel) return;
  panel.classList.remove("error");
  panel.classList.add("complete");
  const labelEl = panel.querySelector(".status-label");
  if (labelEl) labelEl.textContent = "COMPLETE";
  const totalSeconds = runStartTime ? Math.max(0, Math.floor((Date.now() - runStartTime) / 1000)) : 0;
  const descEl = document.getElementById("current-stage-description");
  const modelEl = document.getElementById("current-stage-model");
  if (descEl) descEl.textContent = `Pipeline finished in ${formatTimer(totalSeconds)}`;
  if (modelEl) modelEl.textContent = "All stages complete";
  updateElapsedDisplay();
}

function setLiveStatusError() {
  const panel = document.getElementById("live-status");
  if (!panel) return;
  panel.classList.remove("complete");
  panel.classList.add("error");
  const labelEl = panel.querySelector(".status-label");
  if (labelEl) labelEl.textContent = "ERROR";
}

function resetUI() {
  stopPolling();
  stopElapsedTimer();
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  runStartTime = null;
  stageStartTime = null;
  currentStage = null;
  document.querySelectorAll(".stage-step").forEach((step) => step.classList.remove("active", "done"));
  resetLiveStatusPanel();
  document.getElementById("stage3Panel").hidden = true;
  document.getElementById("promptProgress").style.width = "0";
  document.getElementById("stageOutputs").innerHTML = "";
  document.getElementById("finalActions").hidden = true;
  document.getElementById("errorMsg").hidden = true;
  document.getElementById("errorMsg").style.display = "";
  stageOutputs = {};
  consecutiveRefreshFailures = 0;
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
  el.style.display = "block";
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
