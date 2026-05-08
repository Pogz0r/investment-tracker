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
  1: { label: "Extracting investment signals from transcript", model: "Gemini 3.1 Pro", emoji: "🎯" },
  2: { label: "Building thematic deep-dive (TAM, competitors, beneficiaries)", model: "Claude Opus 4.6", emoji: "🧠" },
  3: { label: "Designing research plan and dispatching parallel queries", model: "GPT-5.5 + Perplexity Sonar Pro", emoji: "🔍" },
  4: { label: "Consolidating evidence into thesis with confidence scoring", model: "Gemini 3.1 Pro", emoji: "📊" },
  5: { label: "Running equity screen with personalized portfolio observations", model: "Claude Opus 4.7 (adaptive thinking)", emoji: "💡" },
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
const uploadedStages = {};

const POLL_INTERVAL_MS = 5000;
const MAX_CONSECUTIVE_REFRESH_FAILURES = 3;

document.getElementById("researchForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (isManualMode()) {
    await runManualPipeline();
    return;
  }
  await runPipeline();
});

document.getElementById("retryBtn").addEventListener("click", retryRun);
document.getElementById("manualModeToggle").addEventListener("change", toggleManualMode);
document.getElementById("runManualBtn").addEventListener("click", runManualPipeline);
document.getElementById("clearUploadsBtn").addEventListener("click", clearUploads);
document.getElementById("youtubeUrl").addEventListener("input", updateRunManualButtonState);
document.querySelectorAll("[data-upload-stage]").forEach((input) => {
  input.addEventListener("change", () => handleUpload(input.dataset.uploadStage, input));
});

async function runPipeline() {
  console.log("[RUN] Run Pipeline submitted");
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  stopPolling();
  if (elapsedTimerInterval) {
    clearInterval(elapsedTimerInterval);
    elapsedTimerInterval = null;
  }
  currentRunId = null;
  runStartTime = null;
  stageStartTime = null;
  currentStage = null;
  consecutiveRefreshFailures = 0;

  const url = document.getElementById("youtubeUrl").value.trim();
  if (!url) return showError("Paste a YouTube URL first.");
  resetUI();
  setRunning(true);
  document.getElementById("runBtn").textContent = "Starting...";

  try {
    console.log("[RUN] Submitting POST /research/run with URL:", url);
    const response = await fetch("/research/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ url }),
    });
    console.log("[RUN] Server responded:", response.status);
    const responseText = await response.text();
    let payload = {};
    try {
      payload = responseText ? JSON.parse(responseText) : {};
    } catch (parseError) {
      console.error("[RUN] Non-JSON response body:", responseText.slice(0, 500));
      throw new Error(`Server returned non-JSON response ${response.status}: ${responseText.slice(0, 200)}`);
    }
    if (!response.ok) {
      console.error("[RUN] Error body:", responseText.slice(0, 500));
      throw new Error(payload.error || `Server error ${response.status}: ${responseText.slice(0, 200)}`);
    }
    console.log("[RUN] Got run_id:", payload.run_id, "duplicate:", Boolean(payload.duplicate));
    currentRunId = payload.run_id;
    if (payload.duplicate) {
      showError(`This video was already analyzed (run #${payload.run_id}). Showing existing results.`);
      await refreshRun();
      setRunning(false);
      return;
    }
    startElapsedTimer();
    setActiveStage("transcript");
    document.getElementById("runBtn").textContent = "Running...";
    connectStream(currentRunId);
    startPolling();
    await refreshRun();
  } catch (error) {
    console.error("[RUN] Fetch failed:", error);
    showError(error.message);
    setRunning(false);
  }
}

function isManualMode() {
  return Boolean(document.getElementById("manualModeToggle").checked);
}

function toggleManualMode() {
  const enabled = isManualMode();
  const panel = document.getElementById("manualModePanel");
  const urlInput = document.getElementById("youtubeUrl");
  const runBtn = document.getElementById("runBtn");
  const manualBtn = document.getElementById("runManualBtn");

  panel.hidden = !enabled;
  runBtn.hidden = enabled;
  manualBtn.hidden = !enabled;
  if (enabled) {
    urlInput.placeholder = "Optional YouTube URL (for context)";
    urlInput.removeAttribute("required");
  } else {
    urlInput.placeholder = "Paste a YouTube URL";
    clearUploads();
  }
}

async function handleUpload(stage, fileInput) {
  const file = fileInput.files[0];
  const statusEl = uploadStatusEl(stage);
  if (!file) return;

  if (file.size > 5 * 1024 * 1024) {
    if (statusEl) statusEl.textContent = "File too large (max 5MB)";
    fileInput.value = "";
    delete uploadedStages[String(stage)];
    updateRunManualButtonState();
    return;
  }

  try {
    const content = await file.text();
    if (!content.trim()) {
      if (statusEl) statusEl.textContent = "File is empty";
      delete uploadedStages[String(stage)];
      updateRunManualButtonState();
      return;
    }
    uploadedStages[String(stage)] = content;
    if (statusEl) statusEl.textContent = `${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
    updateRunManualButtonState();
  } catch (error) {
    if (statusEl) statusEl.textContent = `Error reading file: ${error.message}`;
  }
}

function uploadStatusEl(stage) {
  const idMap = {
    "1": "uploadStatus1",
    "2": "uploadStatus2",
    "3-plan": "uploadStatus3Plan",
    "3-research": "uploadStatus3Research",
    "4": "uploadStatus4",
  };
  return document.getElementById(idMap[String(stage)]);
}

function updateRunManualButtonState() {
  const btn = document.getElementById("runManualBtn");
  const hasUrl = Boolean(document.getElementById("youtubeUrl").value.trim());
  btn.disabled = Object.keys(uploadedStages).length === 0 && !hasUrl;
}

function clearUploads() {
  Object.keys(uploadedStages).forEach((key) => delete uploadedStages[key]);
  document.querySelectorAll("[data-upload-stage]").forEach((input) => {
    input.value = "";
  });
  document.querySelectorAll(".upload-status").forEach((status) => {
    status.textContent = "";
  });
  updateRunManualButtonState();
}

async function runManualPipeline() {
  const hasStage3Plan = Object.prototype.hasOwnProperty.call(uploadedStages, "3-plan");
  const hasStage3Research = Object.prototype.hasOwnProperty.call(uploadedStages, "3-research");
  if (hasStage3Plan !== hasStage3Research) {
    showError("Stage 3 requires both the research plan and research findings. Upload both or neither.");
    return;
  }

  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  stopPolling();
  if (elapsedTimerInterval) {
    clearInterval(elapsedTimerInterval);
    elapsedTimerInterval = null;
  }
  currentRunId = null;
  runStartTime = null;
  stageStartTime = null;
  currentStage = null;
  consecutiveRefreshFailures = 0;

  const url = document.getElementById("youtubeUrl").value.trim();
  const payload = {
    url: url || null,
    manual: true,
    uploaded_stages: { ...uploadedStages },
  };

  resetUI();
  const btn = document.getElementById("runManualBtn");
  btn.disabled = true;
  btn.textContent = "Starting...";

  try {
    console.log("[MANUAL RUN] Submitting with stages:", Object.keys(uploadedStages));
    const response = await fetch("/research/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(payload),
    });
    const responseText = await response.text();
    let data = {};
    try {
      data = responseText ? JSON.parse(responseText) : {};
    } catch (parseError) {
      throw new Error(`Server returned non-JSON response ${response.status}: ${responseText.slice(0, 200)}`);
    }
    if (!response.ok) {
      throw new Error(data.error || `Server error ${response.status}: ${responseText.slice(0, 200)}`);
    }
    console.log("[MANUAL RUN] Got run_id:", data.run_id, "starting from:", data.resume_from);
    currentRunId = data.run_id;
    btn.textContent = "Running...";
    startElapsedTimer();
    setActiveStage(stageFromResume(data.resume_from));
    connectStream(currentRunId);
    startPolling();
    await refreshRun();
  } catch (error) {
    showError(`Could not start: ${error.message}`);
    btn.disabled = false;
    btn.textContent = "Run From Uploaded Stages";
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
  if (event.type === "stage_skipped") markStageDone(event.stage);
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
    resetManualButton();
    stopPolling();
    stopElapsedTimer();
    showRetryButton();
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
    resetManualButton();
    stopPolling();
    stopElapsedTimer();
    showRetryButton();
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
        <button type="button" class="copy-btn" data-copy-stage="${stage}">Copy</button>
        <a href="/research/report/${currentRunId}/${downloadName}" download>MD</a>
      </div>
    </header>
    <div class="markdown-body">${marked.parse(markdown)}</div>
  `;
  card.querySelector("[data-copy-stage]").addEventListener("click", () => {
    copyStageOutput(stage);
  });
}

async function copyStageOutput(stage) {
  const markdown = stageOutputs[stage];
  if (!markdown) {
    console.warn("[COPY] No content for stage", stage);
    return;
  }

  const btn = document.querySelector(`#stage-card-${stage} .copy-btn`);
  const originalText = btn ? btn.textContent : "Copy";

  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(markdown);
      setCopyButtonText(btn, "Copied!", originalText);
      return;
    }

    const textarea = document.createElement("textarea");
    textarea.value = markdown;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    textarea.style.top = "0";
    textarea.style.left = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();

    const success = document.execCommand("copy");
    document.body.removeChild(textarea);
    setCopyButtonText(btn, success ? "Copied!" : "Copy failed", originalText);
  } catch (error) {
    console.error("[COPY] Failed:", error);
    setCopyButtonText(btn, "Copy failed", originalText);
  }
}

function setCopyButtonText(btn, text, originalText) {
  if (!btn) return;
  btn.textContent = text;
  setTimeout(() => {
    btn.textContent = originalText;
  }, 2000);
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
  resetManualButton();
  stopPolling();
  setLiveStatusComplete();
  stopElapsedTimer();
  hideRetryButton();
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
  hideRetryButton();
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
  hideRetryButton();
  updateElapsedDisplay();
}

function setLiveStatusError() {
  const panel = document.getElementById("live-status");
  if (!panel) return;
  panel.classList.remove("complete");
  panel.classList.add("error");
  const labelEl = panel.querySelector(".status-label");
  if (labelEl) labelEl.textContent = "ERROR";
  showRetryButton();
}

async function retryRun() {
  if (!currentRunId) return;
  if (!window.confirm("Retry this run from the failed stage? Successful stages will be reused.")) return;

  const btn = document.getElementById("retryBtn");
  btn.disabled = true;
  btn.textContent = "Retrying...";

  try {
    const response = await fetch(`/research/run/${currentRunId}/retry`, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Retry failed.");
    console.log("[RETRY] Resuming from", payload.resume_from);
    if (payload.status === "complete") {
      await refreshRun();
      hideRetryButton();
      setRunning(false);
      return;
    }

    hideRefreshError();
    document.getElementById("errorMsg").hidden = true;
    document.getElementById("errorMsg").style.display = "";
    hideRetryButton();
    resetLiveStatusPanel();
    setRunning(true);
    startElapsedTimer();
    setActiveStage(stageFromResume(payload.resume_from));
    connectStream(currentRunId);
    startPolling();
    await refreshRun();
  } catch (error) {
    showError(`Retry failed: ${error.message}`);
    btn.disabled = false;
    btn.textContent = "Retry From Failed Stage";
    showRetryButton();
  }
}

function stageFromResume(resumeFrom) {
  if (resumeFrom === "stage1") return 1;
  if (resumeFrom === "stage2") return 2;
  if (resumeFrom === "stage3") return 3;
  if (resumeFrom === "stage4") return 4;
  if (resumeFrom === "stage5") return 5;
  return "transcript";
}

function showRetryButton() {
  const btn = document.getElementById("retryBtn");
  if (!btn) return;
  btn.hidden = false;
  btn.disabled = false;
  btn.textContent = "Retry From Failed Stage";
}

function hideRetryButton() {
  const btn = document.getElementById("retryBtn");
  if (!btn) return;
  btn.hidden = true;
  btn.disabled = false;
  btn.textContent = "Retry From Failed Stage";
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

function resetManualButton() {
  const btn = document.getElementById("runManualBtn");
  if (!btn) return;
  btn.textContent = "Run From Uploaded Stages";
  updateRunManualButtonState();
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
